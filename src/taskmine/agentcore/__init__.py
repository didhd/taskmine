"""Run taskmine's tasks on Amazon Bedrock AgentCore Runtime instead of a Docker host.

    taskmine agentcore setup        a Python 3.12 venv with Harbor, then the AWS resources (once)
    taskmine bench --env agentcore  every agent over the tasks, each trial in its own AgentCore session
    taskmine agentcore resources    what exists in the account under the prefix
    taskmine agentcore teardown     delete all of it

The AgentCore side is the Harbor environment from aws-samples/sample-gen-ai-evaluations-workshop
pull request #120, vendored unchanged in pr120/ (see PROVENANCE.md). Each task image is built on
CodeBuild (arm64) and deployed as one AgentCore runtime. Each trial runs in a fresh microVM session:
- the agent runs as an unprivileged user in an empty network namespace;
- its only way out is an allowlist proxy that reaches the Bedrock endpoints;
- its AWS credentials are short-lived and can call only the agent models named at setup.

This module runs with the standard library only. It drives the harness venv through subprocesses
and turns taskmine tasks into AgentCore-ready copies.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
import tomllib
from pathlib import Path

from taskmine import tomlw
from taskmine.agents import AgentError

HERE = Path(__file__).resolve().parent
REGION = "us-east-1"           # PR #120's region; its clients and endpoints are fixed to it
PREFIX = "evals-tm-"           # every AWS resource name starts with this (teardown refuses other prefixes)
HARBOR = "harbor==0.27.0"
BOTO3 = "boto3>=1.44.7"        # the first boto3 whose AgentCore API takes platformVersion
MODEL_HOSTS = ("bedrock-runtime.us-east-1.amazonaws.com", "bedrock-mantle.us-east-1.api.aws")
ENV_IMPORT = "taskmine_env:TaskmineAgentCoreEnvironment"

# Harnesses baked into each image: the agent's network reaches only Bedrock, so Harbor cannot
# install them at trial time. OpenCode comes from PR #120's layer (opencode-ai 1.19.6).
CLAUDE_CODE = "@anthropic-ai/claude-code@2.1.327"
CODEX = "@openai/codex@0.162.1"
STRANDS = "strands-agents==1.54.0"
# PR #120's staged grader works on /work/repo: its layer gives the agent /work/repo, keeps a root-only
# copy of its .git, and records the tree as built. /app, where the instruction and the task's
# commands put the repository, is a root-owned link to it, made before anything is written there.
STAGED_LAYOUT = """# ===== taskmine: the repository lives at /work/repo for PR #120's staged grader; /app links to it =====
RUN mkdir -p /work/repo && rm -rf /app && ln -s /work/repo /app
"""
VARIANT = f"""
# ===== taskmine: AgentCore variant (taskmine bench --env agentcore) =====
# PR #120's layer below makes the agent user (uid 2000) and gives it /work/repo. The staged grader
# reads the base commit from /var/lib/task_base_ref.
USER root
RUN git -C /work/repo rev-parse --verify -q taskmine-base^{{commit}} > /var/lib/task_base_ref \\
 && chmod 0644 /var/lib/task_base_ref
# A session's /etc/hosts is empty and read-only, and the agent's namespace has no DNS, so `localhost`
# would not resolve (vitest, and every test that starts a local server, fail). glibc's myhostname
# module answers localhost with 127.0.0.1 and ::1 without either.
RUN if command -v apt-get >/dev/null; then ls /lib/*/libnss_myhostname.so.2 >/dev/null 2>&1 \\
      || (apt-get update && apt-get install -y --no-install-recommends libnss-myhostname && rm -rf /var/lib/apt/lists/*); fi \\
 && sed -i 's/^hosts:.*/hosts:          files myhostname dns/' /etc/nsswitch.conf
