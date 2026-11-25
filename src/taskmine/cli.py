"""taskmine: turn a repository's merged pull requests into Terminal-Bench (Harbor) coding tasks,
each one proven by execution.

    taskmine scan      --recipe R --repo CLONE --out DIR    every change unit, and why it can or cannot be a task
    taskmine meta      --recipe R --out DIR                 pull request titles and closing issues (GitHub, via gh)
    taskmine build     --recipe R --repo CLONE --out DIR    a draft Harbor task per candidate (no Docker needed)
    taskmine validate  --recipe R --out DIR                 run every draft in Docker; keep what execution proves
    taskmine finalize  --recipe R --out DIR                 write instruction.md and task.toml; DIR/tasks/ is the dataset
    taskmine check     TASK_DIR...                          static checks on finished tasks
    taskmine report    --out DIR                            the funnel, with a reason at every step

A task is the repository at the commit before a merged change, an instruction assembled from the
pull request title, the issue it closed and the names of the tests it added, and a verifier that
runs those tests. It is kept only if the tests fail without the change and pass with it, in every
repeated run, and if Harbor's do-nothing agent scores 0 and its oracle scores 1. Run the dataset
with `harbor run -p DIR/tasks -a <agent> -m <model>`.
"""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from taskmine import __version__
from taskmine import config as config_mod
from taskmine import emit, forge, report, scan, validate
from taskmine.check import check_task
from taskmine.scan import REFUSALS, Candidate
from taskmine.vcs import Repo


def _rel(p) -> str:
    """A path as the person typed it: relative to the working directory when it is inside it."""
    import os
    try:
        r = os.path.relpath(p)
        return str(p) if r.startswith("..") else r
    except ValueError:
        return str(p)


def _cands(out: Path) -> list[Candidate]:
    return [Candidate.from_json(json.loads(l)) for l in (out / "candidates.jsonl").read_text().splitlines() if l.strip()]


def _select(cands: list[Candidate], ids: list[str] | None) -> list[Candidate]:
    usable = [c for c in cands if not c.refusal]
    if ids:
        want = set(ids)
        usable = [c for c in usable if c.id in want or c.unit in want]
    return usable


def cmd_scan(a) -> int:
    cfg = config_mod.load(a.recipe)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    cands = scan.scan(Repo(a.repo), cfg, limit=a.limit)
    with open(out / "candidates.jsonl", "w") as f:
        for c in cands:
            f.write(json.dumps(c.to_json(), sort_keys=True) + "\n")
    ok = [c for c in cands if not c.refusal]
    counts: dict[str, int] = {}
    for c in cands:
        if c.refusal:
            counts[c.refusal] = counts.get(c.refusal, 0) + 1
    print(f"{len(cands)} change units, {len(ok)} candidates -> {_rel(out / 'candidates.jsonl')}")
    by_prof: dict[str, int] = {}
    for c in ok:
        by_prof[c.profile] = by_prof.get(c.profile, 0) + 1
    for k, v in sorted(by_prof.items(), key=lambda x: -x[1]):
        print(f"  candidate  {v:5}  profile {k}")
    for k, v in sorted(counts.items(), key=lambda x: -x[1]):
        print(f"  left out   {v:5}  {k}" + ("" if a.brief else f": {REFUSALS[k]}"))
    return 0


def cmd_meta(a) -> int:
    cfg = config_mod.load(a.recipe)
    if not cfg.github:
        print("the recipe has no [repo] github, so there is nothing to fetch", file=sys.stderr)
        return 2
    out = Path(a.out)
    cands = [c for c in _select(_cands(out), a.ids) if c.pr is not None]
    meta_dir = out / "meta"
    meta_dir.mkdir(exist_ok=True)
    todo = [c for c in cands if a.refresh or not (meta_dir / f"{c.id}.json").exists()]
    got = forge.fetch(cfg.github, [c.pr for c in todo])
    for c in todo:
        if c.pr in got:
            (meta_dir / f"{c.id}.json").write_text(json.dumps(got[c.pr], indent=1, sort_keys=True) + "\n")
    issues = sum(len(v.get("issues") or []) for v in got.values())
    print(f"metadata for {len(got)} of {len(todo)} pull requests ({issues} closing issues) -> {_rel(meta_dir)}")
    return 0


