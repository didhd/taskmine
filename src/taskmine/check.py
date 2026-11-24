"""`check`: static checks on finished task directories, with no repository and no Docker.

What a reviewer of a dataset (or a CI job) can prove from the task directory alone:

- STRUCTURE: Harbor's files exist (instruction.md, task.toml, environment/Dockerfile,
  solution/solve.sh, tests/test.sh) and the scripts are executable.
- TASK.TOML: parses as TOML, has the [verifier], [agent] and [environment] timeouts as floats.
- EXPECTED: at least one fail-to-pass test, and every test id names one of the run files.
- HIDDEN: no hidden test file is already in the snapshot with the same content at the same path,
  and the build context holds nothing but the Dockerfile, the snapshot and the dependency files.
- INSTRUCTION: the leak check against the gold patch passes.
"""

from __future__ import annotations

import io
import json
import os
import tarfile
import tomllib
from pathlib import Path

from taskmine import contract, leak
from taskmine.globs import GlobSet
from taskmine.snapshot import git_blob_sha

REQUIRED = ["instruction.md", "task.toml", "environment/Dockerfile", "environment/repo.tar.gz",
            "solution/solve.sh", "solution/gold.patch", "tests/test.sh", "tests/grader.py",
            "tests/spec.json", "tests/expected.json"]
DOCS = GlobSet(["**/*.md", "**/*.mdx", "**/*.txt", "**/CHANGELOG*", "**/LICENSE*", "docs/**"])


def check_task(task: Path) -> list[str]:
    problems = []
    for rel in REQUIRED:
        if not (task / rel).is_file():
            problems.append(f"missing {rel}")
    if problems:
        return problems
    for rel in ("solution/solve.sh", "tests/test.sh"):
        if not os.access(task / rel, os.X_OK):
            problems.append(f"{rel} is not executable")
    try:
        doc = tomllib.loads((task / "task.toml").read_text())
        for sec in ("verifier", "agent"):
            if not isinstance(doc.get(sec, {}).get("timeout_sec"), float):
                problems.append(f"task.toml [{sec}] timeout_sec is not a float")
        if not isinstance(doc.get("environment", {}).get("build_timeout_sec"), float):
            problems.append("task.toml [environment] build_timeout_sec is not a float")
    except tomllib.TOMLDecodeError as e:
        problems.append(f"task.toml does not parse: {e}")
        doc = {}

    spec = json.loads((task / "tests" / "spec.json").read_text())
    exp = json.loads((task / "tests" / "expected.json").read_text())
    if not exp.get("fail_to_pass"):
        problems.append("expected.json has no fail-to-pass test")
    run_files = set(spec["run_files"])
    for t in exp.get("fail_to_pass", []) + exp.get("pass_to_pass", []):
        if t.split("::", 1)[0] not in run_files:
            problems.append(f"test id outside the run files: {t[:80]}")
            break

    extra = [p.name for p in (task / "environment").iterdir() if p.name not in ("Dockerfile", "repo.tar.gz", "deps")]
    if extra:
        problems.append(f"unexpected build context entries: {', '.join(sorted(extra))}")
    hidden = {}
    files_root = task / "tests" / "files"
    for p in files_root.rglob("*"):
        if p.is_file():
            hidden[p.relative_to(files_root).as_posix()] = git_blob_sha(p.read_bytes())
    with tarfile.open(task / "environment" / "repo.tar.gz", "r:gz") as tar:
        for m in tar.getmembers():
            if m.isfile() and m.name in hidden:
                if git_blob_sha(tar.extractfile(m).read()) == hidden[m.name]:
                    problems.append(f"hidden test {m.name} is already in the snapshot")

    gold = (task / "solution" / "gold.patch").read_bytes()
    code_lines = contract.added_lines(gold, skip_paths=DOCS.match)
    meta = doc.get("metadata", {}).get("taskmine", {})
    con = meta.get("contract", {})
    allowed = list(con.get("names", [])) + list(con.get("modules", [])) + list(con.get("text", []))
    text = (task / "instruction.md").read_text()
    for f in leak.find(text, code_lines, allowed):
        problems.append(f"instruction leak: {f}")
    if not meta:
        problems.append("task.toml has no [metadata.taskmine] provenance")
    return problems
