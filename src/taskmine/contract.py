"""The interface contract: what hidden tests need that the base repository does not have.

Hidden tests are fair only when the instruction names what they depend on. SWE-bench Verified's
human review threw out most of its rejected tasks for exactly this: tests that call a function
or expect a message the issue never mentions, so a correct fix still fails. Here the contract is
computed, not judged:

- NEW NAMES: identifiers the test code uses that occur nowhere in the base snapshot and that the
  gold change adds (e.g. a new exported function).
- NEW MODULES: files the change creates that the tests import: a relative import that resolves to
  one, or an import statement in any language that names one (`from shop.discount import`,
  `use shop::discount::`, `use Acme\\Shop\\Discount;`, `require "shop/discount"`).
- EXACT TEXT: string literals the tests contain (test titles excluded) that occur nowhere in the
  base snapshot and that the gold change adds (an error code, a message, a key).
- CALLS: the first call of each new name in the test code, as written, so the argument order and
  types are not a guess (`Discount::of(1000, 'SAVE10')`).

Each item is mechanical and checkable: a reader can grep the base tree and the gold patch and get
the same list. The instruction states them; the leak check knows they are stated on purpose.
"""

from __future__ import annotations

import posixpath
import re
from collections import Counter
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
    calls: list[str] = field(default_factory=list)

    def to_json(self) -> dict:
        return asdict(self)


_REGEX_AFTER = set("(,=:[!&|?{};+-*%<>~^") | {""}
_REGEX_KEYWORDS = ("return", "typeof", "case", "in", "of", "new", "delete", "void", "throw", "yield", "await")


def _regex_may_start(code: list[str]) -> bool:
    """A `/` starts a regular expression literal where a value is expected, not after a value (division)."""
    tail = "".join(code[-40:]).rstrip()
    if not tail or tail[-1] in _REGEX_AFTER:
        return True
    word = re.search(r"([A-Za-z_$][\w$]*)$", tail)
    return bool(word and word.group(1) in _REGEX_KEYWORDS)


def _regex_end(text: str, i: int) -> int | None:
    """The index after a regular expression literal starting at i (its flags included), or None."""
    j, n, in_class = i + 1, len(text), False
    while j < n:
        ch = text[j]
        if ch == "\n":
            return None
        if ch == "\\":
            j += 2
            continue
        if ch == "[":
            in_class = True
        elif ch == "]":
            in_class = False
        elif ch == "/" and not in_class:
            j += 1
            while j < n and text[j].isalpha():
                j += 1
            return j
        j += 1
    return None


_C_LIKE = (".go", ".java", ".kt", ".kts", ".scala", ".rs", ".cs", ".swift", ".c", ".h", ".cc", ".cpp", ".hpp", ".m",
           ".dart", ".php")
# A string right after one of these is a test's title, not text the code must contain: rspec and minitest
# (`it "..."`, `test "..."`), Go subtests (`t.Run("...")`), JUnit 5 (`@DisplayName("...")`), xUnit (`DisplayName = "..."`).
_TITLE_BEFORE = re.compile(r"\b(?:it|describe|context|specify|example|scenario|feature|test|Run|DisplayName)\s*(?:\(\s*|=\s*)?$")
_CHAR_LITERAL = re.compile(r"'(?:\\.[^']*|[^\\'\n])'")
_INTERPOLATION = {".rb": "#{", ".php": "$", ".kt": "$", ".kts": "$", ".swift": "\\(", ".dart": "$", ".scala": "$"}


