"""numbers.json for the video, from the pulled dataset results (nothing typed by hand)."""
import json
import statistics
import sys
from pathlib import Path

D = Path(sys.argv[1])  # the dataset dir: report.json, validation/, tasks/, results/harbor-summary.json
rep = json.loads((D / "report.json").read_text())
vals = [json.loads(p.read_text()) for p in (D / "validation").glob("*.json")]
probed = [v for v in vals if "fail_to_pass_source_text" in v]
dropped = sum(len(v["fail_to_pass_source_text"]) for v in probed)
kept = sum(len(v.get("fail_to_pass", [])) for v in probed)
import tomllib
tasks = [tomllib.loads(p.read_text())["metadata"]["taskmine"] for p in (D / "tasks").glob("*/task.toml")]
hs = json.loads((D / "results" / "harbor-summary.json").read_text())
oracle = hs["oracle-0270"]
nop = hs["nop-0270"]


def mean(job):
    r = [v["reward"] for v in job["per_task"].values()]
    return sum(x or 0 for x in r) / len(r)


om, nm = mean(oracle), mean(nop)
out = {
    "units": rep["units"],
    "candidates": rep["candidates"],
    "accepted": len(tasks),
    "source_text_dropped": dropped,
    "f2p_total": dropped + kept,
    "profiles": len({t["profile"] for t in tasks}),
    "f2p_tasks_median": int(statistics.median(t["fail_to_pass"] for t in tasks)),
    "harbor_trials": oracle["trials"],
    "oracle_mean": f"{om:.3f}",
    "nop_mean": f"{nm:.3f}",
    "oracle_words": "one" if om == 1 else f"{om:.2f}",
}
print(json.dumps(out, indent=1))
