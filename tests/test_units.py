"""Unit tests for the pure parts: globs, the leak check, the contract lexer, TOML, deps."""

from __future__ import annotations

import json
import tomllib
import unittest

from taskmine import contract, deps, instruction, leak, tomlw
from taskmine.globs import GlobSet


class Globs(unittest.TestCase):
    def test_star_stays_in_one_segment(self):
        g = GlobSet(["foxl/tests/*.test.mjs"])
        self.assertTrue(g.match("foxl/tests/a.test.mjs"))
        self.assertFalse(g.match("foxl/tests/sub/a.test.mjs"))

    def test_double_star(self):
        g = GlobSet(["**/tests/**"])
        self.assertTrue(g.match("tests/a.py"))
        self.assertTrue(g.match("relay/tests/fixtures/x.json"))
        self.assertFalse(g.match("relay/testsuite/x.json"))


class Leak(unittest.TestCase):
    gold = ["  if (!Number.isInteger(a)) throw new RangeError('multiplyExact needs integers');", "  return a * b;"]

    def test_a_gold_line_in_the_instruction_is_a_leak(self):
        text = "Fix it.\n\n    if (!Number.isInteger(a)) throw new RangeError('multiplyExact needs integers');\n"
        self.assertTrue(leak.find(text, self.gold, []))

    def test_stated_contract_is_not_a_leak(self):
        text = "Add `multiplyExact`. It must throw `multiplyExact needs integers`.\n"
        self.assertEqual(leak.find(text, self.gold, ["multiplyExact", "multiplyExact needs integers"]), [])

    def test_diff_syntax_is_a_leak(self):
        self.assertTrue(leak.find("diff --git a/x b/x\n", [], []))


class Contract(unittest.TestCase):
    def test_titles_are_not_exact_text_and_comments_are_ignored(self):
        code, strings = contract.split_source(
            "t.test.mjs", "// uses ghostName\ntest('a title', () => f('needle text'));\n")
        self.assertNotIn("ghostName", code)
        self.assertIn(("a title", True), strings)
        self.assertIn(("needle text", False), strings)

    def test_extract(self):
        src = {"test/x.test.mjs": "import { newThing } from '../lib/new.mjs';\n"
                                  "test('works', () => assert.equal(newThing(), 'brand new value'));\n"}
        c = contract.extract(src, b"export function old() {}\n", {"lib/old.mjs"}, {"lib/old.mjs", "lib/new.mjs"},
                             "export function newThing() { return 'brand new value'; }")
        self.assertEqual(c.new_names, ["newThing"])
        self.assertEqual(c.new_modules, ["lib/new.mjs"])
        self.assertEqual(c.exact_text, ["brand new value"])


class SourceText(unittest.TestCase):
    def test_a_test_that_reads_changed_source_is_coupled(self):
        src = {"web/test/a.test.mjs": "import { readFileSync } from 'node:fs';\n"
               "const s = readFileSync(new URL('../src/App.tsx', import.meta.url), 'utf8');\n"
               "test('wired', () => assert.match(s, /railToggle/));\n",
               "web/test/b.test.mjs": "import { readFileSync } from 'node:fs';\n"
               "const s = readFileSync(join(here, '..', 'src', 'App.tsx'), 'utf8');\n",
               "web/test/c.test.mjs": "import { f } from '../src/App.tsx';\ntest('works', () => f());\n"}
        got = contract.source_text_reads(src, {"web/src/App.tsx"}, "web")
        self.assertEqual(got, {"web/test/a.test.mjs": ["web/src/App.tsx"], "web/test/b.test.mjs": ["web/src/App.tsx"]})


class Instruction(unittest.TestCase):
    def test_clean_title(self):
        self.assertEqual(instruction.clean_title("[next train] fix(x): a thing (#5895, chore 2 of #6040)"), "fix(x): a thing")
        self.assertEqual(instruction.clean_title("fix: keep (parens) here"), "fix: keep (parens) here")


class Toml(unittest.TestCase):
    def test_round_trip(self):
        doc = {"version": "1.0", "metadata": {"tags": ["a", "b"], "taskmine": {"n": 3, "s": 'q"x'}},
               "verifier": {"timeout_sec": 900.0}, "agent": {"timeout_sec": 3600.0}}
        back = tomllib.loads(tomlw.dumps(doc))
        self.assertEqual(back, doc)
        self.assertIsInstance(back["verifier"]["timeout_sec"], float)


class Deps(unittest.TestCase):
    def test_npm_version_bump_keeps_the_fingerprint(self):
        def files(v):
            pkg = json.dumps({"name": "x", "version": v, "dependencies": {"a": "^1"}}).encode()
            lock = json.dumps({"name": "x", "version": v, "packages": {"": {"version": v}, "node_modules/a": {"version": "1.2.3"}}}).encode()
            return {"x/package.json": pkg, "x/package-lock.json": lock}
        _, a = deps.materialize(files("1.0.0"), "npm")
        _, b = deps.materialize(files("1.0.1"), "npm")
        _, c = deps.materialize(files("1.0.1"), None)
        self.assertEqual(a, b)
        self.assertNotEqual(b, c)


if __name__ == "__main__":
    unittest.main()
