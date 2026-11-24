"""`instruction.md`, assembled from human-written records and mechanical facts only.

No sentence in an instruction is written by a model. Each part comes from a named source and the
task records which sources it used:

- `title`: the pull request's title (or the commit subject), with tracker tags and issue
  references removed. A title is a one-line problem statement a person wrote at the time.
- `issue`: the body of an issue the pull request closes, quoted, when it was opened before the
  pull request and passes the leak check. Pull request BODIES are never used: they describe the
  fix, and the agent is being asked to find it.
- `tests`: the names of the fail-to-pass tests, verbatim from the runner's own report, and the
  command that runs them. Test names in a codebase that writes them as behaviour statements are
  the acceptance criteria.
- `contract`: the new names, modules and exact text the hidden tests depend on (contract.py).

The template text around them is fixed and the same for every task.
"""

from __future__ import annotations

import re

from taskmine.contract import Contract

_TRAILING_REFS = re.compile(r"\s*\((?:[^()]*#\d+[^()]*)\)\s*$")
_LEADING_TAGS = re.compile(r"^(?:\s*\[[^\]]{1,40}\]\s*)+")

MAX_TEST_NAMES = 30


def clean_title(title: str) -> str:
    t = title.strip()
    t = _LEADING_TAGS.sub("", t)
    prev = None
    while prev != t:
        prev = t
        t = _TRAILING_REFS.sub("", t)
    return t.strip() or title.strip()


def _quote_block(text: str) -> str:
    return "\n".join(("> " + line) if line.strip() else ">" for line in text.strip().splitlines())


def trim(text: str, limit: int) -> tuple[str, bool]:
    text = text.replace("\r\n", "\n").strip()
    if len(text) <= limit:
        return text, False
    cut = text.rfind("\n\n", 0, limit)
    if cut < limit // 2:
        cut = limit
    return text[:cut].rstrip(), True


def render(
    *,
    repo_name: str,
    title: str,
    issue: dict | None,
    issue_limit: int,
    workdir: str,
    display_command: str,
    f2p: list[str],
    contract: Contract,
) -> tuple[str, list[str]]:
    """Returns (markdown, sources used)."""
    sources = ["title", "tests"]
    parts = [f"# {clean_title(title)}", ""]
    parts.append(
        f"The repository at `/app` is {repo_name} at the commit just before this change was made. "
        "Make the change described here."
    )
    parts.append("")
    if issue:
        body, cut = trim(issue.get("body") or "", issue_limit)
        parts.append(f"The change was requested in issue #{issue['number']}, quoted as it was written:")
        parts.append("")
        parts.append(_quote_block(f"**{issue['title']}**\n\n{body}" if body else f"**{issue['title']}**"))
        if cut:
            parts.append(">")
            parts.append("> (issue text shortened)")
        parts.append("")
        sources.append("issue")

    parts.append("## How the change is checked")
    parts.append("")
    where = "the repository root" if workdir in (".", "") else f"`{workdir}`"
    parts.append(f"Tests are added to the repository and run from {where} with:")
    parts.append("")
    parts.append(f"    {display_command}")
    parts.append("")
    parts.append("These tests must pass. Their names, as the test runner reports them:")
    parts.append("")
    shown = f2p[:MAX_TEST_NAMES]
    for t in shown:
        file, _, name = t.partition("::")
        parts.append(f"- {name.replace('::', ' > ')} (`{file}`)")
    if len(f2p) > len(shown):
        parts.append(f"- and {len(f2p) - len(shown)} more in the same files")
    parts.append("")
    parts.append(
        "Tests in those files that pass today must keep passing. The test files are replaced with "
        "their own versions before grading, so edits to them do not count."
    )
    parts.append("")

    if contract.new_names or contract.new_modules:
        sources.append("contract")
        parts.append("## Names the tests use that do not exist yet")
        parts.append("")
        for m in contract.new_modules:
            parts.append(f"- module `{m}`")
        for n in contract.new_names:
            parts.append(f"- `{n}`")
        parts.append("")
    if contract.exact_text:
        if "contract" not in sources:
            sources.append("contract")
        parts.append("## Exact text the tests look for")
        parts.append("")
        for s in contract.exact_text:
            parts.append(f"- `{s}`" if "`" not in s else f"- {s!r}")
        parts.append("")
    return "\n".join(parts).rstrip() + "\n", sources
