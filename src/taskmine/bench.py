"""`bench`: run every configured agent over the task set with Harbor, and `results`: the table.

Each agent gets its own Harbor job, `<out>/bench/<run>/<agent name>/`, over the same tasks. A
task counts as solved when Harbor's verifier wrote reward 1.0 (the task's own grader: every
fail-to-pass and pass-to-pass test passed). A trial that ended in an exception (the agent could not
install, the model refused the credentials, a timeout) is reported as an error, not as unsolved,
so infrastructure problems are visible instead of lowering a score.
"""

from __future__ import annotations

import json
import os
import random
import shutil
import subprocess
import time
from pathlib import Path

from taskmine import agents as agents_mod


def pick_tasks(tasks_dir: Path, n: int | None, seed: int, out: Path) -> Path:
    """All tasks, or a seeded sample of n hard-linked into a subset directory Harbor can run."""
    names = sorted(p.name for p in tasks_dir.iterdir() if (p / "task.toml").exists())
    if not n or n >= len(names):
        return tasks_dir
    chosen = sorted(random.Random(seed).sample(names, n))
    sub = out / "tasks"
    if sub.exists():
        shutil.rmtree(sub)
    for t in chosen:
        shutil.copytree(tasks_dir / t, sub / t, copy_function=os.link)
    return sub


def start_agent(a: dict, tasks: Path, jobs_dir: Path, concurrency: int, region: str, log, root=".taskmine"):
    args, env = agents_mod.harbor_args(a, region, root)
    cmd = ["harbor", "run", "-y", "-p", str(tasks), *args, "-n", str(concurrency),
           "-o", str(jobs_dir), "--job-name", a["name"]]
    log(f"  $ {' '.join(cmd)}")
    out = open(jobs_dir / f"{a['name']}.harbor.log", "w")
    return subprocess.Popen(cmd, env={**os.environ, **env}, stdout=out, stderr=subprocess.STDOUT)


def watch(procs: dict, jobs_dir: Path, log, poll: float = 2.0) -> dict:
    """Print each trial as Harbor finishes it, until every agent's job has exited."""
    seen: set[Path] = set()
    t0 = time.time()
    while True:
        for name in procs:
            job = jobs_dir / name
            for r in sorted(job.rglob("result.json")) if job.exists() else []:
                if "__" not in r.parent.name or r in seen:
                    continue
                try:
                    d = json.loads(r.read_text())
                except ValueError:
                    continue  # still being written
                seen.add(r)
                rew = (d.get("verifier_result") or {}).get("rewards") or {}
                exc = (d.get("exception_info") or {}).get("exception_type")
                mark = "error " + str(exc) if exc else "solved" if rew.get("reward") == 1.0 else "failed"
                ar = d.get("agent_result") or {}
                cost = f"  ${ar['cost_usd']:.2f}" if isinstance(ar.get("cost_usd"), (int, float)) else ""
                m, sec = divmod(int(time.time() - t0), 60)
                log(f"  [{m:02d}:{sec:02d}] {name:<30} {d['task_name']:<16} {mark}{cost}")
        if all(p.poll() is not None for p in procs.values()):
            return {n: p.returncode for n, p in procs.items()}
        time.sleep(poll)


def collect(job: Path) -> dict:
    rows = {}
    for r in job.rglob("result.json"):
        if "__" not in r.parent.name:
            continue
        d = json.loads(r.read_text())
        rew = (d.get("verifier_result") or {}).get("rewards") or {}
        exc = (d.get("exception_info") or {}).get("exception_type")
        ar = d.get("agent_result") or {}
        rows[d["task_name"]] = {
            "reward": rew.get("reward"),
            "error": exc,
            "cost_usd": ar.get("cost_usd"),
            "n_input_tokens": ar.get("n_input_tokens"),
            "n_output_tokens": ar.get("n_output_tokens"),
        }
    return rows


def summarize(bench_dir: Path, project_agents: list[dict]) -> dict:
    out = {"agents": []}
    by_name = {a["name"]: a for a in project_agents}
    for job in sorted(p for p in bench_dir.iterdir() if p.is_dir() and not p.name.startswith("tasks")):
        rows = collect(job)
        if not rows:
            continue
        solved = sum(1 for r in rows.values() if r["reward"] == 1.0)
        errors = sum(1 for r in rows.values() if r["error"])
        cost = [r["cost_usd"] for r in rows.values() if isinstance(r["cost_usd"], (int, float))]
        a = by_name.get(job.name, {})
        out["agents"].append({
            "name": job.name, "agent": a.get("agent"), "provider": a.get("provider"), "model": a.get("model"),
            "trials": len(rows), "solved": solved, "errors": errors,
            "cost_usd": round(sum(cost), 2) if cost else None, "per_task": rows,
        })
    return out


def table(summary: dict) -> str:
    agents = summary["agents"]
    if not agents:
        return "no bench results yet: run `taskmine bench`"
    lines = [f"{'agent':<34} {'model':<32} {'solved':>9} {'rate':>6} {'errors':>6} {'cost':>8}"]
    for a in sorted(agents, key=lambda x: (-x["solved"], x["name"])):
        n = a["trials"] - a["errors"]
        rate = f"{100 * a['solved'] / n:.0f}%" if n else "-"
        cost = f"${a['cost_usd']:.2f}" if a["cost_usd"] is not None else "-"
        lines.append(f"{a['name']:<34} {str(a['model'])[:32]:<32} {a['solved']:>4}/{a['trials']:<4} {rate:>6} "
                     f"{a['errors']:>6} {cost:>8}")
    tasks = sorted({t for a in agents for t in a["per_task"]})
    if tasks and len(tasks) <= 30:
        lines.append("")
        head = "task".ljust(22) + "".join(a["name"][:16].ljust(18) for a in agents)
        lines.append(head)
        for t in tasks:
            cells = []
            for a in agents:
                r = a["per_task"].get(t)
                cells.append(("-" if r is None else "error" if r["error"] else
                              "solved" if r["reward"] == 1.0 else "failed").ljust(18))
            lines.append(t[:21].ljust(22) + "".join(cells))
    return "\n".join(lines)
