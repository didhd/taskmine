"""`scan`: every change unit in the history, and why each one can or cannot become a task.

A change unit is a merged pull request (a merge commit: base = first parent, after = the merge)
or, for squash-merge histories, a first-parent commit (base = its parent). Its files split into
the TEST SIDE (hidden from the agent, restored before grading) and the GOLD SIDE (the change the
agent is asked to make; `solution/` applies it). A unit is a candidate only when its test side
holds a file some profile can run and its gold side changes something other than documentation.

Every refusal has a code and a sentence, because "why was my PR not used" is the first question
a person asks and the answer must not require reading this file.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from taskmine.config import Config
from taskmine.vcs import Repo

REFUSALS = {
    "not-a-pr": "Its subject matches none of [units] pr_patterns, so it is not a pull request merge.",
    "not-two-parents": "A merge unit must have exactly two parents (base = first parent).",
    "superseded": "The same pull request landed again later; the newest landing is the one used.",
    "no-test-change": "It changes no test-side file, so there is nothing to grade it with.",
    "no-runnable-test": (
        "Test-side files changed, but none of the added or modified ones matches a profile's `run` "
        "globs. Add a [[profiles]] entry for that test runner, or accept that these are left out."
    ),
    "no-code-change": "Every gold-side file is documentation ([tests] docs), so the task would ask for nothing.",
    "gold-too-large": "The gold change exceeds [limits] max_gold_files or max_gold_lines.",
    "too-many-run-files": "It changes more runnable test files than [limits] max_run_files.",
    "large-file": (
        "It changes a file larger than [limits] max_changed_file_bytes, or one whose content this "
        "clone does not hold (a filtered clone)."
    ),
    "submodule-change": "It changes a submodule pointer, which a snapshot cannot carry.",
}


@dataclass
class Candidate:
    id: str
    unit: str
    pr: int | None
    base: str
    after: str
    subject: str
    date: str
    profile: str | None = None
    run_files: list[str] = field(default_factory=list)
    test_files: list[str] = field(default_factory=list)  # test side, added or modified (restored)
    test_deleted: list[str] = field(default_factory=list)  # test side, deleted by the change
    gold_files: list[str] = field(default_factory=list)
    gold_lines: int = 0
    refusal: str | None = None
    detail: str | None = None

    def to_json(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_json(d: dict) -> "Candidate":
        return Candidate(**d)


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.split("/")[-1].lower()).strip("-")


def _pr_number(cfg: Config, subject: str) -> int | None:
    for pat in cfg.pr_patterns:
        m = re.search(pat, subject)
        if m:
            return int(m.group(1))
    return None


def list_units(repo: Repo, cfg: Config) -> tuple[list[Candidate], list[Candidate]]:
    """Returns (units, refused-before-diff). Newest first, as git log orders them."""
    slug = _slug(cfg.repo_name)
    units: list[Candidate] = []
    refused: list[Candidate] = []
    seen: set[int] = set()
    commits = (
        repo.merges(cfg.branch, cfg.since)
        if cfg.units_kind == "merges"
        else repo.first_parent_commits(cfg.branch, cfg.since)
    )
    for c in commits:
        pr = _pr_number(cfg, c.subject)
        unit = f"pr{pr}" if pr is not None else c.sha[:12]
        cand = Candidate(
            id=f"{slug}-{unit}", unit=unit, pr=pr, base=c.parents[0] if c.parents else "",
            after=c.sha, subject=c.subject, date=c.date,
        )
        if cfg.units_kind == "merges":
            if pr is None:
                cand.refusal = "not-a-pr"
            elif len(c.parents) != 2:
                cand.refusal = "not-two-parents"
        elif len(c.parents) != 1:
            cand.refusal = "not-two-parents" if len(c.parents) > 1 else "no-test-change"
        if cand.refusal is None and pr is not None:
            if pr in seen:
                cand.refusal = "superseded"
            seen.add(pr)
        (refused if cand.refusal else units).append(cand)
    return units, refused


def classify(repo: Repo, cfg: Config, cand: Candidate) -> Candidate:
    changes = repo.changes(cand.base, cand.after)
    if any("160000" in (ch.old_mode, ch.new_mode) for ch in changes):
        cand.refusal = "submodule-change"
        return cand
    test_side = [ch for ch in changes if cfg.tests.match(ch.path)]
    gold_side = [ch for ch in changes if not cfg.tests.match(ch.path)]
    cand.test_files = sorted(ch.path for ch in test_side if ch.status != "D")
    cand.test_deleted = sorted(ch.path for ch in test_side if ch.status == "D")
    cand.gold_files = sorted(ch.path for ch in gold_side)
    if not test_side:
        cand.refusal = "no-test-change"
        return cand

    best, best_files = None, []
    for prof in cfg.profiles:
        files = [p for p in cand.test_files if prof.run.match(p)]
        if len(files) > len(best_files):
            best, best_files = prof, files
    if best is None:
        cand.refusal = "no-runnable-test"
        return cand
    cand.profile = best.name
    cand.run_files = best_files

    if not gold_side or all(cfg.docs.match(ch.path) for ch in gold_side):
        cand.refusal = "no-code-change"
        return cand

    stats = repo.numstat(cand.base, cand.after)
    cand.gold_lines = sum(sum(stats.get(p) or (0, 0)) for p in cand.gold_files)
    if len(cand.gold_files) > cfg.max_gold_files or cand.gold_lines > cfg.max_gold_lines:
        cand.refusal = "gold-too-large"
        cand.detail = f"{len(cand.gold_files)} files, {cand.gold_lines} lines"
        return cand
    if len(cand.run_files) > cfg.max_run_files:
        cand.refusal = "too-many-run-files"
        cand.detail = f"{len(cand.run_files)} run files"
        return cand

    sizes = repo.blob_sizes([s for ch in changes for s in (ch.old_sha, ch.new_sha)])
    big = [
        ch.path for ch in changes
        if any((sizes.get(s) or 0) > cfg.max_changed_file_bytes for s in (ch.old_sha, ch.new_sha))
        or any(s in sizes and sizes[s] is None for s in (ch.old_sha, ch.new_sha))
    ]
    if big:
        cand.refusal = "large-file"
        cand.detail = ", ".join(big[:5])
    return cand


def scan(repo: Repo, cfg: Config, limit: int | None = None) -> list[Candidate]:
    units, refused = list_units(repo, cfg)
    if limit is not None:
        units = units[:limit]
    return [classify(repo, cfg, u) for u in units] + refused
