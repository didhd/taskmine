"""The interface contract: what hidden tests need that the base repository does not have.

Hidden tests are fair only when the instruction names what they depend on. SWE-bench Verified's
human review threw out most of its rejected tasks for exactly this: tests that call a function
or expect a message the issue never mentions, so a correct fix still fails. Here the contract is
computed, not judged:

- NEW NAMES: identifiers the test code uses that occur nowhere in the base snapshot and that the
  gold change adds (e.g. a new exported function).
- NEW MODULES: relative imports in the tests that resolve to a file the change creates.
- EXACT TEXT: string literals the tests contain (test titles excluded) that occur nowhere in the
  base snapshot and that the gold change adds (an error code, a message, a key).

Each item is mechanical and checkable: a reader can grep the base tree and the gold patch and get
the same list. The instruction states them; the leak check knows they are stated on purpose.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import asdict, dataclass, field

MAX_NAMES = 25
MAX_STRINGS = 20
# Each check scans the whole base snapshot (hundreds of MB in a monorepo). A test file with thousands
# of literals that the gold change also contains (a large fixture) would take minutes, so at most
# this many candidates of each kind are checked; the contract is then marked truncated.
MAX_CHECKS = 150

_IDENT = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*")
_TITLE_CALL = re.compile(r"\b(?:test|it|describe|suite|context|subtest)(?:\.\w+)*\s*\(\s*$")
_DECL = re.compile(r"\b(?:const|let|var|function\*?|class|def)\s+([A-Za-z_$][A-Za-z0-9_$]*)")
_IMPORT = re.compile(
    r"""(?:\bfrom\s*|\bimport\s*\(\s*|\brequire\s*\(\s*|^\s*import\s+)(['"])([^'"\n]+)\1""", re.M
)
_JS_EXT = (".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".mts", ".cts")
_KEYWORDS = set(
    """await async break case catch class const continue debugger default delete else export extends
    false finally function from import instanceof null return super switch this throw true typeof
    undefined void while with yield assert test describe expect equal deepEqual strictEqual ok
    match notEqual throws rejects doesNotThrow before after beforeEach afterEach mock self None True
    False lambda pass raise elif except print""".split()
)


@dataclass
class Contract:
    new_names: list[str] = field(default_factory=list)
    new_modules: list[str] = field(default_factory=list)
    exact_text: list[str] = field(default_factory=list)
    truncated: bool = False

    def to_json(self) -> dict:
        return asdict(self)


def split_source(path: str, text: str) -> tuple[str, list[tuple[str, bool]]]:
    """(code with comments and strings blanked, [(string literal, is_test_title)])."""
    py = path.endswith(".py")
    js = path.endswith(_JS_EXT)
    if not (py or js):
        return text, []
    code: list[str] = []
    strings: list[tuple[str, bool]] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if js and text.startswith("//", i) or py and c == "#":
            j = text.find("\n", i)
            i = n if j < 0 else j
        elif js and text.startswith("/*", i):
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
            code.append(" ")
        elif c in "'\"`" and not (py and c == "`"):
            q = text[i : i + 3] if py and text[i : i + 3] in ('"""', "'''") else c
            j = i + len(q)
            buf = []
            while j < n and not text.startswith(q, j):
                if text[j] == "\\" and j + 1 < n:
                    buf.append(text[j : j + 2])
                    j += 2
                    continue
                if len(q) == 1 and q != "`" and text[j] == "\n":
                    break
                buf.append(text[j])
                j += 1
            is_title = bool(_TITLE_CALL.search("".join(code[-40:])))
            value = "".join(buf)
            # A template literal with an interpolation is not exact text: its value exists only at run time.
            if not (q == "`" and "${" in value):
                strings.append((value, is_title))
            code.append(" ")
            i = j + len(q)
        else:
            code.append(c)
            i += 1
    return "".join(code), strings


def _present(haystack: bytes, needle: str, word: bool) -> bool:
    nb = needle.encode()
    start = haystack.find(nb)
    while start != -1:
        if not word:
            return True
        before = haystack[start - 1 : start]
        after = haystack[start + len(nb) : start + len(nb) + 1]
        if not (before and (before.isalnum() or before in b"_$")) and not (
            after and (after.isalnum() or after in b"_$")
        ):
            return True
        start = haystack.find(nb, start + 1)
    return False


def _resolve(test_path: str, spec: str, paths: set[str]) -> str | None:
    if not spec.startswith("."):
        return None
    target = posixpath.normpath(posixpath.join(posixpath.dirname(test_path), spec))
    stem = target[: -len(posixpath.splitext(target)[1])] if target.endswith(_JS_EXT) else target
    options = [target] + [stem + e for e in _JS_EXT] + [target + "/index" + e for e in _JS_EXT]
    for o in options:
        if o in paths:
            return o
    return None


def extract(
    test_sources: dict[str, str],
    base_text: bytes,
    base_paths: set[str],
    after_paths: set[str],
    gold_added: str,
) -> Contract:
    gold_names = set(_IDENT.findall(gold_added))
    checks = {"names": 0, "text": 0}
    over = False

    def budget(kind: str) -> bool:
        nonlocal over
        checks[kind] += 1
        if checks[kind] > MAX_CHECKS:
            over = True
            return False
        return True

    names: dict[str, None] = {}
    texts: dict[str, None] = {}
    modules: dict[str, None] = {}
    for path, src in sorted(test_sources.items()):
        code, strings = split_source(path, src)
        declared = set(_DECL.findall(code))
        for tok in _IDENT.findall(code):
            if (
                len(tok) >= 4 and re.search(r"[A-Za-z]", tok) and tok not in _KEYWORDS and tok not in declared
                and tok in gold_names and tok not in names and budget("names")
                and not _present(base_text, tok, word=True)
            ):
                names[tok] = None
        for s, is_title in strings:
            s_clean = s.strip()
            if (
                not is_title and 4 <= len(s_clean) <= 160 and re.search(r"[A-Za-z0-9]", s_clean)
                and s_clean not in texts and s_clean in gold_added and budget("text")
                and not _present(base_text, s_clean, word=False)
            ):
                texts[s_clean] = None
        for m in _IMPORT.finditer(src):
            if _resolve(path, m.group(2), base_paths) is not None:
                continue
            created = _resolve(path, m.group(2), after_paths)
            if created and created not in test_sources:
                modules[created] = None
    c = Contract(
        new_names=list(names)[:MAX_NAMES],
        new_modules=sorted(modules),
        exact_text=list(texts)[:MAX_STRINGS],
    )
    c.truncated = over or len(names) > MAX_NAMES or len(texts) > MAX_STRINGS
    return c


def added_lines(patch: bytes, skip_paths=None) -> list[str]:
    """The `+` lines of a unified diff, optionally skipping some files (e.g. documentation)."""
    lines = []
    current = None
    for raw in patch.decode("utf-8", "replace").splitlines():
        if raw.startswith("diff --git "):
            parts = raw.split(" b/", 1)
            current = parts[1] if len(parts) == 2 else None
            continue
        if raw.startswith("+++") or raw.startswith("---"):
            continue
        if raw.startswith("+") and not (skip_paths and current and skip_paths(current)):
            lines.append(raw[1:])
    return lines


_READ_CALL = re.compile(
    r"\b(?:readFileSync|readFile|createReadStream|readTextFile|read_text|read_bytes|Bun\.file)\s*\(|\bopen\s*\("
)


def source_text_reads(test_sources: dict[str, str], gold_code_paths: set[str], workdir: str) -> dict[str, list[str]]:
    """Test files that read, as text, a source file the change modifies: {test file: [paths]}.

    A test that matches a regular expression against source text passes only for one spelling of
    the fix, so its fail-to-pass result says nothing about behaviour. The detection is static and
    explainable: the test file calls a file-reading function, and one of its string literals names
    a file the gold change touches (as a path relative to the test, the profile's workdir or the
    repository root, or as a bare file name when the path is assembled with join()).
    """
    by_name: dict[str, set[str]] = {}
    for g in gold_code_paths:
        by_name.setdefault(posixpath.basename(g), set()).add(g)
    out: dict[str, list[str]] = {}
    for path, src in test_sources.items():
        code, strings = split_source(path, src)
        if not _READ_CALL.search(code):
            continue
        hits: set[str] = set()
        for value, is_title in strings:
            v = value.strip()
            if is_title or not v or len(v) > 300 or "\n" in v:
                continue
            v = v.split("?", 1)[0].removeprefix("file://")
            options = {
                posixpath.normpath(posixpath.join(posixpath.dirname(path), v)),
                posixpath.normpath(posixpath.join(workdir, v)),
                posixpath.normpath(v.lstrip("/")),
            }
            hits |= options & gold_code_paths
            if "/" not in v and "." in v and len(v) >= 5:
                hits |= by_name.get(v, set())
        if hits:
            out[path] = sorted(hits)
    return out
