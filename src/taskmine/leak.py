"""Answer-absence check for the instruction.

A task whose instruction contains its own solution measures copying. The check compares the
instruction with the CODE lines the gold change adds (documentation files are skipped: a changelog
bullet that repeats the title is not a solution):

- LINE: a gold line of 30 or more non-space characters appears verbatim (whitespace-normalised).
- SHINGLE: 12 consecutive code tokens of the gold change appear in the instruction in order.
- DIFF: the instruction contains diff syntax.

Text the contract states on purpose (new names, exact strings) is removed before the shingle
check, so stating the interface is never reported as a leak.
"""

from __future__ import annotations

import re

_TOKEN = re.compile(r"[A-Za-z0-9_$]+|[^\sA-Za-z0-9_$]")
SHINGLE = 12
MIN_LINE = 30


_COMMENT = re.compile(r"^\s*(?://|#|/\*|\*|<!--)")


def _norm(s: str) -> str:
    return " ".join(s.split())


def stated_paths(run_files: list[str], workdir: str) -> list[str]:
    """The test files the instruction names on purpose (in the run command and the test list)."""
    out = []
    for f in run_files:
        out.append(f)
        if workdir not in (".", "") and f.startswith(workdir.rstrip("/") + "/"):
            out.append(f[len(workdir.rstrip("/")) + 1 :])
    return out


def code_text_items(items: list[str], gold_code_lines: list[str]) -> list[str]:
    """Contract strings that contain a whole gold code line the LINE rule would report."""
    lines = [_norm(g) for g in gold_code_lines if not _COMMENT.match(g)]
    lines = [g for g in lines if len(g.replace(" ", "")) >= MIN_LINE]
    return [s for s in items if any(g in _norm(s) for g in lines)]


def find(text: str, gold_code_lines: list[str], allowed: list[str]) -> list[str]:
    """Comment-only gold lines are skipped: a comment that repeats a test's name implements nothing."""
    gold_code_lines = [line for line in gold_code_lines if not _COMMENT.match(line)]
    found = []
    if re.search(r"^(?:diff --git |@@ -\d+(?:,\d+)? \+\d+)", text, re.M):
        found.append("diff syntax in the instruction")
    flat = _norm(text)
    for line in gold_code_lines:
        n = _norm(line)
        if len(n.replace(" ", "")) >= MIN_LINE and n in flat:
            found.append(f"gold line verbatim: {n[:80]}")
    stripped = text
    for a in sorted(allowed, key=len, reverse=True):
        stripped = stripped.replace(a, " ")
    itoks = _TOKEN.findall(stripped)
    ishingles = {tuple(itoks[i : i + SHINGLE]) for i in range(max(0, len(itoks) - SHINGLE + 1))}
    gtoks = _TOKEN.findall("\n".join(gold_code_lines))
    for i in range(max(0, len(gtoks) - SHINGLE + 1)):
        sh = tuple(gtoks[i : i + SHINGLE])
        if sh in ishingles and sum(1 for t in sh if t[0].isalnum() or t[0] in "_$") >= 6:
            found.append("gold code sequence: " + " ".join(sh)[:80])
            break
    return found
