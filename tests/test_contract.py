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

    def test_a_language_without_a_lexer_is_left_alone(self):
        self.assertEqual(contract.split_source("a.ex", "x = \"y\""), ("x = \"y\"", []))

    def test_c_family_ruby_and_php_strings_and_comments(self):
        cases = {
            "cart/discount_test.go": ('package cart\n// TestX checks unknown\nfunc TestUnknown(t *testing.T) {\n'
                                      '\tif err.Error() != "unknown code: FREE" { t.Run("sub case", nil) }\n\tr := `raw text`\n}\n',
                                      [("unknown code: FREE", False), ("sub case", True), ("raw text", False)]),
            "tests/discount.rs": ("fn f<'a>(x: &'a str) -> char { let c = 'x'; let s = r#\"raw \"q\"\"#; "
                                  'assert_eq!(e, Err("unknown code: FREE".to_string())) }\n',
                                  [('raw "q"', False), ("unknown code: FREE", False)]),
            "Shop.Tests/T.cs": ('[Fact(DisplayName = "shows text")] public void A() { var t = $"x {y}"; '
                                'Assert.Equal("unknown code: FREE", m); }\n',
                                [("shows text", True), ("unknown code: FREE", False)]),
            "spec/d_spec.rb": ('# comment unknown\nit "raises unknown code" do\n'
                               '  expect { x }.to raise_error(ArgumentError, "unknown code: FREE")\n  y = "v#{z}"\nend\n',
                               [("raises unknown code", True), ("unknown code: FREE", False)]),
            "tests/DTest.php": ("<?php // c unknown\n# h unknown\n$this->expectExceptionMessage('unknown code: FREE'); $s = \"a $b\";\n",
                                [("unknown code: FREE", False)]),
            "src/test/java/DTest.java": ('@DisplayName("nice name") void t() { assertEquals("unknown code: FREE", e.getMessage()); '
                                         "char q = '\"'; }\n", [("nice name", True), ("unknown code: FREE", False)]),
        }
        for path, (src, want) in cases.items():
            code, strings = contract.split_source(path, src)
            self.assertNotIn("unknown", code, path)
            self.assertEqual(strings, want, path)

    def test_text_the_change_formats_counts_as_text_it_adds(self):
        gold = ('return 0, fmt.Errorf("unknown code: %s", code)\nthrow new IllegalArgumentException("unknown code: " + c);\n'
                "throw new Error(`unknown code: ${code}`);\nraise ArgumentError, \"unknown code: #{code}\"\n")
        templates = contract.gold_templates(gold)
        self.assertTrue(all(any(t.match("unknown code: FREE") for t in templates[i:i + 1]) for i in range(4)), templates)
        self.assertFalse(any(t.match("something else") for t in templates))

    def test_format_expressions_and_specs_count_as_placeholders(self):
        golds = ['raise ValueError(f"unknown code: {code.strip()}")', 'raise KeyError(f"unknown code: {code!r:>5}")',
                 'throw new ArgumentException($"unknown code: {code.Trim()}");', 'bail!("unknown code: {:?}", code)',
                 'throw Shop.Error("unknown code: \\(code)")', 'throw IllegalArgumentException("unknown code: $code")']
        for gold in golds:
            self.assertTrue(any(t.match("unknown code: FREE") for t in contract.gold_templates(gold)), gold)


class NewModulesAndCalls(unittest.TestCase):
    BASE = {"shop/__init__.py", "shop/cart.py", "src/lib.rs", "src/cart.rs", "src/Cart.php", "lib/shop/cart.rb",
            "pkg/utils.py", "README.md"}

    def new_files(self, tests, created):
        return contract.imported_new_files(tests, self.BASE, self.BASE | set(created) | set(tests))

    def test_an_import_in_any_language_names_a_file_the_change_creates(self):
        cases = {
            "tests/test_discount.py": ("from shop.discount import discount\n", "shop/discount.py"),
            "tests/discount.rs": ("use shop::discount::discount;\n", "src/discount.rs"),
            "tests/DiscountTest.php": ("<?php\nuse Acme\\Shop\\Discount;\n", "src/Discount.php"),
            "spec/discount_spec.rb": ('require_relative "../lib/shop/discount"\n', "lib/shop/discount.rb"),
            "tests/coupon.rs": ("use shop::coupon;\n", "src/coupon/mod.rs"),
        }
        for test, (src, created) in cases.items():
            self.assertEqual(self.new_files({test: src}, [created, "docs/discount.md"]), [created], test)

    def test_a_name_a_base_file_has_or_an_imported_name_is_not_a_new_module(self):
        self.assertEqual(self.new_files({"tests/test_u.py": "from pkg.utils import x\n"}, ["other/utils.py"]), [])
        self.assertEqual(self.new_files({"test/a.test.ts": "import { discount } from 'vitest';\n"}, ["src/discount.ts"]), [])

    def test_the_first_call_skips_titles_and_comments_and_keeps_the_receiver(self):
        src = ("test('discount(1000, SAVE10) is 100', () => {\n  // discount(1, 2)\n"
               "  expect(discount(1000, 'SAVE10')).toBe(100);\n});\n")
        self.assertEqual(contract.first_call(src, "discount"), "discount(1000, 'SAVE10')")
        self.assertEqual(contract.first_call("$this->assertSame(1, Discount::of(1000, 'A'));", "Discount"),
                         "Discount::of(1000, 'A')")
        self.assertEqual(contract.first_call("expect(described_class.of(1000, \"A\")).to eq(1)", "of"),
                         'described_class.of(1000, "A")')
        self.assertIsNone(contract.first_call("x = discount(\n  1000)\n", "discount"))

    def test_a_short_name_counts_when_the_change_declares_it(self):
        tests = {"spec/d_spec.rb": 'it "x" do\n  expect(described_class.of(1000, "A")).to eq(1)\nend\n'}
        c = contract.extract(tests, b"a list of things\n", set(), set(tests), "def self.of(cents, code)\n")
        self.assertEqual(c.new_names, ["of"])
        self.assertEqual(c.calls, ['described_class.of(1000, "A")'])
        c = contract.extract(tests, b"Money.of(5)\n", set(), set(tests), "def self.of(cents, code)\n")
        self.assertEqual(c.new_names, [])


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

    def test_importing_a_changed_module_is_not_reading_its_text(self):
        # Counted as a read, the import made every fail-to-pass test in a file that also reads a
        # fixture a "source-text" test under the static check, and dropped it.
        gold = {"lib/cart.mjs", "lib/codes.mjs"}
        fixture = {"t/a.test.mjs": "import { total } from '../lib/cart.mjs';\n"
                                   "const cases = JSON.parse(readFileSync(new URL('./cases.json', import.meta.url)));\n"}
        self.assertEqual(contract.source_text_reads(fixture, gold, "."), {})
        both = {"t/a.test.mjs": "import { total } from '../lib/cart.mjs';\nconst { CODES } = await import('../lib/codes.mjs');\n"
                                "const src = readFileSync(new URL('../lib/cart.mjs', import.meta.url), 'utf8');\n"}
        self.assertEqual(contract.source_text_reads(both, gold, "."), {"t/a.test.mjs": ["lib/cart.mjs"]})
        cjs = {"t/a.test.js": "const { total } = require('../lib/cart.mjs');\nconst f = fs.readFileSync('x.json');\n"}
        self.assertEqual(contract.source_text_reads(cjs, gold, "."), {})


if __name__ == "__main__":
    unittest.main()