def _build_one(job: tuple[str, str, str, dict]) -> tuple[str, tuple[str, str] | None]:
    """One draft, in its own process: snapshot building is CPU-bound Python (tar, regex)."""
    recipe, repo_path, out, cand_json = job
    cfg = config_mod.load(recipe)
    c = Candidate.from_json(cand_json)
    mp = Path(out) / "meta" / f"{c.id}.json"
    meta = json.loads(mp.read_text()) if mp.exists() else {}
    try:
        emit.build_draft(Repo(repo_path), cfg, c, meta, Path(out) / "drafts")
        return c.id, None
    except emit.DraftError as e:
        return c.id, (e.code, e.detail)
    except Exception as e:  # one broken candidate must not stop the others
        return c.id, ("taskmine-error", repr(e)[:500])


def cmd_build(a) -> int:
    config_mod.load(a.recipe)
    out = Path(a.out)
    cands = _select(_cands(out), a.ids)
    (out / "drafts").mkdir(exist_ok=True)
    jobs = [(a.recipe, a.repo, str(out), c.to_json()) for c in cands]
    refused = []
    with ProcessPoolExecutor(max_workers=a.jobs) as pool:
        for i, (cid, err) in enumerate(pool.map(_build_one, jobs), 1):
            if err:
                refused.append({"id": cid, "code": err[0], "detail": err[1]})
                print(f"refused  {cid}: {err[0]}: {err[1][:120]}", flush=True)
            elif i % 25 == 0:
                print(f"built {i}/{len(cands)}", flush=True)
    with open(out / "build-refusals.jsonl", "a" if a.ids else "w") as f:
        for r in refused:
            f.write(json.dumps(r) + "\n")
    print(f"{len(cands) - len(refused)} drafts in {_rel(out / 'drafts')}, {len(refused)} refused")
    return 0


def cmd_validate(a) -> int:
    cfg = config_mod.load(a.recipe)
    out = Path(a.out)
    drafts = sorted(p for p in (out / "drafts").iterdir() if (p / "draft.json").exists())
    if a.ids:
        want = set(a.ids)
        drafts = [d for d in drafts if d.name in want]
    if a.skip_done:
        drafts = [d for d in drafts if not (out / "validation" / f"{d.name}.json").exists()]
    # Interleave the dependency groups. The first draft of each group installs while the rest of that
    # group waits for Docker's cache (BuildGate); sorted by group, every worker waited behind one
    # install (measured: 75% of a 32-vCPU box idle). Round-robin keeps workers on different groups.
    fp = {d: json.loads((d / "draft.json").read_text())["deps_fingerprint"] for d in drafts}
    groups: dict[str, list[Path]] = {}
    for d in drafts:
        groups.setdefault(fp[d], []).append(d)
    order = sorted(groups.values(), key=len, reverse=True)
    width = max((len(g) for g in order), default=0)
    drafts = [g[i] for i in range(width) for g in order if i < len(g)]
    if a.executor == "docker":
        ex = validate.DockerExecutor(a.docker)
    elif a.executor == "agentcore":
        from taskmine import agentcore
        ex = agentcore.AgentCoreExecutor(Path(".taskmine"), getattr(a, "platform", "V1"))
    else:
        ex = validate.LocalExecutor(Path(a.scratch or (out / "scratch")))
    res = validate.validate_all(cfg, drafts, ex, a.jobs, a.base_runs, a.gold_runs, a.keep_images, out / "validation")
    acc = sum(1 for r in res if r["verdict"] == "accepted")
    print(f"{acc} of {len(res)} drafts accepted; summaries in {_rel(out / 'validation')}")
    return 0