# Harnesses are baked in: at trial time the agent's network reaches only Bedrock.
RUN command -v npm >/dev/null 2>&1 || (apt-get update && apt-get install -y --no-install-recommends nodejs npm \\
 && rm -rf /var/lib/apt/lists/*)
RUN npm install -g {CLAUDE_CODE} {CODEX} && npm cache clean --force && rm -rf /root/.npm /tmp/* \\
 && claude --version && codex --version
# Strands Agents, in the venv that Harbor's strands runner uses.
COPY --from=ghcr.io/astral-sh/uv:0.13.0 /uv /usr/local/bin/uv
# uv uses the image's own Python 3.12 when it has one, and installs one in /opt/uv-python when not.
# Bytecode is compiled now, so the runner's imports don't compile in every session.
RUN mkdir -p /opt/uv-python && UV_PYTHON_INSTALL_DIR=/opt/uv-python uv venv -q --python 3.12 /installed-agent/strands/venv \\
 && UV_COMPILE_BYTECODE=1 uv pip install -q --python /installed-agent/strands/venv/bin/python {STRANDS} && rm -rf /root/.cache/uv \\
 && chown -R 2000:2000 /installed-agent/strands && chmod -R a+rX /opt/uv-python
"""


class AgentCoreError(AgentError):
    pass


# ------------------------------------------------------------------ the harness venv
def home(root) -> Path:
    return Path(root) / "agentcore"


def python(root) -> Path:
    return home(root) / "venv" / "bin" / "python"


def harness(root) -> Path:
    return home(root) / "harness"


def ensure_venv(root, log=print) -> Path:
    py = python(root)
    ok = py.exists() and subprocess.run(
        [str(py), "-c", "import boto3, harbor, sys; v = tuple(int(x) for x in boto3.__version__.split('.')[:3]); "
                        "sys.exit(v < (1, 44, 7))"], capture_output=True).returncode == 0
    if ok:
        return py
    uv = shutil.which("uv")
    if not uv:
        raise AgentCoreError("the AgentCore harness needs uv to make its Python 3.12 venv: https://docs.astral.sh/uv/")
    log(f"making {home(root) / 'venv'} ({HARBOR}, {BOTO3})")
    for cmd in ([uv, "venv", "-q", "--allow-existing", "--python", "3.12", str(home(root) / "venv")],
                [uv, "pip", "install", "-q", "--python", str(py), HARBOR, BOTO3]):
        if subprocess.run(cmd).returncode:
            raise AgentCoreError(f"failed: {' '.join(cmd)}")
    return py


def install_harness(root, codex_model: str | None = None) -> Path:
    """A working copy of pr120/ plus taskmine's layer; the harness writes reports next to its own files.
    With codex_model, also the model config that setup's bootstrap reads (it shapes the IAM policies)."""
    dest = harness(root)
    for src in sorted((HERE / "pr120").rglob("*")):
        if src.is_file() and "__pycache__" not in src.parts:
            out = dest / src.relative_to(HERE / "pr120")
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, out)
    for n in ("taskmine_env.py", "taskmine_ac.py", "taskmine_warmup.py"):
        shutil.copy2(HERE / n, dest / n)
    if codex_model is None:
        return dest
    module = home(root) / "module"  # PR #120 reads model IDs from <module>/model_config.py and wants datasets/
    (module / "datasets").mkdir(parents=True, exist_ok=True)
    (module / "model_config.py").write_text(
        "# Written by taskmine agentcore setup. PR #120 reads the model IDs from here.\n"
        f"CODEX_MODEL_ID = {codex_model!r}\n"
        "JUDGE_MODEL_ID = 'us.anthropic.claude-haiku-5-5'      # unused by taskmine tasks (no LLM judge)\n"
        "VERIFIER_MODEL_ID = 'us.anthropic.claude-haiku-5-5'   # unused by taskmine tasks (no LLM judge)\n")
    return dest


def run_env(root, platform: str = "V1") -> dict[str, str]:
    """The harness environment. An AWS_BEARER_TOKEN_BEDROCK in the shell makes PR #120 build wrong policies, so it is dropped."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("AWS_BEARER_TOKEN")}
    env.update(PYTHONPATH=str(harness(root).resolve()), P2_PREFIX=PREFIX, P2_MODULE=str((home(root) / "module").resolve()),
               AWS_REGION=REGION, AWS_DEFAULT_REGION=REGION, TASKMINE_AC_PLATFORM=platform)
    return env


def harbor(root) -> str:
    return str(home(root) / "venv" / "bin" / "harbor")


# ------------------------------------------------------------------ models
def codex_model(model: str) -> str:
    """Codex reaches GPT models on bedrock-mantle, which takes the bare model ID, not a `us.` inference profile."""
    return model.split(".", 1)[1] if model.startswith(("us.", "eu.", "apac.", "global.")) else model


def agent_models(agents: list[dict]) -> tuple[str, list[str]]:
    """(the one Codex model, the other agents' models): what the agent role may call."""
    codex = sorted({codex_model(a["model"]) for a in agents if a["agent"] == "codex"})
    if len(codex) > 1:
        raise AgentCoreError(f"PR #120 allows one Codex model per setup, these agents name {len(codex)}: {', '.join(codex)}")
    others = sorted({a["model"] for a in agents if a["agent"] not in ("codex", "oracle", "nop")})
    return (codex[0] if codex else "openai.gpt-6.1-terra"), others


def check_agents(agents: list[dict]) -> None:
    for a in agents:
        if a["agent"] in ("oracle", "nop"):
            continue
        if a["agent"] not in ("claude-code", "codex", "opencode", "strands"):
            raise AgentCoreError(f"{a['name']}: {a['agent']} is not baked into AgentCore images "
                                 "(claude-code, codex, opencode, strands are)")
        if a["provider"] != "bedrock":
            raise AgentCoreError(f"{a['name']}: on AgentCore an agent reaches models through Bedrock only")


# ------------------------------------------------------------------ setup.py of PR #120
def _setup(root, *args: str, platform: str = "V1") -> int:
    py = python(root)
    return subprocess.run([str(py), str(harness(root) / "harbor_env" / "setup.py"), *args],
                          env=run_env(root, platform)).returncode


def setup(root, agents: list[dict], log=print) -> int:
    check_agents(agents)
    codex, others = agent_models(agents)
    ensure_venv(root, log)
    install_harness(root, codex)
    log(f"agent role may call: {', '.join([codex, *others])} (region {REGION}, prefix {PREFIX})")
    # PR #120's bootstrap logs one line per resource, then prints its whole report as JSON: show the
    # lines, keep the report in a file.
    p = subprocess.Popen([str(python(root)), str(harness(root) / "harbor_env" / "setup.py"), "bootstrap",
                          *[x for m in others for x in ("--agent-model", m)]],
                         env=run_env(root), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    report = []
    for line in p.stdout:
        if re.match(r"^\d\d:\d\d:\d\d ", line):
            log(line.rstrip()[9:].strip())
        else:
            report.append(line)
    (home(root) / "bootstrap.json").write_text("".join(report))
    return p.wait()


def resources(root) -> int:
    return _setup(root, "resources")


def teardown(root) -> int:
    return _setup(root, "teardown")


def prebuild(root, tasks: Path, jobs: int, platform: str, log=print) -> list[dict]:
    install_harness(root)  # this taskmine's copy of the harness files, never a stale one
    dirs = sorted(str(p) for p in tasks.iterdir() if (p / "task.toml").exists())
    p = subprocess.Popen([str(python(root)), str(harness(root) / "taskmine_ac.py"), "prebuild", "--jobs", str(jobs), *dirs],
                         env=run_env(root, platform), stdout=subprocess.PIPE, text=True)
    out = []
    for line in p.stdout:
        try:
            r = json.loads(line)
        except ValueError:
            continue
        out.append(r)
        what = ("built " if r.get("built") else "") + ("deployed" if r.get("deployed") else "") or "ready"
        log(f"  image {r['task']:<16} {what if r['ok'] else 'FAILED'}  {r['secs']:.0f} s" +
            ("" if r["ok"] else f"\n    {r['error'][:600]}"))
    p.wait()
    return out


# ------------------------------------------------------------------ tasks
_LIBRARY = re.compile(r"^(?:docker\.io/)?(?:library/)?([a-z0-9][a-z0-9._-]*(?::[\w.-]+)?(?:@sha256:[0-9a-f]{64})?)$")


def base_image(ref: str) -> str:
    """A Docker Hub official image, from AWS's mirror of it (same digests): CodeBuild's shared IPs hit Docker Hub's pull limit."""
    m = _LIBRARY.match(ref)
    return f"public.ecr.aws/docker/library/{m.group(1)}" if m and "/" not in m.group(1) else ref


def dockerfile(text: str) -> str:
    """The AgentCore copy of a task's Dockerfile: base images from the mirror, the repository at
    /work/repo behind /app, and the variant (harnesses, localhost, the base ref) at the end."""
    out, linked = [], False
    for line in text.splitlines():
        if not linked and re.match(r"^WORKDIR\s+/app/?\s*$", line):
            out.extend(STAGED_LAYOUT.rstrip("\n").splitlines())
            linked = True
        m = re.match(r"^(FROM\s+)(\S+)(.*)$", line)
        out.append(m.group(1) + base_image(m.group(2)) + m.group(3) if m else line)
    if not linked:
        raise AgentCoreError("this Dockerfile has no `WORKDIR /app`, so its repository cannot be put where PR #120's "
                             "staged grader looks (/work/repo); taskmine writes that line in every task it makes")
    return "\n".join(out) + "\n" + VARIANT


def task_toml(text: str) -> str:
    """Agent: only the Bedrock endpoints (PR #120 refuses an open network). Grader: no network at all."""
    doc = tomllib.loads(text)
    env = doc.setdefault("environment", {})
    env["network_mode"] = "allowlist"
    env["allowed_hosts"] = list(MODEL_HOSTS)
    doc.setdefault("verifier", {})["network_mode"] = "no-network"
    return tomlw.dumps(doc)


def prepare(tasks: Path, dest: Path) -> Path:
    """AgentCore copies of the tasks: Dockerfile and task.toml rewritten, everything else hard-linked."""
    if dest.exists():
        shutil.rmtree(dest)
    for t in sorted(p for p in tasks.iterdir() if (p / "task.toml").exists()):
        rewritten = {(t, "task.toml"), (t / "environment", "Dockerfile")}
        shutil.copytree(t, dest / t.name, copy_function=os.link,
                        ignore=lambda d, names, rewritten=rewritten: [n for n in names if (Path(d), n) in rewritten])
        (dest / t.name / "task.toml").write_text(task_toml((t / "task.toml").read_text()))
        (dest / t.name / "environment" / "Dockerfile").write_text(dockerfile((t / "environment" / "Dockerfile").read_text()))
    return dest


# ------------------------------------------------------------------ validation (taskmine verify --executor agentcore)
class AgentCoreExecutor:
    """validate.py's executor interface on AgentCore: one image per draft, one fresh session per run.

    A run uploads tests/ (and solution/ for gold runs), then runs test.sh as the agent user with no
    network and no credentials, the way the Docker executor uses `--network none`. The verifier
    logs come back checked against their sha256.
    """

    name = "agentcore"
    shares_build_cache = False  # each image builds on its own CodeBuild host: nothing to wait for
    parallel_runs = True        # every run is its own microVM session, so the repeat runs go at once

    def __init__(self, root, platform: str = "V1"):
        self.root, self.platform = Path(root), platform
        self.keys: dict[str, str] = {}
        if not python(self.root).exists():
            raise AgentCoreError("no AgentCore harness here: run `taskmine agentcore setup` first")
        install_harness(self.root)

    def _helper(self, *args: str, timeout: float | None = None, stdout=subprocess.PIPE, stderr=None):
        return subprocess.run([str(python(self.root)), str(harness(self.root) / "taskmine_ac.py"), *args],
                              env=run_env(self.root, self.platform), stdout=stdout, stderr=stderr, text=True, timeout=timeout)

    def build(self, ctx: Path, tag: str, log: Path, timeout: float) -> bool:
        env_dir = self.root / "agentcore" / "envs" / ctx.parent.name / "environment"
        if env_dir.parent.exists():
            shutil.rmtree(env_dir.parent)
        shutil.copytree(ctx, env_dir, copy_function=os.link,
                        ignore=lambda d, names: ["Dockerfile"] if Path(d) == ctx else [])
        (env_dir / "Dockerfile").write_text(dockerfile((ctx / "Dockerfile").read_text()))
        with open(log, "w") as err:
            try:
                p = self._helper("build", str(env_dir), ctx.parent.name, timeout=timeout + 600, stderr=err)
            except subprocess.TimeoutExpired:
                err.write("\ntaskmine: build timed out\n")
                return False
        r = next((json.loads(x) for x in p.stdout.splitlines() if x.startswith("{")), {"ok": False, "error": "no answer"})
        with open(log, "a") as f:
            f.write(json.dumps(r, indent=1) + "\n")
        if r.get("tag"):
            self.keys[tag] = r["tag"]
        return bool(r["ok"])

    def run(self, tag: str, task: Path, with_solution: bool, logs: Path, prof, name: str,
            probe_globs: list[str] | None = None) -> dict:
        from taskmine import validate
        logs.mkdir(parents=True, exist_ok=True)
        args = ["run", "--tag", self.keys[tag], "--tests", str((task / "tests").resolve()), "--out", str(logs.resolve()),
                "--timeout", str(int(prof.timeout_sec + 300))]
        if with_solution:
            args += ["--solution", str((task / "solution").resolve())]
        if probe_globs is not None:
            args += ["--probe", str(validate.PROBE.resolve())]
            env = validate._probe_env("/app", "/taskmine-probe/" + validate.PROBE.name, "/logs/verifier", probe_globs)
            args += [x for k, v in env.items() for x in ("--env", f"{k}={v}")]
        t0 = time.time()
        try:
            self._helper(*args, timeout=prof.timeout_sec + 900, stdout=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            with open(logs / "docker.log", "a") as f:
                f.write("\ntaskmine: run timed out\n")
        return validate._read_run(logs, time.time() - t0)

    def remove(self, tag: str) -> None:
        if tag in self.keys:
            self._helper("remove", self.keys.pop(tag), stdout=subprocess.DEVNULL)


# ------------------------------------------------------------------ Harbor flags
def harbor_args(a: dict, root) -> list[str]:
    """PR #120's tested flags for each harness on AgentCore. Credentials never go through Harbor: the
    environment drops every AWS credential variable and gives the agent its own scoped ones."""
    kind, model = a["agent"], a.get("model") or ""
    region = ["--ae", f"AWS_REGION={REGION}", "--ae", f"AWS_DEFAULT_REGION={REGION}"]
    extra = [x for k, v in (a.get("env") or {}).items() for x in ("--ae", f"{k}={v}")]
    if kind in ("oracle", "nop"):
        return ["-a", kind]
    skip = ["--ak", "skip_install=true"]
    if kind == "claude-code":
        return ["-a", "claude-code", "-m", model, "--skip-model-parsing-and-provider-mapping", *skip, *region,
                "--ae", "CLAUDE_CODE_USE_BEDROCK=1", "--ae", f"ANTHROPIC_DEFAULT_HAIKU_MODEL={model}",
                "--ae", f"ANTHROPIC_SMALL_FAST_MODEL={model}", "--ae", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1",
                "--ae", "DISABLE_AUTOUPDATER=1", *extra]
    if kind == "codex":
        cfg = (harness(root) / "codex-bedrock.toml").resolve()
        return ["-a", "codex", "-m", codex_model(model), "--skip-model-parsing-and-provider-mapping", *skip,
                "--ak", f"config={cfg}", "--ak", "disable_web_search=true", *region, *extra]
    if kind == "opencode":
        cfg = {"small_model": f"amazon-bedrock/{model}", "autoupdate": False, "share": "disabled"}
        quiet = [x for k in ("MODELS_FETCH", "AUTOUPDATE", "LSP_DOWNLOAD", "DEFAULT_PLUGINS", "CLAUDE_CODE")
                 for x in ("--ae", f"OPENCODE_DISABLE_{k}=1")]
        return ["-a", "opencode", "-m", f"amazon-bedrock/{model}", *skip, *region, *quiet,
                "--ak", "opencode_config=" + json.dumps(cfg, separators=(",", ":")), *extra]
    if kind == "strands":
        from taskmine.agents import STRANDS_FACTORY
        # Harbor cannot see the credentials, which exist only inside the session, so the factory builds the Bedrock model.
        kw = json.dumps({"model": model}, separators=(",", ":"))
        return ["-a", "strands", "-m", f"amazon-bedrock/{model}", *STRANDS_FACTORY, "--ak", f"factory_kwargs={kw}",
                *skip, *region, *extra]
    raise AgentCoreError(f"{a['name']}: {kind} is not supported on AgentCore")


def start_agent(a: dict, tasks: Path, jobs_dir: Path, concurrency: int, root, platform: str, log):
    cmd = [harbor(root), "run", "-y", "-p", str(tasks), "-e", ENV_IMPORT, *harbor_args(a, root),
           "-n", str(concurrency), "-o", str(jobs_dir), "--job-name", a["name"]]
    log(f"  $ harbor run -p {os.path.relpath(tasks)} -e {ENV_IMPORT} -a {a['agent']} ... --job-name {a['name']}")
    out = open(jobs_dir / f"{a['name']}.harbor.log", "w")
    return subprocess.Popen(cmd, env=run_env(root, platform), stdout=out, stderr=subprocess.STDOUT)
