"""Path globs with gitignore-style `**`, compiled once.

`fnmatch` lets `*` cross `/`, which makes `foxl/tests/*.test.mjs` also match files in nested
directories. The test-side and run-file decisions are the whole grading contract, so they use
this stricter matcher: `*` and `?` stay inside one path segment, `**/` spans any number of
directories (including none) and a trailing `/**` matches everything below a directory.
"""

from __future__ import annotations

import re
from collections.abc import Iterable


def compile_glob(pattern: str) -> re.Pattern[str]:
    out = []
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("/**", i) and i + 3 == len(pattern):
            out.append("(?:/.*)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("^" + "".join(out) + "$")


class GlobSet:
    def __init__(self, patterns: Iterable[str]):
        self.patterns = list(patterns)
        self._compiled = [compile_glob(p) for p in self.patterns]

    def match(self, path: str) -> bool:
        return any(c.match(path) for c in self._compiled)

    def __bool__(self) -> bool:
        return bool(self.patterns)