def _split_c_like(path: str, text: str) -> tuple[str, list[tuple[str, bool]]]:
    """split_source for C-family languages, PHP and Ruby: their comment and string forms, and none of a
    regular expression's (a `/.../` there is division or a comment)."""
    ext = "." + path.rsplit(".", 1)[-1].lower()
    ruby, php, rust, go, cs = ext == ".rb", ext == ".php", ext == ".rs", ext == ".go", ext == ".cs"
    hash_comments, slash_comments = ruby or php, not ruby
    interp = _INTERPOLATION.get(ext)
    code: list[str] = []
    strings: list[tuple[str, bool]] = []
    i, n = 0, len(text)

    def add(value: str, start: int, exact: bool = True) -> None:
        if exact and not (interp and interp in value):
            strings.append((value, bool(_TITLE_BEFORE.search("".join(code[-40:])))))
        code.append(" ")

    def quoted(start: int, q: str, multiline: bool) -> int:
        j, buf = start + len(q), []
        while j < n and not text.startswith(q, j):
            if text[j] == "\\" and j + 1 < n:
                buf.append(text[j:j + 2])
                j += 2
                continue
            if text[j] == "\n" and not multiline:
                break
            buf.append(text[j])
            j += 1
        add("".join(buf), start)
        return j + len(q)

    while i < n:
        c = text[i]
        if slash_comments and text.startswith("//", i) or hash_comments and c == "#" and not text.startswith("#[", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
        elif slash_comments and text.startswith("/*", i):
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
            code.append(" ")
        elif ruby and text.startswith("=begin", i) and (i == 0 or text[i - 1] == "\n"):
            j = text.find("\n=end", i)
            i = n if j < 0 else j + 5
        elif rust and c == "r" and (m := re.match(r'r(#*)"', text[i:i + 12])) and not (i and (text[i - 1].isalnum() or text[i - 1] == "_")):
            close = '"' + m.group(1)
            j = text.find(close, i + len(m.group(0)))
            j = n if j < 0 else j
            add(text[i + len(m.group(0)):j], i)
            i = j + len(close)
        elif go and c == "`":
            j = text.find("`", i + 1)
            j = n if j < 0 else j
            add(text[i + 1:j], i)
            i = j + 1
        elif cs and (text.startswith('@"', i) or text.startswith('$@"', i) or text.startswith('@$"', i)):
            k = text.index('"', i) + 1
            j = k
            while j < n and not (text[j] == '"' and text[j + 1:j + 2] != '"'):
                j += 2 if text[j] == '"' else 1
            add(text[k:j], i, exact="$" not in text[i:k])
            i = j + 1
        elif cs and text.startswith('$"', i):
            i = quoted(i + 1, '"', False)
            strings.pop() if strings and "{" in strings[-1][0] else None
        elif text.startswith('"""', i):
            i = quoted(i, '"""', True)
        elif c == '"':
            i = quoted(i, '"', ruby or php)
        elif c == "'":
            if ruby or php:
                i = quoted(i, "'", True)
            elif m := _CHAR_LITERAL.match(text, i):
                code.append(" ")
                i = m.end()
            else:  # a Rust lifetime or a lone quote: code
                code.append(c)
                i += 1
        else:
            code.append(c)
            i += 1
    return "".join(code), strings


def split_source(path: str, text: str) -> tuple[str, list[tuple[str, bool]]]:
    """(code with comments and strings blanked, [(string literal, is_test_title)])."""
    py = path.endswith(".py")
    js = path.endswith(_JS_EXT)
    if not (py or js):
        if path.lower().endswith(_C_LIKE + (".rb",)):
            return _split_c_like(path, text)
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
        elif js and c == "/" and _regex_may_start(code):
            j = _regex_end(text, i)
            if j is None:
                code.append(c)
                i += 1
            else:
                code.append(" ")
                i = j
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


def _short_name_present(haystack: bytes, name: str) -> bool:
    """A short name is in the base when it is called or declared there; as a word it is too common."""
    n = re.escape(name).encode()
    return re.search(rb"(?:[.:>]" + n + rb"|\b" + n + rb"\s*\(|\b(?:def|fn|func|function|sub)\s+(?:self\.)?" + n + rb")\b",
                     haystack) is not None


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


_PLACEHOLDER = re.compile(r"%[-+ 0#]*\d*(?:\.\d+)?[sdvfqxXiu]|\$\{[^}\n]*\}|#\{[^}\n]*\}|\{[^{}\n]*\}|\\\([^)\n]*\)"
                          r"|\$[A-Za-z_]\w*")
_QUOTED = re.compile(r'"((?:\\.|[^"\\\n])*)"' + r"|'((?:\\.|[^'\\\n])*)'" + r"|`((?:\\.|[^`\\])*)`")


def gold_templates(gold_added: str) -> list[re.Pattern]:
    """What the change's own string literals can produce at run time.

    A test that checks `"unknown code: FREE"` against a change that writes `"unknown code: %s"` (or
    `${code}`, `#{code}`, `{}`, `{0}`, or `"unknown code: " + code`) checks text the change adds,
    though the gold code never holds it verbatim.
    """
    out = []
    for m in _QUOTED.finditer(gold_added):
        lit = next(g for g in m.groups() if g is not None)
        if _PLACEHOLDER.search(lit):
            parts = _PLACEHOLDER.split(lit)
            if sum(len(x.strip()) for x in parts) >= 4:
                out.append(re.compile("^" + ".+?".join(re.escape(x) for x in parts) + "$"))
        elif len(lit.strip()) >= 4 and lit.endswith((": ", " ", "=")):
            out.append(re.compile("^" + re.escape(lit) + ".+$"))
    return out


# A name shorter than 4 characters is new only when the change declares it (Ruby's `def self.of`).
_GOLD_DECL = re.compile(r"\b(?:def|fn|func|function|sub)\s+(?:self\.|\([^)]*\)\s*)?([A-Za-z_]\w*)")
# An import statement, in any language; the words in it are module path segments.
_USE = re.compile(r"^[ \t]*(?:use|import|using|require|require_relative|from|mod|include|load)\b([^\n]*)", re.M)
_GO_IMPORTS = re.compile(r"^[ \t]*import\s*\(([^)]*)\)", re.M)
_GENERIC_STEMS = {"mod", "index", "__init__", "lib", "main", "init"}
_CODE_EXT = _JS_EXT + (".py", ".rb", ".go", ".java", ".kt", ".kts", ".scala", ".rs", ".cs", ".swift", ".c", ".h",
                       ".cc", ".cpp", ".hpp", ".m", ".dart", ".php", ".ex", ".exs")
MAX_CALL = 100


def _module_stem(path: str) -> str:
    stem = posixpath.splitext(posixpath.basename(path))[0]
    return posixpath.basename(posixpath.dirname(path)) if stem in _GENERIC_STEMS else stem


def imported_new_files(test_sources: dict[str, str], base_paths: set[str], after_paths: set[str]) -> list[str]:
    """Files the change creates whose module name is a segment of an import statement in the tests.

    A file named `mod`, `index`, `__init__`, `lib`, `main` or `init` is known by its directory. A
    name that a base file already has is left out, since the import may mean that file.
    """
    segments: set[str] = set()
    for path, src in test_sources.items():
        if path.endswith(_JS_EXT):  # the specifier only: `import { name } from` lists names, not modules
            found = [m.group(2) for m in _IMPORT.finditer(src)]
        else:
            found = [m.group(1) for m in list(_USE.finditer(src)) + list(_GO_IMPORTS.finditer(src))]
        for f in found:
            segments.update(re.findall(r"[A-Za-z_]\w*", f))
    base_stems = {_module_stem(p) for p in base_paths}
    return [p for p in sorted(after_paths - base_paths)
            if p not in test_sources and p.endswith(_CODE_EXT)
            and _module_stem(p) in segments and _module_stem(p) not in base_stems]


def first_call(src: str, name: str) -> str | None:
    """The first call of `name` in a test file, with its receiver and arguments, from one line.

    `name(...)`, `name.member(...)`, `name::member(...)`, `name->member(...)`, and the same after a
    receiver (`described_class.of(...)`, `Discount::of(...)`). None when no call fits on one line.
    """
    pat = re.compile(r"(?:[A-Za-z_$][\w$]*(?:\.|::|->))*\b" + re.escape(name) + r"\b(?:(?:\.|::|->)[A-Za-z_]\w*)?\s*\(")
    for m in pat.finditer(src):
        before = re.sub(r"\\.", "", src[src.rfind("\n", 0, m.start()) + 1: m.start()])
        if any(before.count(q) % 2 for q in "\"'`") or re.match(r"\s*(?://|#|\*|/\*)", before):
            continue  # in a string (a test title) or a comment
        depth, i = 0, m.end() - 1
        while i < len(src) and src[i] != "\n" and i - m.start() < MAX_CALL:
            depth += {"(": 1, ")": -1}.get(src[i], 0)
            if depth == 0:
                return src[m.start(): i + 1]
            i += 1
    return None


def extract(
    test_sources: dict[str, str],
    base_text: bytes,
    base_paths: set[str],
    after_paths: set[str],
    gold_added: str,
) -> Contract:
    gold_names = set(_IDENT.findall(gold_added))
    gold_short = {n for n in _GOLD_DECL.findall(gold_added) if 2 <= len(n) < 4}
    templates = gold_templates(gold_added)
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
                (len(tok) >= 4 or tok in gold_short) and re.search(r"[A-Za-z]", tok) and tok not in _KEYWORDS and tok not in declared
                and tok in gold_names and tok not in names and budget("names")
                and not (_short_name_present(base_text, tok) if len(tok) < 4 else _present(base_text, tok, word=True))
            ):
                names[tok] = None
        for s, is_title in strings:
            s_clean = s.strip()
            if (
                not is_title and 4 <= len(s_clean) <= 160 and re.search(r"[A-Za-z0-9]", s_clean)
                and s_clean not in texts
                and (s_clean in gold_added or any(t.match(s_clean) for t in templates)) and budget("text")
                and not _present(base_text, s_clean, word=False)
            ):
                texts[s_clean] = None
        for m in _IMPORT.finditer(src):
            if _resolve(path, m.group(2), base_paths) is not None:
                continue
            created = _resolve(path, m.group(2), after_paths)
            if created and created not in test_sources:
                modules[created] = None
    for created in imported_new_files(test_sources, base_paths, after_paths):
        modules[created] = None
    calls: dict[str, None] = {}
    for n in list(names)[:MAX_NAMES]:
        call = next((c for p in sorted(test_sources) if (c := first_call(test_sources[p], n))), None)
        if call:
            calls[call] = None
    c = Contract(
        new_names=list(names)[:MAX_NAMES],
        new_modules=sorted(modules),
        exact_text=list(texts)[:MAX_STRINGS],
        calls=list(calls),
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

    An import specifier names a module to load, not a file to read: a test that imports a changed
    module and reads a fixture is not reading the module's text. Each import of a path discounts
    one occurrence of that string, so a path that is both imported and read still counts.
    """
    by_name: dict[str, set[str]] = {}
    for g in gold_code_paths:
        by_name.setdefault(posixpath.basename(g), set()).add(g)
    out: dict[str, list[str]] = {}
    for path, src in test_sources.items():
        code, strings = split_source(path, src)
        if not _READ_CALL.search(code):
            continue
        imports = Counter(m.group(2) for m in _IMPORT.finditer(src))
        hits: set[str] = set()
        for value, is_title in strings:
            if imports[value] > 0:
                imports[value] -= 1
                continue
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
