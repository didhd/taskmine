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
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from taskmine import config as config_mod
from taskmine import emit, forge, report, scan, validate
from taskmine.check import check_task
from taskmine.scan import REFUSALS, Candidate
from taskmine.vcs import Repo


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
    print(f"{len(cands)} change units, {len(ok)} candidates -> {out / 'candidates.jsonl'}")
    by_prof: dict[str, int] = {}
    for c in ok:
        by_prof[c.profile] = by_prof.get(c.profile, 0) + 1
    for k, v in sorted(by_prof.items(), key=lambda x: -x[1]):
        print(f"  candidate  {v:5}  profile {k}")
    for k, v in sorted(counts.items(), key=lambda x: -x[1]):
        print(f"  left out   {v:5}  {k}: {REFUSALS[k]}")
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
    print(f"metadata for {len(got)} of {len(todo)} pull requests -> {meta_dir}")
    return 0


def cmd_build(a) -> int:
    cfg = config_mod.load(a.recipe)
    out = Path(a.out)
    repo = Repo(a.repo)
    cands = _select(_cands(out), a.ids)
    drafts_dir = out / "drafts"
    drafts_dir.mkdir(exist_ok=True)

    def one(c: Candidate):
        mp = out / "meta" / f"{c.id}.json"
        meta = json.loads(mp.read_text()) if mp.exists() else {}
        try:
            emit.build_draft(repo, cfg, c, meta, drafts_dir)
            return c.id, None
        except emit.DraftError as e:
            return c.id, (e.code, e.detail)

    refused = []
    with ThreadPoolExecutor(max_workers=a.jobs) as pool:
        for i, (cid, err) in enumerate(pool.map(one, cands), 1):
            if err:
                refused.append({"id": cid, "code": err[0], "detail": err[1]})
                print(f"refused  {cid}: {err[0]}: {err[1][:120]}")
            elif i % 25 == 0:
                print(f"built {i}/{len(cands)}")
    with open(out / "build-refusals.jsonl", "a" if a.ids else "w") as f:
        for r in refused:
            f.write(json.dumps(r) + "\n")
    print(f"{len(cands) - len(refused)} drafts in {drafts_dir}, {len(refused)} refused")
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
    # Group drafts that share a dependency install, so the first of each group warms Docker's cache.
    fp = {d: json.loads((d / "draft.json").read_text())["deps_fingerprint"] for d in drafts}
    drafts.sort(key=lambda d: (fp[d], d.name))
    if a.executor == "docker":
        ex = validate.DockerExecutor(a.docker)
    else:
        ex = validate.LocalExecutor(Path(a.scratch or (out / "scratch")))
    res = validate.validate_all(cfg, drafts, ex, a.jobs, a.base_runs, a.gold_runs, a.keep_images, out / "validation")
    acc = sum(1 for r in res if r["verdict"] == "accepted")
    print(f"{acc} of {len(res)} drafts accepted; summaries in {out / 'validation'}")
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
    print(f"{sum(1 for r in rows if r['status'] == 'task')} tasks in {tasks}")
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
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p, repo=False):
        p.add_argument("--recipe", required=True, help="the repository recipe, taskmine.toml")
        p.add_argument("--out", required=True, help="the dataset working directory")
        if repo:
            p.add_argument("--repo", required=True, help="a local clone (read only)")

    p = sub.add_parser("scan", help="list change units and candidates")
    common(p, repo=True)
    p.add_argument("--limit", type=int, help="only the newest N change units")
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
    p.add_argument("--executor", choices=["docker", "local"], default="docker")
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


if __name__ == "__main__":
    sys.exit(main())
