"""The everyday commands: login, init, agents, mine, verify, bench, results.

    taskmine login                                   GitHub sign-in, through the GitHub CLI
    taskmine init owner/repo                         clone it (or point at a clone) and write a recipe
    taskmine agents add claude-code --provider bedrock --model us.anthropic.claude-sonnet-5-5
    taskmine mine                                    merged PRs (and the issues they close) -> drafts
    taskmine verify                                  run every draft in Docker; keep what execution proves
    taskmine bench                                   each agent over the tasks, with Harbor
    taskmine results                                 who solved what

Each step's lower-level verbs (scan, meta, build, validate, finalize, check, report) are still
there for scripting; these commands only chain them for a project in `.taskmine/`.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from taskmine import agents as agents_mod
from taskmine import bench as bench_mod
from taskmine import config as config_mod
from taskmine import project as project_mod
from taskmine import report

TTY = sys.stdout.isatty() or os.environ.get("TASKMINE_COLOR") == "1"


def c(code: str, s: str) -> str:
    return f"\033[{code}m{s}\033[0m" if TTY else s


ok = lambda s: print(c("32", "✓ ") + s)  # noqa: E731
info = lambda s: print(c("2", "  " + s))  # noqa: E731


def cmd_login(a) -> int:
    user = project_mod.gh_user()
    if user is None:
        print("Signing in to GitHub with the GitHub CLI...")
        if subprocess.run(["gh", "auth", "login", "--web", "--git-protocol", "https"]).returncode != 0:
            return 1
        user = project_mod.gh_user()
    if user is None:
        print("still not signed in: run `gh auth login`")
        return 1
    ok(f"GitHub: signed in as {c('1', user)} (through gh)")
    return 0


# Read inside the image, from the commit's own pyproject.toml: "x" when it has a [project] table, else
# the first of these names that is an extra (e) or a dependency group (g).
_PY_PICK = ('import sys, tomllib; d = tomllib.load(open("pyproject.toml", "rb")); '
            's = {"p": {"x": 1} if "project" in d else {}, "e": d.get("project", {}).get("optional-dependencies", {}), '
            '"g": d.get("dependency-groups", {})}[sys.argv[1]]; '
            'print(next((k for k in ("x", "tests", "test", "testing", "dev") if k in s), ""))')
_PY_REQUIREMENTS = ("requirements/tests.txt", "requirements/test.txt", "requirements-tests.txt", "requirements-test.txt",
                    "requirements/dev.txt", "requirements-dev.txt")


def _python_install(files: list[str]) -> tuple[list[str], str, str]:
    """(deps, install, env line) for a pytest profile.

    One recipe covers every change, and older ones declare their dependencies differently (a
    dependency group that HEAD has may not exist yet), so the install reads each base's own files
    in the image: pyproject.toml's dependencies with its tests (or test, testing, dev) extra and
    dependency group when they exist, then the first test requirements file and requirements.txt.
    A src/ layout puts src on PYTHONPATH, so the tests import the package without installing it.
    """
    fs = set(files)
    deps = (["pyproject.toml"] if "pyproject.toml" in fs else []) + ["requirements*.txt", "requirements/*.txt"]
    tests_req = " ".join(_PY_REQUIREMENTS)
    install = " && ".join([
        "pip install --no-cache-dir uv",
        "cd /app",
        f"P='{_PY_PICK}'",
        'if [ -f pyproject.toml ]; then if [ -n "$(python3 -c "$P" p)" ]; then E=$(python3 -c "$P" e) && '
        'uv pip install --system -r pyproject.toml ${E:+--extra "$E"} || exit 1; fi; '
        'G=$(python3 -c "$P" g) || exit 1; if [ -n "$G" ]; then uv pip install --system --group "$G" || exit 1; fi; fi',
        f'for r in {tests_req}; do if [ -f "$r" ]; then uv pip install --system -r "$r" || exit 1; break; fi; done',
        'if [ -f requirements.txt ]; then uv pip install --system -r requirements.txt; fi',
        "uv pip install --system pytest",
    ])
    src = any(f.startswith("src/") and f.endswith("/__init__.py") for f in files)
    return deps, install, ('env = { PYTHONPATH = "src" }\n' if src else "")


def _starter_recipe(repo: str, clone: Path) -> str:
    """A first recipe from what the repository holds; review it before mining."""
    files = subprocess.run(["git", "-C", str(clone), "ls-tree", "-r", "--name-only", "HEAD"],
                           capture_output=True, text=True).stdout.splitlines()  # paths may hold spaces
    node_tests = sorted({f.rsplit("/", 1)[0] if "/" in f else "" for f in files if f.endswith((".test.mjs", ".test.js"))})
    py_tests = any(f.split("/")[-1].startswith("test_") and f.endswith(".py") for f in files)
    out = [f'[repo]\nname = "{repo}"\ngithub = "{repo}"\n', '[units]\nkind = "merges"\nbranch = "main"\n']
    for d in node_tests[:4]:
        name = "node-" + (re.sub(r"[^A-Za-z0-9]+", "-", d).strip("-") or "root")
        pre = f"{d}/" if d else ""
        run = json.dumps([f"{pre}*.test.mjs", f"{pre}*.test.js"])
        out.append(
            f'[[profiles]]\nname = "{name}"\nrun = {run}\nimage = "node:24-bookworm"\n'
            f'command = "node --test --test-reporter=junit --test-reporter-destination={{junit}} '
            f'--test-reporter=spec --test-reporter-destination=stdout {{files}}"\ndisplay_command = "node --test {{files}}"\n'
            'text_probe = "node"\n')
    if py_tests:
        deps, install, env = _python_install(files)
        out.append('[[profiles]]\nname = "pytest"\nrun = ["**/test_*.py", "**/*_test.py"]\nimage = "python:3.12-bookworm"\n'
                   f'deps = {json.dumps(deps)}\ninstall = {json.dumps(install)}\n{env}'
                   'command = "python -m pytest -p no:cacheprovider -o junit_family=xunit1 --junitxml={junit} {files}"\n'
                   'display_command = "python -m pytest {files}"\n')
    if len(out) == 2:
        out.append("# No test runner found: add a [[profiles]] entry (see the README).\n")
    return "# Starter recipe written by `taskmine init`. Check the profiles before `taskmine mine`.\n\n" + "\n".join(out)


def tilde(p) -> str:
    s, h = str(p), str(Path.home())
    return "~" + s[len(h):] if s.startswith(h) else s


def _shown(path: Path) -> str:
    """A path as a person reads it: relative under the current directory, else with ~ for home."""
    try:
        return str(path.relative_to(Path.cwd().resolve())) or "."
    except ValueError:
        return tilde(path)


def cmd_init(a) -> int:
    # GitHub is needed to clone and to read pull request metadata; a local clone with a recipe that
    # names no GitHub repository needs neither.
    user = project_mod.gh_user()
    if user is None and not a.clone:
        print("not signed in to GitHub: run `taskmine login` (or point at a local clone with --clone)")
        return 1
    root = project_mod.HOME
    clone = Path(a.clone).resolve() if a.clone else (root / "repo").resolve()
    if not a.clone and not clone.exists():
        print(f"Cloning {a.repo} (blobs over 9 MB stay on GitHub)...")
        p = subprocess.run(["gh", "repo", "clone", a.repo, str(clone), "--", "--no-checkout",
                            "--filter=blob:limit=9m", "--quiet"])
        if p.returncode:
            return p.returncode
    if not (clone / ".git").exists() and not (clone / "HEAD").exists():
        print(f"{clone} is not a git clone")
        return 1
    recipe = Path("taskmine.toml")
    if a.recipe:
        shutil.copyfile(a.recipe, recipe)
    elif not recipe.exists():
        recipe.write_text(_starter_recipe(a.repo, clone))
    cfg = config_mod.load(recipe)
    if cfg.github and user is None:
        print(f"the recipe reads pull requests of {cfg.github} from GitHub: run `taskmine login`")
        return 1
    proj = project_mod.Project(root, a.repo, clone, recipe.resolve(), (root / "out").resolve())
    if (root / "agents.toml").exists():
        proj.agents = project_mod.load(root).agents if (root / "project.toml").exists() else []
    project_mod.save(proj)
    merges = subprocess.run(["git", "-C", str(clone), "rev-list", "--merges", "--count", cfg.branch],
                            capture_output=True, text=True).stdout.strip()
    ok(f"project {c('1', a.repo)}" + (f" (GitHub account {user})" if cfg.github or not a.clone else " (local clone)"))
    info(f"clone    {_shown(clone)}  ({merges} merge commits on {cfg.branch})")
    info(f"recipe   {recipe}  ({len(cfg.profiles)} test runner profiles: {', '.join(p.name for p in cfg.profiles)})")
    info(f"dataset  {os.path.relpath(proj.out)}")
    return 0


def cmd_agents(a) -> int:
    proj = project_mod.load()
    if a.action == "add":
        env = dict(kv.split("=", 1) for kv in (a.env or []))
        e = agents_mod.entry(a.agent, a.provider, a.model, a.name, env)
        proj.agents = [x for x in proj.agents if x["name"] != e["name"]] + [e]
        project_mod.save_agents(proj)
        ok(f"agent {c('1', e['name'])}: {e['agent']} on {e['provider']}, model {e['model']}")
        if (e["agent"], e["provider"]) not in agents_mod.VERIFIED:
            info("this pairing is passed to Harbor as is; taskmine has not run it end to end")
        return 0
    if a.action == "remove":
        before = len(proj.agents)
        proj.agents = [x for x in proj.agents if x["name"] != a.agent]
        project_mod.save_agents(proj)
        return 0 if len(proj.agents) < before else 1
    if not proj.agents:
        print("no agents yet: taskmine agents add claude-code --provider bedrock --model <model>")
    for x in proj.agents:
        print(f"  {x['name']:<36} {x['agent']:<12} {x['provider']:<10} {x['model']}")
    return 0


def _host_ip() -> str:
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))  # no packet is sent; this only picks the outbound interface
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def cmd_gateway(a) -> int:
    import signal
    import time
    from taskmine import gateway
    proj = project_mod.load()
    if a.detach:
        args = [x for x in sys.argv[1:] if x != "--detach"]
        (proj.root / "gateway.toml").unlink(missing_ok=True)
        logf = open(proj.root / "gateway.log", "w")
        subprocess.Popen([sys.executable, "-m", "taskmine", *args], stdout=logf, stderr=subprocess.STDOUT,
                         start_new_session=True)
        for _ in range(240):
            if (proj.root / "gateway.toml").exists():
                break
            time.sleep(0.5)
        time.sleep(1.0)
        print((proj.root / "gateway.log").read_text().rstrip())
        return 0 if (proj.root / "gateway.toml").exists() else 1
    codex = sorted({x["model"] for x in proj.agents
                    if x["agent"] in agents_mod.OPENAI_PROTOCOL and x["provider"] == "bedrock"})
    advertise = a.advertise or _host_ip()
    endpoint = a.endpoint or f"https://bedrock-runtime.{a.region}.amazonaws.com"
    g = gateway.start(proj.root, a.listen, advertise, a.port, a.region, endpoint, codex)
    ok(f"gateway on {advertise}: Bedrock pass-through for Claude Code at {g['bedrock_url']}")
    if codex:
        info(f"OpenAI API for Codex, OpenCode, Strands at {g['openai_url']} ({', '.join(codex)}, via LiteLLM)")
    info(f"upstream {endpoint} ({a.region}); credentials stay on this host")
    sys.stdout.flush()
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    try:
        while True:
            time.sleep(3600)
    except (KeyboardInterrupt, SystemExit):
        if g["litellm"]:
            g["litellm"].terminate()
    return 0


def _ns(**kw) -> argparse.Namespace:
    return argparse.Namespace(**kw)


def cmd_mine(a) -> int:
    from taskmine import cli
    proj = project_mod.load()
    r = str(proj.recipe)
    o = str(proj.out)
    Path(o).mkdir(parents=True, exist_ok=True)
    rc = cli.cmd_scan(_ns(recipe=r, out=o, repo=str(proj.clone), limit=a.limit, brief=True))
    if rc:
        return rc
    cfg = config_mod.load(proj.recipe)
    if cfg.github:
        rc = cli.cmd_meta(_ns(recipe=r, out=o, ids=None, refresh=False))
        if rc:
            return rc
    return cli.cmd_build(_ns(recipe=r, out=o, repo=str(proj.clone), ids=None, jobs=a.jobs))


def cmd_verify(a) -> int:
    from taskmine import cli
    proj = project_mod.load()
    r, o = str(proj.recipe), str(proj.out)
    rc = cli.cmd_validate(_ns(recipe=r, out=o, ids=None, executor=a.env, docker="docker", scratch=None, platform=a.platform,
                              jobs=a.jobs, base_runs=2, gold_runs=3, keep_images=True, skip_done=True))
    if rc:
        return rc
    rc = cli.cmd_finalize(_ns(recipe=r, out=o, validation=None))
    if rc:
        return rc
    rc = cli.cmd_check(_ns(tasks=[str(Path(o) / "tasks")]))
    md, _ = report.build(Path(o))
    (Path(o) / "report.md").write_text(md)
    return rc


BUILTIN_AGENTS = {  # Harbor's reference agents: the gold patch, and doing nothing
    "oracle": {"name": "oracle", "agent": "oracle", "provider": "-", "model": "gold patch"},
    "nop": {"name": "nop", "agent": "nop", "provider": "-", "model": "does nothing"},
}


def _chosen_agents(proj, names: str | None) -> list[dict]:
    if not names:
        return list(proj.agents)
    wanted = names.split(",")
    known = {x["name"]: x for x in proj.agents} | {k: v for k, v in BUILTIN_AGENTS.items()}
    missing = [n for n in wanted if n not in known]
    if missing:
        raise agents_mod.AgentError(f"no agent named {', '.join(missing)} (taskmine agents list)")
    return [known[n] for n in wanted]


def cmd_bench(a) -> int:
    proj = project_mod.load()
    chosen = _chosen_agents(proj, a.agents)
    if not chosen:
        print("no agents: taskmine agents add ...")
        return 1
    run_dir = proj.out / "bench" / a.run
    run_dir.mkdir(parents=True, exist_ok=True)
    tasks = bench_mod.pick_tasks(proj.out / "tasks", a.tasks, a.seed, run_dir)
    n = len([p for p in tasks.iterdir() if (p / "task.toml").exists()]) if tasks.exists() else 0
    if n == 0:
        print("no tasks to benchmark: `taskmine verify` keeps the drafts that execution proves, and none are kept yet")
        return 1
    where = f"AgentCore Runtime ({a.platform}, arm64)" if a.env == "agentcore" else "Docker"
    ok(f"bench {c('1', a.run)}: {len(chosen)} agents x {n} tasks on {where}, {a.concurrency} at a time per agent")
    if a.env == "agentcore":
        from taskmine import agentcore as ac
        ac.check_agents(chosen)
        tasks = ac.prepare(tasks, run_dir / "tasks-agentcore")
        info(f"images: one per task, built on CodeBuild and deployed as an AgentCore runtime ({a.build_jobs} at a time)")
        built = ac.prebuild(proj.root, tasks, a.build_jobs, a.platform, print)
        for r in built:
            if not r["ok"]:
                shutil.rmtree(tasks / r["task"])
        if not any((p / "task.toml").exists() for p in tasks.iterdir()):
            print("no task image could be built")
            return 1
        procs = {x["name"]: ac.start_agent(x, tasks, run_dir, a.concurrency, proj.root, a.platform, print)
                 for x in chosen}
    else:
        procs = {x["name"]: bench_mod.start_agent(x, tasks, run_dir, a.concurrency, a.region, print, proj.root)
                 for x in chosen}
    codes = bench_mod.watch(procs, run_dir, print)
    for name, code in codes.items():
        if code:
            info(f"{name}: harbor exited {code} (log: {run_dir / (name + '.harbor.log')})")
    s = bench_mod.summarize(run_dir, [*proj.agents, *BUILTIN_AGENTS.values()])
    (run_dir / "results.json").write_text(json.dumps(s, indent=1) + "\n")
    print()
    print(bench_mod.table(s))
    return 0


def cmd_results(a) -> int:
    proj = project_mod.load()
    base = proj.out / "bench"
    runs = [p for p in base.iterdir() if (p / "results.json").exists()] if base.exists() else []
    if not runs:
        print("no bench runs yet: taskmine bench")
        return 1
    # The latest run is the one that finished last, not the last name in sort order.
    run = base / a.run if a.run else max(runs, key=lambda p: (p / "results.json").stat().st_mtime)
    s = bench_mod.summarize(run, [*proj.agents, *BUILTIN_AGENTS.values()])
    print(c("1", f"run {run.name}"))
    print(bench_mod.table(s))
    return 0


def register(sub) -> None:
    p = sub.add_parser("login", help="sign in to GitHub (through gh)")
    p.set_defaults(fn=cmd_login)

    p = sub.add_parser("init", help="start a project for one repository")
    p.add_argument("repo", help="owner/name on GitHub")
    p.add_argument("--clone", help="use this local clone instead of cloning")
    p.add_argument("--recipe", help="use this recipe instead of writing a starter one")
    p.set_defaults(fn=cmd_init)

    p = sub.add_parser("agents", help="the coding agents to benchmark")
    p.add_argument("action", choices=["add", "list", "remove"], nargs="?", default="list")
    p.add_argument("agent", nargs="?", help=f"one of: {', '.join(agents_mod.CATALOG)} (or a name, for remove)")
    p.add_argument("--provider", default="bedrock")
    p.add_argument("--model")
    p.add_argument("--name")
    p.add_argument("--env", action="append", help="KEY=VALUE passed to the agent (repeatable)")
    p.set_defaults(fn=cmd_agents)

    p = sub.add_parser("gateway", help="serve models to agent containers without handing them credentials")
    p.add_argument("--listen", default="0.0.0.0")
    p.add_argument("--advertise", help="the address containers use (default: this host's private IP)")
    p.add_argument("--port", type=int, default=8790)
    p.add_argument("--region", default=os.environ.get("AWS_REGION", "us-west-2"))
    p.add_argument("--endpoint", help="Bedrock runtime URL (default: the region's; a VPC may need the -fips one)")
    p.add_argument("--detach", action="store_true", help="run in the background (log: .taskmine/gateway.log)")
    p.set_defaults(fn=cmd_gateway)

    p = sub.add_parser("mine", help="merged pull requests -> draft tasks")
    p.add_argument("--limit", type=int, help="only the newest N change units")
    p.add_argument("--jobs", type=int, default=4)
    p.set_defaults(fn=cmd_mine)

    p = sub.add_parser("verify", help="run every draft (AgentCore or Docker) and keep what execution proves")
    p.add_argument("--jobs", type=int, default=8)
    p.add_argument("--env", choices=["agentcore", "docker"], default="agentcore",
                   help="where validation runs: AgentCore Runtime sessions (default) or this Docker host")
    p.add_argument("--platform", choices=["V1", "V2"], default="V1",
                   help="AgentCore platform version (default: V1; V2 was measured slower here, see AGENTS.md)")
    p.set_defaults(fn=cmd_verify)

    p = sub.add_parser("bench", help="run every agent over the tasks with Harbor")
    p.add_argument("--agents", help="comma-separated agent names (default: all)")
    p.add_argument("--tasks", type=int, help="a seeded sample of N tasks (default: all)")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("-n", "--concurrency", type=int, default=4)
    p.add_argument("--run", default="latest", help="run name (directory under .taskmine/out/bench)")
    p.add_argument("--env", choices=["agentcore", "docker"], default="agentcore",
                   help="where trials run: AgentCore Runtime sessions (default) or this Docker host")
    p.add_argument("--platform", choices=["V1", "V2"], default="V1",
                   help="AgentCore platform version for new runtimes (default: V1, images up to ~2 GB compressed; V2: 10 GB, measured slower here)")
    p.add_argument("--build-jobs", type=int, default=6, help="AgentCore: image builds at a time")
    p.add_argument("--region", default=os.environ.get("AWS_REGION", "us-west-2"), help="Docker: the gateway's region")
    p.set_defaults(fn=cmd_bench)

    p = sub.add_parser("results", help="the latest bench run as a table")
    p.add_argument("--run")
    p.set_defaults(fn=cmd_results)

    p = sub.add_parser("agentcore", help="the AWS side of `bench --env agentcore`: setup, resources, teardown")
    p.add_argument("action", choices=["setup", "resources", "teardown"])
    p.set_defaults(fn=cmd_agentcore)

    p = sub.add_parser("skill", help="install the taskmine agent skill for Claude Code, Codex, Cursor or Kiro")
    p.add_argument("action", choices=["install", "path"])
    p.add_argument("--for", dest="tools", default="claude,codex,cursor,kiro",
                   help="comma-separated: claude, codex, cursor, kiro (default: all four)")
    p.add_argument("--project", action="store_true", help="install into this project's folders instead of your home folder")
    p.add_argument("--dry-run", action="store_true", help="print where it would go, write nothing")
    p.set_defaults(fn=cmd_skill)


# Where each tool reads Agent Skills (SKILL.md folders), per its own docs. Cursor also reads .claude/skills.
SKILL_DIRS = {"claude": ".claude/skills", "codex": ".agents/skills", "cursor": ".agents/skills", "kiro": ".kiro/skills"}
SKILL_SRC = Path(__file__).parent / "skills" / "taskmine"


def cmd_skill(a) -> int:
    if a.action == "path":
        print(SKILL_SRC)
        return 0
    tools = [t.strip() for t in a.tools.split(",") if t.strip()]
    unknown = [t for t in tools if t not in SKILL_DIRS]
    if unknown:
        print(f"unknown tool {', '.join(unknown)}: use {', '.join(SKILL_DIRS)}", file=sys.stderr)
        return 2
    base = Path.cwd() if a.project else Path.home()
    targets: dict[Path, list[str]] = {}
    for t in tools:
        targets.setdefault(base / SKILL_DIRS[t] / "taskmine", []).append(t)
    for dest, who in targets.items():
        shown = _shown(dest)
        if a.dry_run:
            info(f"would write {shown}  ({', '.join(who)})")
            continue
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(SKILL_SRC, dest, ignore=shutil.ignore_patterns("__pycache__"))
        ok(f"skill taskmine -> {shown}  ({', '.join(who)})")
    if not a.dry_run:
        info("restart the agent (or reload its skills) to pick it up; Claude Code can also use the plugin: "
             "/plugin install taskmine --marketplace didhd/taskmine")
    return 0


def cmd_agentcore(a) -> int:
    from taskmine import agentcore as ac
    proj = project_mod.load()
    if a.action == "setup":
        rc = ac.setup(proj.root, proj.agents, info)
        if rc == 0:
            ok(f"AgentCore ready in {ac.REGION}: resources prefixed {ac.PREFIX}; remove them with `taskmine agentcore teardown`")
        return rc
    ac.ensure_venv(proj.root, info)
    if not ac.harness(proj.root).exists():
        ac.install_harness(proj.root, ac.agent_models(proj.agents)[0])
    return ac.resources(proj.root) if a.action == "resources" else ac.teardown(proj.root)
