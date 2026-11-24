"""Pull request records from GitHub, through the `gh` CLI (its own sign-in; taskmine holds no token).

Optional: without it a task's statement is the merge's or commit's subject line. With it, the
pull request title and the issues it closes. Bodies of pull requests are fetched for the record
but never put into an instruction (see instruction.py).
"""

from __future__ import annotations

import json
import subprocess
import time

_FIELDS = (
    "number title url createdAt mergedAt "
    "closingIssuesReferences(first: 5) { nodes { number title body createdAt url } }"
)


def _query(owner: str, name: str, numbers: list[int]) -> str:
    parts = [f"p{n}: pullRequest(number: {n}) {{ {_FIELDS} }}" for n in numbers]
    return f'query {{ repository(owner: "{owner}", name: "{name}") {{ {" ".join(parts)} }} }}'


def fetch(repo: str, numbers: list[int], batch: int = 25, retries: int = 4, log=print) -> dict[int, dict]:
    owner, name = repo.split("/", 1)
    out: dict[int, dict] = {}
    todo = sorted(set(numbers))
    for i in range(0, len(todo), batch):
        chunk = todo[i : i + batch]
        for attempt in range(retries):
            p = subprocess.run(["gh", "api", "graphql", "-f", f"query={_query(owner, name, chunk)}"],
                               capture_output=True, text=True)
            if p.returncode == 0:
                break
            log(f"gh api graphql failed (attempt {attempt + 1}/{retries}): {p.stderr.strip()[:200]}")
            time.sleep(5 * (attempt + 1))
        else:
            raise RuntimeError(f"GitHub metadata for pull requests {chunk[0]}..{chunk[-1]} could not be read")
        data = json.loads(p.stdout).get("data", {}).get("repository", {}) or {}
        for v in data.values():
            if v:
                out[v["number"]] = {
                    "title": v["title"],
                    "url": v["url"],
                    "createdAt": v["createdAt"],
                    "mergedAt": v["mergedAt"],
                    "issues": [
                        {k: iss.get(k) for k in ("number", "title", "body", "createdAt", "url")}
                        for iss in (v.get("closingIssuesReferences") or {}).get("nodes", [])
                    ],
                }
    return out
