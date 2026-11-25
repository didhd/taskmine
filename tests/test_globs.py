"""Path globs: the matcher decides the test side, so its three copies (core, grader, text probe) must agree."""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import unittest
from pathlib import Path

from taskmine.globs import GlobSet, compile_glob

TEMPLATES = Path(__file__).resolve().parent.parent / "src" / "taskmine" / "templates"

CASES = [
    ("test/*.mjs", "test/a.mjs", True),
    ("test/*.mjs", "test/sub/a.mjs", False),
    ("test/?.mjs", "test/a.mjs", True),
    ("test/?.mjs", "test/ab.mjs", False),
    ("test/?.mjs", "test//.mjs", False),
    ("**/tests/**", "tests/a.py", True),
    ("**/tests/**", "a/b/tests/c/d.py", True),
    ("**/tests/**", "a/testsuite/d.py", False),
    ("a/**/b.py", "a/b.py", True),
    ("a/**/b.py", "a/x/y/b.py", True),
    ("a/**/b.py", "ab.py", False),
    ("docs/**", "docs", True),
    ("docs/**", "docs/x/y.md", True),
    ("docs/**", "docsx/y.md", False),
    ("src/**.py", "src/a/b.py", True),
    ("**", "anything/at/all", True),
    ("*.test.*", "a.test.js", True),
    ("*.test.*", "dir/a.test.js", False),
    ("**/*.test.*", "dir/a.test.js", True),
    ("lib/a+b(1).js", "lib/a+b(1).js", True),
    ("lib/a+b(1).js", "lib/aab1.js", False),
    ("lib/[ab].js", "lib/[ab].js", True),
    ("lib/[ab].js", "lib/a.js", False),
    ("$weird^{x}|.js", "$weird^{x}|.js", True),
]


def load_grader():
    spec = importlib.util.spec_from_file_location("taskmine_grader", TEMPLATES / "grader.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Matching(unittest.TestCase):
    def test_cases(self):
        for pattern, path, want in CASES:
            with self.subTest(pattern=pattern, path=path):
                self.assertEqual(bool(compile_glob(pattern).match(path)), want)

    def test_a_globset_matches_any_pattern_and_is_falsy_when_empty(self):
        g = GlobSet(["a/*", "b/**"])
        self.assertTrue(g.match("b/c/d"))
        self.assertFalse(g.match("c"))
        self.assertTrue(g)
        self.assertFalse(GlobSet([]))
        self.assertFalse(GlobSet([]).match("a"))
        self.assertEqual(g.patterns, ["a/*", "b/**"])


class Copies(unittest.TestCase):
    """grader.py runs inside task containers and textprobe.mjs inside Node; neither can import taskmine."""

    def test_the_grader_matches_like_the_core(self):
        grader = load_grader()
        for pattern, path, want in CASES:
            with self.subTest(pattern=pattern, path=path):
                self.assertEqual(bool(grader.compile_glob(pattern).match(path)), want)

    @unittest.skipUnless(shutil.which("node"), "needs node")
    def test_the_text_probe_matches_like_the_core(self):
        src = (TEMPLATES / "textprobe.mjs").read_text()
        start = src.index("const toRegex = ")
        body = src[start: src.index("\n};\n", start) + 3]
        script = body + "\nconst cases = JSON.parse(process.argv[1]);\n" \
            "console.log(JSON.stringify(cases.map(([p, s]) => toRegex(p).test(s))));\n"
        out = subprocess.run(["node", "-e", script, json.dumps([[p, s] for p, s, _ in CASES])],
                             capture_output=True, text=True, check=True).stdout
        self.assertEqual(json.loads(out), [want for _, _, want in CASES])


if __name__ == "__main__":
    unittest.main()
