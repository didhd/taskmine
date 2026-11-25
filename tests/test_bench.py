"""bench and results: reading Harbor's job directories, the table, the task sample and the live watch."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

from support import TempDir

from taskmine import bench


def trial(job: Path, task: str, reward: float | None = None, error: str | None = None, cost=None,
          tokens: tuple[int, int] | None = None) -> None:
    d = job / "2026-11-24__00-00-00" / f"{task}__abc123"
    d.mkdir(parents=True, exist_ok=True)
    ar = {"cost_usd": cost}
    if tokens:
        ar.update(n_input_tokens=tokens[0], n_output_tokens=tokens[1])
    (d / "result.json").write_text(json.dumps({
        "task_name": task,
        "verifier_result": {"rewards": {"reward": reward}} if reward is not None else None,
        "exception_info": {"exception_type": error} if error else None,
        "agent_result": ar,
    }))


class Collect(TempDir):
    def test_rows_per_task(self):
        job = self.tmp / "claude-code-sonnet"
        trial(job, "shop-pr1", 1.0, cost=0.25, tokens=(1000, 50))
        trial(job, "shop-pr2", None, error="AgentTimeoutError")
        (job / "result.json").write_text(json.dumps({"job": "summary, not a trial"}))
        rows = bench.collect(job)
        self.assertEqual(rows, {
            "shop-pr1": {"reward": 1.0, "error": None, "cost_usd": 0.25, "n_input_tokens": 1000, "n_output_tokens": 50},
            "shop-pr2": {"reward": None, "error": "AgentTimeoutError", "cost_usd": None, "n_input_tokens": None,
                         "n_output_tokens": None},
        })


class Summary(TempDir):
    def run_dir(self) -> Path:
        run = self.tmp / "run"
        trial(run / "b-agent", "t1", 1.0, cost=0.125)
        trial(run / "b-agent", "t2", 0.0, cost=0.25)
        trial(run / "a-agent", "t1", 1.0)
        trial(run / "a-agent", "t2", None, error="RuntimeError")
        (run / "empty-agent").mkdir()
        trial(run / "tasks", "t9", 1.0)  # the sampled task set, not an agent
        return run

    def test_summarize_reads_every_agent_job(self):
        s = bench.summarize(self.run_dir(), [{"name": "a-agent", "agent": "codex", "provider": "bedrock", "model": "m-a"}])
        self.assertEqual([a["name"] for a in s["agents"]], ["a-agent", "b-agent"])
        a, b = s["agents"]
        self.assertEqual((a["agent"], a["provider"], a["model"]), ("codex", "bedrock", "m-a"))
        self.assertEqual((a["trials"], a["solved"], a["errors"], a["cost_usd"]), (2, 1, 1, None))
        self.assertEqual((b["agent"], b["trials"], b["solved"], b["errors"], b["cost_usd"]), (None, 2, 1, 0, 0.38))

    def test_the_table_ranks_by_solved_and_rates_without_errors(self):
        text = bench.table(bench.summarize(self.run_dir(), []))
        lines = text.splitlines()
        self.assertTrue(lines[0].startswith("agent"))
        self.assertTrue(lines[1].startswith("a-agent"))
        self.assertEqual(lines[1].split()[-4:], ["1/2", "100%", "1", "-"])
        self.assertEqual(lines[2].split()[-4:], ["1/2", "50%", "0", "$0.38"])
        grid = lines[4:]
        self.assertEqual(grid[0].split(), ["task", "a-agent", "b-agent"])
        self.assertEqual(grid[1].split(), ["t1", "solved", "solved"])
        self.assertEqual(grid[2].split(), ["t2", "error", "failed"])

    def test_large_runs_skip_the_grid_and_empty_runs_say_so(self):
        run = self.tmp / "big"
        for i in range(31):
            trial(run / "agent", f"t{i:02d}", 1.0)
        self.assertEqual(len(bench.table(bench.summarize(run, [])).splitlines()), 2)
        self.assertEqual(bench.table({"agents": []}), "no bench results yet: run `taskmine bench`")

    def test_an_agent_with_only_errors_has_no_rate(self):
        run = self.tmp / "errors"
        trial(run / "agent", "t1", None, error="X")
        self.assertEqual(bench.table(bench.summarize(run, [])).splitlines()[1].split()[-4:], ["0/1", "-", "1", "-"])


class Sample(TempDir):
    def tasks(self, n: int) -> Path:
        root = self.tmp / "tasks"
        for i in range(n):
            (root / f"t{i}" / "environment").mkdir(parents=True)
            (root / f"t{i}" / "task.toml").write_text(f"n = {i}\n")
            (root / f"t{i}" / "environment" / "repo.tar.gz").write_bytes(b"x" * i)
        (root / "not-a-task").mkdir()
        return root

    def test_all_tasks_need_no_copy(self):
        root = self.tasks(3)
        self.assertEqual(bench.pick_tasks(root, None, 0, self.tmp / "run"), root)
        self.assertEqual(bench.pick_tasks(root, 3, 0, self.tmp / "run"), root)

    def test_a_seeded_sample_is_stable_and_hard_linked(self):
        root = self.tasks(10)
        out = self.tmp / "run"
        sub = bench.pick_tasks(root, 4, 7, out)
        chosen = sorted(p.name for p in sub.iterdir())
        self.assertEqual(len(chosen), 4)
        again = sorted(p.name for p in bench.pick_tasks(root, 4, 7, out).iterdir())
        self.assertEqual(again, chosen)
        t = chosen[0]
        self.assertEqual(os.stat(sub / t / "task.toml").st_ino, os.stat(root / t / "task.toml").st_ino)
        other = sorted(p.name for p in bench.pick_tasks(root, 4, 8, self.tmp / "run2").iterdir())
        self.assertNotEqual(other, chosen)


class Watch(TempDir):
    def test_each_trial_is_printed_once_as_it_finishes(self):
        script = textwrap.dedent("""
            import json, pathlib, sys, time
            job = pathlib.Path(sys.argv[1])
            for i, (reward, exc, cost) in enumerate([(1.0, None, 0.5), (0.0, None, None), (None, "Boom", None)]):
                d = job / "ts" / f"task{i}__x"
                d.mkdir(parents=True)
                (d / "result.json").write_text(json.dumps({"task_name": f"task{i}",
                    "verifier_result": {"rewards": {"reward": reward}} if reward is not None else None,
                    "exception_info": {"exception_type": exc} if exc else None,
                    "agent_result": {"cost_usd": cost}}))
                time.sleep(0.1)
            sys.exit(3)
        """)
        procs = {"agent-a": subprocess.Popen([sys.executable, "-c", script, str(self.tmp / "agent-a")])}
        out = io.StringIO()
        t0 = time.time()
        codes = bench.watch(procs, self.tmp, lambda s: print(s, file=out), poll=0.05)
        self.assertLess(time.time() - t0, 30)
        self.assertEqual(codes, {"agent-a": 3})
        lines = out.getvalue().splitlines()
        self.assertEqual(len(lines), 3)
        self.assertEqual(lines[0].split()[1:], ["agent-a", "task0", "solved", "$0.50"])
        self.assertEqual(lines[1].split()[1:], ["agent-a", "task1", "failed"])
        self.assertEqual(lines[2].split()[1:], ["agent-a", "task2", "error", "Boom"])
