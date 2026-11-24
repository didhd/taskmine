"""`report`: what happened to every change unit, as numbers a reader can check.

One funnel, from units in the history to finished tasks, with the reason at every step where a
unit left it. No score and no quality adjective: counts, reasons and distributions.
"""

from __future__ import annotations

import json
import statistics
from collections import Counter
from pathlib import Path


def _dist(values: list[float]) -> str:
    if not values:
        return "-"
    v = sorted(values)
    q = lambda p: v[min(len(v) - 1, int(p * (len(v) - 1) + 0.5))]
    return f"median {statistics.median(v):g}, p10 {q(0.1):g}, p90 {q(0.9):g}, max {v[-1]:g}"


def build(out: Path) -> tuple[str, dict]:
    cands = [json.loads(l) for l in (out / "candidates.jsonl").read_text().splitlines() if l.strip()]
    scan_refusals = Counter(c["refusal"] for c in cands if c["refusal"])
    n_cand = sum(1 for c in cands if not c["refusal"])
    build_refusals = Counter()
    bpath = out / "build-refusals.jsonl"
    if bpath.exists():
        for l in bpath.read_text().splitlines():
            if l.strip():
                build_refusals[json.loads(l)["code"]] += 1
    vals = [json.loads(p.read_text()) for p in sorted((out / "validation").glob("*.json"))] if (out / "validation").exists() else []
    vrej = Counter(v.get("reason") for v in vals if v.get("verdict") != "accepted")
    accepted = [v for v in vals if v.get("verdict") == "accepted"]
    fin = []
    fpath = out / "finalize.jsonl"
    if fpath.exists():
        fin = [json.loads(l) for l in fpath.read_text().splitlines() if l.strip()]
    done = [f for f in fin if f.get("status") == "task"]
    fin_ref = Counter(f.get("code") for f in fin if f.get("status") != "task")

    tasks = []
    for t in sorted((out / "tasks").glob("*/task.toml")) if (out / "tasks").exists() else []:
        import tomllib
        tasks.append(tomllib.loads(t.read_text())["metadata"]["taskmine"])

    data = {
        "units": len(cands), "scan_refused": dict(scan_refusals), "candidates": n_cand,
        "build_refused": dict(build_refusals), "validated": len(vals), "validation_rejected": dict(vrej),
        "accepted": len(accepted), "finalize_refused": dict(fin_ref), "tasks": len(tasks),
    }
    L = ["# taskmine dataset report", ""]
    L.append("| step | units | left out here, by reason |")
    L.append("|---|---:|---|")
    fmt = lambda c: ", ".join(f"{k} {v}" for k, v in c.most_common()) or "-"
    L.append(f"| change units in the history | {len(cands)} | {fmt(scan_refusals)} |")
    L.append(f"| candidates (scan) | {n_cand} | {fmt(build_refusals)} |")
    L.append(f"| validated in Docker | {len(vals)} | {fmt(vrej)} |")
    L.append(f"| accepted by execution | {len(accepted)} | {fmt(fin_ref)} |")
    L.append(f"| finished tasks | {len(tasks)} | |")
    L.append("")
    if tasks:
        L.append("## Finished tasks")
        L.append("")
        L.append(f"- by profile: {', '.join(f'{k} {v}' for k, v in Counter(t['profile'] for t in tasks).most_common())}")
        L.append(f"- fail-to-pass tests per task: {_dist([t['fail_to_pass'] for t in tasks])}")
        L.append(f"- pass-to-pass tests per task: {_dist([t['pass_to_pass'] for t in tasks])}")
        L.append(f"- gold change, files: {_dist([t['gold_files'] for t in tasks])}")
        L.append(f"- gold change, lines: {_dist([t['gold_lines'] for t in tasks])}")
        src = Counter(s for t in tasks for s in t["instruction_sources"])
        L.append(f"- instruction sources: {', '.join(f'{k} {v}' for k, v in src.most_common())}")
        L.append(f"- tasks naming a new name, module or exact text: "
                 f"{sum(1 for t in tasks if t['contract_names'] or t['contract_modules'] or t['contract_text'])}")
        notes = sum(1 for t in tasks if t.get("notes"))
        L.append(f"- tasks whose issue text was left out by the leak check: {notes}")
        L.append("")
    probed = [v for v in vals if "fail_to_pass_source_text" in v]
    if probed:
        dropped = sum(len(v["fail_to_pass_source_text"]) for v in probed)
        kept = sum(len(v.get("fail_to_pass", [])) for v in probed)
        L.append("## Source-text tests")
        L.append("")
        L.append(f"Fail-to-pass tests that pass only because the source TEXT changed (the text probe gives test "
                 f"code the base text of changed files; these tests then fail): {dropped} of {dropped + kept} "
                 f"fail-to-pass tests in {len(probed)} drafts. They are never required. Drafts left with no "
                 f"behavioural fail-to-pass test: {vrej.get('source-text-only', 0)}. Accepted tasks that had some "
                 f"dropped: {sum(1 for v in accepted if v.get('fail_to_pass_source_text'))}.")
        L.append("")
    if accepted:
        secs = [sum(r["seconds"] for r in v["runs"]) for v in accepted]
        L.append(f"Validation time per accepted task (all runs, seconds): {_dist(secs)}")
        L.append("")
    return "\n".join(L) + "\n", data
