"""`validate`: execute every draft and keep only what execution proves.

Per draft, in the image the task itself will use:

  base runs   (no solution)       x base_runs   -> statuses per test
  gold runs   (solution applied)  x gold_runs   -> statuses per test

  FAIL_TO_PASS = tests that passed in EVERY gold run and passed in NO base run
  PASS_TO_PASS = tests that passed in every base run and every gold run

A test whose status changed between runs of the same kind is flaky and belongs to neither set.
A draft with no fail-to-pass test is rejected with the reason. Then `tests/expected.json` is
written and the task's own `tests/test.sh` must give reward 0 with no solution (the do-nothing
agent) and 1 with the solution (the oracle), exactly what Harbor's `nop` and `oracle` agents
will see. Only then is the verdict `accepted`.

Runs use `--network none`: a test that needs the network is not a reproducible grader.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tarfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from taskmine.config import Config
from taskmine.emit import draft_digest

NO_TESTS = "(no tests reported)"


class DockerExecutor:
    name = "docker"

    def __init__(self, docker: str = "docker"):
        self.docker = docker

    def build(self, ctx: Path, tag: str, log: Path, timeout: float) -> bool:
        with open(log, "wb") as out:
            try:
                p = subprocess.run(
                    [self.docker, "build", "--progress=plain", "-t", tag, str(ctx)],
                    stdout=out, stderr=subprocess.STDOUT, timeout=timeout,
                )
            except subprocess.TimeoutExpired:
                out.write(b"\ntaskmine: build timed out\n")
                return False
        return p.returncode == 0

    def run(self, tag: str, task: Path, with_solution: bool, logs: Path, prof, name: str) -> dict:
        logs.mkdir(parents=True, exist_ok=True)
        os.chmod(logs, 0o777)
        cmd = [
            self.docker, "run", "--rm", "--name", name, "--network", "none",
            "--cpus", str(prof.cpus), "--memory", f"{prof.memory_mb}m",
            "-v", f"{(task / 'tests').resolve()}:/tests:ro",
            "-v", f"{logs.resolve()}:/logs/verifier",
        ]
        script = "bash /tests/test.sh"
        if with_solution:
            cmd += ["-v", f"{(task / 'solution').resolve()}:/solution:ro"]
            script = "bash /solution/solve.sh > /logs/verifier/solve.log 2>&1 || { echo solve-failed > /logs/verifier/solve-failed; exit 0; }; " + script
        cmd += ["--entrypoint", "bash", tag, "-c", script]
        t0 = time.time()
        with open(logs / "docker.log", "wb") as out:
            try:
                subprocess.run(cmd, stdout=out, stderr=subprocess.STDOUT, timeout=prof.timeout_sec + 300)
            except subprocess.TimeoutExpired:
                subprocess.run([self.docker, "kill", name], capture_output=True)
                out.write(b"\ntaskmine: run timed out and was killed\n")
        return _read_run(logs, time.time() - t0)

    def remove(self, tag: str) -> None:
        subprocess.run([self.docker, "rmi", "-f", tag], capture_output=True)


class LocalExecutor:
    """Runs a task without containers, in a scratch directory standing in for /app.

    For runners with no install step (the tool's own tests use it). It does not isolate anything:
    use the Docker executor for real datasets.
    """

    name = "local"

    def __init__(self, scratch: Path):
        self.scratch = scratch

    def build(self, ctx: Path, tag: str, log: Path, timeout: float) -> bool:
        template = self.scratch / tag.replace("/", "_").replace(":", "_") / "app"
        if template.exists():
            shutil.rmtree(template)
        template.mkdir(parents=True)
        with tarfile.open(ctx / "repo.tar.gz") as tar:
            tar.extractall(template, filter="tar") if hasattr(tarfile, "data_filter") else tar.extractall(template)
        script = (
            "set -e; git init -q; printf 'node_modules/\\n.venv/\\n__pycache__/\\n' >> .git/info/exclude; "
            "git config user.name taskmine; git config user.email taskmine@localhost; git add -A; "
            "GIT_AUTHOR_DATE=2000-01-01T00:00:00Z GIT_COMMITTER_DATE=2000-01-01T00:00:00Z "
            "git commit -q -m base; git tag taskmine-base"
        )
        with open(log, "wb") as out:
            p = subprocess.run(["bash", "-c", script], cwd=template, stdout=out, stderr=subprocess.STDOUT)
        return p.returncode == 0

    def run(self, tag: str, task: Path, with_solution: bool, logs: Path, prof, name: str) -> dict:
        template = self.scratch / tag.replace("/", "_").replace(":", "_") / "app"
        app = self.scratch / "runs" / name / "app"
        if app.parent.exists():
            shutil.rmtree(app.parent)
        shutil.copytree(template, app, symlinks=True)
        logs.mkdir(parents=True, exist_ok=True)
        env = dict(
            os.environ, TASKMINE_APP=str(app), TASKMINE_SOLUTION=str((task / "solution").resolve()),
            TASKMINE_LOGS=str(logs.resolve()),
        )
        t0 = time.time()
        with open(logs / "docker.log", "wb") as out:
            if with_solution:
                p = subprocess.run(["bash", str(task / "solution" / "solve.sh")], env=env, stdout=out, stderr=subprocess.STDOUT)
                if p.returncode != 0:
                    (logs / "solve-failed").write_text("solve-failed\n")
                    return _read_run(logs, time.time() - t0)
            subprocess.run(["bash", str(task / "tests" / "test.sh")], env=env, stdout=out, stderr=subprocess.STDOUT,
                           timeout=prof.timeout_sec + 300)
        shutil.rmtree(app.parent, ignore_errors=True)
        return _read_run(logs, time.time() - t0)

    def remove(self, tag: str) -> None:
        shutil.rmtree(self.scratch / tag.replace("/", "_").replace(":", "_"), ignore_errors=True)


def _read_run(logs: Path, seconds: float) -> dict:
    out = {"seconds": round(seconds, 1), "solve_failed": (logs / "solve-failed").exists()}
    try:
        out["reward"] = float((logs / "reward.txt").read_text().strip())
    except (OSError, ValueError):
        out["reward"] = None
    try:
        res = json.loads((logs / "results.json").read_text())
        out["statuses"] = res.get("statuses", {})
        out["exit_code"] = res.get("exit_code")
        out["timed_out"] = res.get("timed_out")
    except (OSError, ValueError):
        out["statuses"] = {}
    return out


def analyse(base_runs: list[dict], gold_runs: list[dict]) -> dict:
    ids = sorted({t for r in base_runs + gold_runs for t in r["statuses"] if not t.endswith(NO_TESTS)})

    def all_pass(runs, t):
        return all(r["statuses"].get(t) == "passed" for r in runs)

    def none_pass(runs, t):
        return all(r["statuses"].get(t) != "passed" for r in runs)

    f2p = [t for t in ids if all_pass(gold_runs, t) and none_pass(base_runs, t)]
    p2p = [t for t in ids if all_pass(gold_runs, t) and all_pass(base_runs, t)]
    flaky = [
        t for t in ids
        if not (all_pass(gold_runs, t) or none_pass(gold_runs, t))
        or not (all_pass(base_runs, t) or none_pass(base_runs, t))
    ]
    gold_pass = [t for t in ids if all_pass(gold_runs, t)]
    return {"fail_to_pass": f2p, "pass_to_pass": p2p, "flaky": flaky, "tests_seen": len(ids), "gold_passing": len(gold_pass)}


def _summ(kind: str, r: dict) -> dict:
    st = r["statuses"].values()
    return {
        "kind": kind, "reward": r.get("reward"), "seconds": r["seconds"], "exit_code": r.get("exit_code"),
        "timed_out": r.get("timed_out"), "solve_failed": r["solve_failed"],
        "passed": sum(1 for s in st if s == "passed"), "not_passed": sum(1 for s in st if s != "passed"),
    }


def validate_one(cfg: Config, draft: Path, ex, base_runs: int, gold_runs: int, keep_image: bool,
                 build_gate=None) -> dict:
    info = json.loads((draft / "draft.json").read_text())
    cand = info["candidate"]
    prof = cfg.profile(cand["profile"])
    vdir = draft / "validation"
    if vdir.exists():
        shutil.rmtree(vdir)
    vdir.mkdir()
    exp = draft / "tests" / "expected.json"
    if exp.exists():
        exp.unlink()
    digest = draft_digest(draft)
    tag = f"taskmine/{cand['id']}:{digest[:12]}"
    result = {"id": cand["id"], "draft_digest": digest, "executor": ex.name, "image": tag, "runs": []}

    def done(verdict: str, reason: str = "", **extra) -> dict:
        result.update(verdict=verdict, reason=reason, **extra)
        kinds = {}
        for r in result["runs"]:
            kinds[r["kind"]] = kinds.get(r["kind"], 0) + 1
        result["runs_summary"] = ", ".join(f"{k} {v}" for k, v in kinds.items())
        (vdir / "validation.json").write_text(json.dumps(result, indent=1, sort_keys=True) + "\n")
        if verdict != "accepted" or not keep_image:
            ex.remove(tag)
        return result

    t0 = time.time()
    if build_gate is not None:
        build_gate.wait_turn(info["deps_fingerprint"])
    ok = ex.build(draft / "environment", tag, vdir / "build.log", 1800)
    if build_gate is not None:
        build_gate.done(info["deps_fingerprint"])
    result["build_seconds"] = round(time.time() - t0, 1)
    if not ok:
        return done("rejected", "build-failed")

    n = {"i": 0}

    def run(kind: str, with_solution: bool) -> dict:
        n["i"] += 1
        name = f"tm-{cand['id']}-{digest[:8]}-{n['i']}"
        r = ex.run(tag, draft, with_solution, vdir / f"{n['i']:02d}-{kind}", prof, name)
        result["runs"].append(_summ(kind, r))
        return r

    bases = [run("base", False)]
    golds = [run("gold", True)]
    if golds[0]["solve_failed"]:
        return done("rejected", "gold-patch-does-not-apply")
    a = analyse(bases, golds)
    if not a["fail_to_pass"]:
        return done("rejected", _why_no_f2p(a), **a)
    bases += [run("base", False) for _ in range(base_runs - 1)]
    golds += [run("gold", True) for _ in range(gold_runs - 1)]
    a = analyse(bases, golds)
    if not a["fail_to_pass"]:
        return done("rejected", "fail-to-pass-was-flaky", **a)

    exp.write_text(json.dumps({"fail_to_pass": a["fail_to_pass"], "pass_to_pass": a["pass_to_pass"]}, indent=1) + "\n")
    nop = run("nop", False)
    if nop.get("reward") != 0:
        return done("rejected", "nop-not-zero", **a)
    oracle = run("oracle", True)
    if oracle.get("reward") != 1:
        return done("rejected", "oracle-not-one", **a)
    return done("accepted", "", **a)


def _why_no_f2p(a: dict) -> str:
    if a["tests_seen"] == 0:
        return "no-tests-reported"
    if a["gold_passing"] == 0:
        return "tests-fail-with-gold"
    return "no-fail-to-pass"


class BuildGate:
    """The first draft of each dependency fingerprint builds alone; the rest of its group wait,
    then reuse the installed layer from Docker's cache instead of installing in parallel."""

    def __init__(self):
        self._lock = threading.Lock()
        self._events: dict[str, threading.Event] = {}

    def wait_turn(self, fp: str) -> None:
        with self._lock:
            ev = self._events.get(fp)
            if ev is None:
                self._events[fp] = threading.Event()
                return
        ev.wait()

    def done(self, fp: str) -> None:
        with self._lock:
            self._events[fp].set()


def validate_all(cfg: Config, drafts: list[Path], ex, jobs: int, base_runs: int, gold_runs: int,
                 keep_image: bool, summary_dir: Path, log=print) -> list[dict]:
    summary_dir.mkdir(parents=True, exist_ok=True)
    gate = BuildGate()
    results = []
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        futs = {pool.submit(validate_one, cfg, d, ex, base_runs, gold_runs, keep_image, gate): d for d in drafts}
        for f in as_completed(futs):
            d = futs[f]
            try:
                r = f.result()
            except Exception as e:  # a crash in one draft must not stop the others
                r = {"id": d.name, "verdict": "rejected", "reason": "taskmine-error", "detail": repr(e)[:500]}
            results.append(r)
            (summary_dir / f"{r['id']}.json").write_text(json.dumps(r, indent=1, sort_keys=True) + "\n")
            log(f"{r['verdict']:9} {r['id']} {r.get('reason', '')} f2p={len(r.get('fail_to_pass', []))} "
                f"p2p={len(r.get('pass_to_pass', []))} [{len(results)}/{len(drafts)}]")
    return results
