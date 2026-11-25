"""The interface contract: the lexer, each kind of item, the scan budget, and source-text coupling."""

from __future__ import annotations

import unittest

from taskmine import contract


class Lexer(unittest.TestCase):
    def test_javascript_comments_strings_and_templates(self):
        src = ("/* block 'not a string' */ const a = 'one'; // line 'nope'\n"
               "const b = \"two \\\" quoted\";\nconst c = `plain`; const d = `has ${a} inside`;\n"
               "describe('suite title', () => it('case title', () => {}));\n")
        code, strings = contract.split_source("x.test.mjs", src)
        self.assertNotIn("block", code)
        self.assertNotIn("nope", code)
        self.assertEqual(strings, [("one", False), ('two \\" quoted', False), ("plain", False),
                                   ("suite title", True), ("case title", True)])

    def test_python_comments_and_triple_quotes(self):
        src = "# a 'comment'\nx = '''multi\nline'''\ny = \"one\"  # trailing\nz = `x`\n"
        code, strings = contract.split_source("test_x.py", src)
        self.assertEqual(strings, [("multi\nline", False), ("one", False)])
        self.assertIn("`x`", code)
        self.assertNotIn("comment", code)

    def test_an_unterminated_single_quote_stops_at_the_line_end(self):
        _, strings = contract.split_source("a.js", "const s = 'open\nconst t = 'closed';\n")
        self.assertEqual(strings, [("open", False), ("closed", False)])

    def test_other_languages_are_left_alone(self):
        self.assertEqual(contract.split_source("a.go", "x := \"y\""), ("x := \"y\"", []))


class Extract(unittest.TestCase):
    BASE_PATHS = {"lib/old.mjs", "lib/index.mjs"}
    AFTER_PATHS = BASE_PATHS | {"lib/new.ts", "lib/feature/index.js", "test/helper.mjs"}

    def extract(self, tests, base=b"export function oldThing() {}\n", gold=""):
        return contract.extract(tests, base, self.BASE_PATHS, self.AFTER_PATHS, gold)

    def test_names_must_be_new_used_by_tests_and_added_by_the_change(self):
        src = {"test/a.test.mjs": "import { oldThing, brandNew, helperName } from '../lib/index.mjs';\n"
                                  "const localHelper = 1;\n"
                                  "test('x', () => { brandNew(oldThing, localHelper, abc, notAdded); });\n"}
        c = self.extract(src, gold="export function brandNew() {}\nconst abc = 1;\nlocalHelper();\n")
        self.assertEqual(c.new_names, ["brandNew"])

    def test_a_name_in_the_base_text_is_not_new_even_inside_a_longer_word(self):
        src = {"test/a.test.mjs": "test('x', () => parseThing());\n"}
        self.assertEqual(self.extract(src, base=b"// parseThing was here\n", gold="parseThing").new_names, [])
        self.assertEqual(self.extract(src, base=b"reparseThings()\n", gold="parseThing").new_names, ["parseThing"])

    def test_modules_the_change_creates(self):
        src = {"test/a.test.mjs": "import { f } from '../lib/new.js';\nimport g from '../lib/feature';\n"
                                  "import h from '../lib/old.mjs';\nimport * as t from './helper.mjs';\n"
                                  "const m = await import('../lib/missing.mjs');\nimport 'node:fs';\n",
               "test/helper.mjs": "export const t = 1;\n"}  # a test-side file the change adds is not asked for
        self.assertEqual(self.extract(src).new_modules, ["lib/feature/index.js", "lib/new.ts"])

    def test_exact_text_excludes_titles_and_text_already_in_the_base(self):
        src = {"test/a.test.mjs": "test('a title the gold adds', () => {\n"
                                  "  assert.equal(f(), 'E_RATE_LIMITED');\n"
                                  "  assert.equal(g(), 'old message');\n"
                                  "  assert.equal(h(), 'ab');\n"
                                  "});\n"}
        gold = "throw new Error('E_RATE_LIMITED'); // a title the gold adds\nreturn 'old message';\nreturn 'ab';\n"
        c = self.extract(src, base=b"return 'old message';\n", gold=gold)
        self.assertEqual(c.exact_text, ["E_RATE_LIMITED"])
        self.assertFalse(c.truncated)

    def test_the_scan_budget_marks_the_contract_truncated(self):
        literals = [f"'value number {i:04d}'" for i in range(contract.MAX_CHECKS + 10)]
        src = {"test/a.test.mjs": "const xs = [" + ", ".join(literals) + "];\n"}
        gold = "\n".join(lit.strip("'") for lit in literals)
        c = self.extract(src, gold=gold)
        self.assertTrue(c.truncated)
        self.assertEqual(len(c.exact_text), contract.MAX_STRINGS)

    def test_to_json_round_trips(self):
        c = contract.Contract(["a"], ["m.js"], ["t"], True)
        self.assertEqual(contract.Contract(**c.to_json()), c)


class Patches(unittest.TestCase):
    PATCH = (b"diff --git a/lib/a.js b/lib/a.js\n--- a/lib/a.js\n+++ b/lib/a.js\n@@ -1 +1,2 @@\n x\n+added code\n"
             b"diff --git a/README.md b/README.md\n--- a/README.md\n+++ b/README.md\n@@ -1 +1,2 @@\n y\n+added docs\n")

    def test_added_lines_with_and_without_a_skip(self):
        self.assertEqual(contract.added_lines(self.PATCH), ["added code", "added docs"])
        self.assertEqual(contract.added_lines(self.PATCH, skip_paths=lambda p: p.endswith(".md")), ["added code"])
        self.assertEqual(contract.added_lines(b""), [])


class SourceTextReads(unittest.TestCase):
    def test_paths_relative_to_the_test_the_workdir_or_the_root(self):
        tests = {
            "web/test/a.test.mjs": "const s = readFileSync('src/App.tsx', 'utf8');\n",
            "web/test/b.test.mjs": "const s = fs.readFileSync('web/src/App.tsx');\n",
            "web/test/c.test.mjs": "const s = readFileSync(new URL('../src/App.tsx?raw', import.meta.url));\n",
            "web/test/d.test.mjs": "import { App } from '../src/App.tsx';\n",
            "web/test/e.test.mjs": "readFileSync('fixture.json')\n",
            "tests/test_x.py": "src = open('pkg/core.py').read()\n",
        }
        got = contract.source_text_reads(tests, {"web/src/App.tsx", "pkg/core.py"}, "web")
        self.assertEqual(got, {"web/test/a.test.mjs": ["web/src/App.tsx"], "web/test/b.test.mjs": ["web/src/App.tsx"],
                               "web/test/c.test.mjs": ["web/src/App.tsx"],
                               "tests/test_x.py": ["pkg/core.py"]})

    def test_a_bare_file_name_counts_when_the_path_is_joined(self):
        tests = {"t/a.test.mjs": "readFileSync(join(root, 'lib', 'core.mjs'))\n"}
        self.assertEqual(contract.source_text_reads(tests, {"lib/core.mjs"}, "."), {"t/a.test.mjs": ["lib/core.mjs"]})


if __name__ == "__main__":
    unittest.main()
