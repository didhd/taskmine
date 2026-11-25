"""The dataset report: a funnel with a reason at every step, read from the files each step writes."""

from __future__ import annotations

import json
from pathlib import Path

from support import TempDir

from taskmine import report, tomlw


def cand(i: int, refusal: str | None = None) -> dict:
    return {"id": f"shop-pr{i}", "unit": f"pr{i}", "refusal": refusal}


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


class Funnel(TempDir):
    def dataset(self) -> Path:
        out = self.tmp / "out"
        out.mkdir()
        write_jsonl(out / "candidates.jsonl", [cand(1), cand(2), cand(3), cand(4), cand(5, "not-a-pr"),
                                               cand(6, "not-a-pr"), cand(7, "no-test-change")])
        write_jsonl(out / "build-refusals.jsonl", [{"id": "shop-pr4", "code": "seal", "detail": "x"}])
        (out / "validation").mkdir()
        runs = [{"kind": "base", "seconds": 2.0}, {"kind": "gold", "seconds": 3.5}]
        (out / "validation" / "shop-pr1.json").write_text(json.dumps({
            "id": "shop-pr1", "verdict": "accepted", "fail_to_pass": ["a", "b"], "pass_to_pass": ["c"],
            "fail_to_pass_source_text": ["d"], "runs": runs}))
        (out / "validation" / "shop-pr2.json").write_text(json.dumps({
            "id": "shop-pr2", "verdict": "rejected", "reason": "source-text-only", "fail_to_pass": [],
            "fail_to_pass_source_text": ["e", "f"], "runs": runs}))
        (out / "unvalidated.json").write_text(json.dumps({"unvalidated": []}))
        write_jsonl(out / "finalize.jsonl", [{"id": "shop-pr1", "status": "task"}])
        t = out / "tasks" / "shop-pr1"
        t.mkdir(parents=True)
        (t / "task.toml").write_text(tomlw.dumps({"metadata": {"taskmine": {
            "profile": "node-test", "fail_to_pass": 2, "pass_to_pass": 1, "gold_files": 3, "gold_lines": 40,
            "instruction_sources": ["title", "tests", "contract"], "contract_names": 1, "contract_modules": 0,
            "contract_text": 0, "notes": ["issue text left out: x"]}}}))
        return out

    def test_the_funnel_counts_and_reasons(self):
        md, data = report.build(self.dataset())
        self.assertEqual(data, {
            "units": 7, "scan_refused": {"not-a-pr": 2, "no-test-change": 1}, "candidates": 4,
            "build_refused": {"seal": 1}, "drafts": 3, "validated": 2, "validation_rejected": {"source-text-only": 1},
            "not_validated": {"not-run": 1},
            "accepted": 1, "finalize_refused": {}, "tasks": 1,
        })
        self.assertIn("| change units in the history | 7 | not-a-pr 2, no-test-change 1 |", md)
        self.assertIn("| candidates (scan) | 4 | seal 1 |", md)
        self.assertIn("| drafts built | 3 | not-run 1 |", md)
        self.assertIn("| validated in Docker | 2 | source-text-only 1 |", md)
        self.assertIn("| accepted by execution | 1 | - |", md)
        self.assertIn("| finished tasks | 1 | |", md)

    def test_the_task_and_source_text_sections(self):
        md, _ = report.build(self.dataset())
        self.assertIn("- by profile: node-test 1", md)
        self.assertIn("- fail-to-pass tests per task: median 2, p10 2, p90 2, max 2", md)
        self.assertIn("- instruction sources: title 1, tests 1, contract 1", md)
        self.assertIn("- tasks naming a new name, module or exact text: 1", md)
        self.assertIn("- tasks whose issue text was left out by the leak check: 1", md)
        self.assertIn(": 3 of 5 fail-to-pass tests in 2 drafts.", md)
        self.assertIn("Drafts left with no behavioural fail-to-pass test: 1. Accepted tasks that had some dropped: 1.", md)
        self.assertIn("Validation time per accepted task (all runs, seconds): median 5.5", md)

    def test_drafts_stopped_at_the_time_limit(self):
        out = self.dataset()
        (out / "unvalidated.json").write_text(json.dumps({"unvalidated": ["shop-pr3"]}))
        md, data = report.build(out)
        self.assertEqual(data["not_validated"], {"stopped-at-the-time-limit": 1})
        self.assertIn("| drafts built | 3 | stopped-at-the-time-limit 1 |", md)

    def test_the_same_files_give_the_same_report(self):
        out = self.dataset()
        self.assertEqual(report.build(out), report.build(out))

    def test_a_fresh_scan_alone(self):
        out = self.tmp / "fresh"
        out.mkdir()
        write_jsonl(out / "candidates.jsonl", [cand(1), cand(2, "not-a-pr")])
        md, data = report.build(out)
        self.assertEqual((data["candidates"], data["drafts"], data["validated"], data["tasks"]), (1, 1, 0, 0))
        self.assertEqual(data["not_validated"], {"not-run": 1})
        self.assertNotIn("## Finished tasks", md)


class Distribution(TempDir):
    def test_dist(self):
        self.assertEqual(report._dist([]), "-")
        self.assertEqual(report._dist([3]), "median 3, p10 3, p90 3, max 3")
        self.assertEqual(report._dist(list(range(1, 11))), "median 5.5, p10 2, p90 9, max 10")