def cmd_finalize(a) -> int:
    cfg = config_mod.load(a.recipe)
    out = Path(a.out)
    vdir = Path(a.validation) if a.validation else out / "validation"
    tasks = out / "tasks"
    tasks.mkdir(exist_ok=True)
    rows = []
    for vp in sorted(vdir.glob("*.json")):
        v = json.loads(vp.read_text())
        if v.get("verdict") != "accepted":
            continue
        draft = out / "drafts" / v["id"]
        if not draft.exists():
            rows.append({"id": v["id"], "status": "refused", "code": "no-draft"})
            continue
        try:
            r = emit.finalize(cfg, draft, v, tasks)
            rows.append({"status": "task", **r})
        except emit.DraftError as e:
            rows.append({"id": v["id"], "status": "refused", "code": e.code, "detail": e.detail})
            print(f"refused  {v['id']}: {e.code}: {e.detail[:120]}")
    with open(out / "finalize.jsonl", "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    print(f"{sum(1 for r in rows if r['status'] == 'task')} tasks in {_rel(tasks)}")
    return 0


def cmd_check(a) -> int:
    bad = 0
    paths = []
    for p in a.tasks:
        p = Path(p)
        paths += sorted(x.parent for x in p.glob("*/task.toml")) if not (p / "task.toml").exists() else [p]
    for t in paths:
        problems = check_task(t)
        if problems:
            bad += 1
            print(f"FAIL {t.name}")
            for pr in problems:
                print(f"     {pr}")
    print(f"{len(paths) - bad} of {len(paths)} tasks pass the static checks")
    return 1 if bad else 0


def cmd_report(a) -> int:
    out = Path(a.out)
    md, data = report.build(out)
    (out / "report.md").write_text(md)
    (out / "report.json").write_text(json.dumps(data, indent=1, sort_keys=True) + "\n")
    print(md)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="taskmine", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", action="version", version=f"taskmine {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    from taskmine import workflow
    workflow.register(sub)

    def common(p, repo=False):
        p.add_argument("--recipe", required=True, help="the repository recipe, taskmine.toml")
        p.add_argument("--out", required=True, help="the dataset working directory")
        if repo:
            p.add_argument("--repo", required=True, help="a local clone (read only)")

    p = sub.add_parser("scan", help="list change units and candidates")
    common(p, repo=True)
    p.add_argument("--limit", type=int, help="only the newest N change units")
    p.add_argument("--brief", action="store_true", help="refusal codes only, without their explanations")
    p.set_defaults(fn=cmd_scan)

    p = sub.add_parser("meta", help="fetch pull request titles and closing issues from GitHub")
    common(p)
    p.add_argument("--ids", nargs="*")
    p.add_argument("--refresh", action="store_true")
    p.set_defaults(fn=cmd_meta)

    p = sub.add_parser("build", help="build a draft task per candidate")
    common(p, repo=True)
    p.add_argument("--ids", nargs="*")
    p.add_argument("--jobs", type=int, default=2)
    p.set_defaults(fn=cmd_build)

    p = sub.add_parser("validate", help="execute drafts and decide fail-to-pass / pass-to-pass")
    common(p)
    p.add_argument("--ids", nargs="*")
    p.add_argument("--executor", choices=["docker", "agentcore", "local"], default="docker")
    p.add_argument("--platform", choices=["V1", "V2"], default="V1", help="agentcore executor: platform version")
    p.add_argument("--docker", default="docker")
    p.add_argument("--scratch", help="local executor scratch directory")
    p.add_argument("--jobs", type=int, default=4)
    p.add_argument("--base-runs", type=int, default=2)
    p.add_argument("--gold-runs", type=int, default=3)
    p.add_argument("--keep-images", action="store_true", help="keep accepted images (Harbor then reuses the cache)")
    p.add_argument("--skip-done", action="store_true", help="skip drafts that already have a validation summary")
    p.set_defaults(fn=cmd_validate)

    p = sub.add_parser("finalize", help="write the finished tasks")
    common(p)
    p.add_argument("--validation", help="directory of validation summaries (default DIR/validation)")
    p.set_defaults(fn=cmd_finalize)

    p = sub.add_parser("check", help="static checks on finished tasks")
    p.add_argument("tasks", nargs="+", help="task directories, or a directory of them")
    p.set_defaults(fn=cmd_check)

    p = sub.add_parser("report", help="the dataset funnel")
    p.add_argument("--out", required=True)
    p.set_defaults(fn=cmd_report)

    a = ap.parse_args(argv)
    try:
        return a.fn(a)
    except config_mod.ConfigError as e:
        print(f"recipe error: {e}", file=sys.stderr)
        return 2
    except Exception as e:  # the everyday commands' own errors read as one line
        from taskmine.agents import AgentError
        from taskmine.project import ProjectError
        if isinstance(e, (AgentError, ProjectError)):
            print(f"taskmine: {e}", file=sys.stderr)
            return 2
        raise


if __name__ == "__main__":
    sys.exit(main())
