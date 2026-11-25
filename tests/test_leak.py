"""The answer-absence check: each rule fires on what it names, and stated text never counts."""

from __future__ import annotations

import unittest

from taskmine import leak

GOLD = [
    "export function retryAfterSeconds(header, now) {",
    "  const when = Date.parse(header);",
    "  return Number.isNaN(when) ? Number(header) : Math.max(0, (when - now) / 1000);",
    "}",
    "// retry after: accept an HTTP date as well as seconds, the way browsers do",
]


class Rules(unittest.TestCase):
    def test_a_clean_instruction_passes(self):
        self.assertEqual(leak.find("Accept an HTTP date in Retry-After.\n", GOLD, []), [])

    def test_line_rule_is_whitespace_insensitive(self):
        text = "Hint:   return Number.isNaN(when) ?\n Number(header) : Math.max(0, (when - now) / 1000);\n"
        found = leak.find(text, GOLD, [])
        self.assertTrue(any(f.startswith("gold line verbatim: return Number.isNaN") for f in found), found)

    def test_short_lines_are_not_line_leaks(self):
        self.assertEqual(leak.find("const when = Date.parse(header);", ["const when = Date.parse(header);"], []), [])

    def test_shingle_rule_finds_twelve_tokens_in_order(self):
        text = "Write return Number.isNaN(when) ? Number(header) for the delay."
        found = leak.find(text, GOLD, [])
        self.assertEqual(len(found), 1)
        self.assertTrue(found[0].startswith("gold code sequence:"), found)

    def test_punctuation_runs_are_not_shingles(self):
        gold = ["x = ((((((((((((a))))))))))))"]
        self.assertEqual(leak.find("((((((((((((a))))))))))))", gold, []), [])

    def test_allowed_text_is_removed_before_the_shingle_check(self):
        text = "Add `return Number.isNaN(when) ? Number(header)` exactly."
        self.assertTrue(leak.find(text, GOLD, []))
        self.assertEqual(leak.find(text, GOLD, ["return Number.isNaN(when) ? Number(header)"]), [])

    def test_comment_lines_are_not_gold_code(self):
        text = "retry after: accept an HTTP date as well as seconds, the way browsers do"
        self.assertEqual(leak.find(text, GOLD, []), [])

    def test_diff_syntax(self):
        self.assertEqual(leak.find("@@ -1,2 +1,3 @@\n", [], []), ["diff syntax in the instruction"])
        self.assertEqual(leak.find("see diff --git in the docs", [], []), [])


class Helpers(unittest.TestCase):
    def test_stated_paths_add_the_workdir_relative_form(self):
        self.assertEqual(leak.stated_paths(["web/test/a.test.mjs", "lib/b.test.mjs"], "web"),
                         ["web/test/a.test.mjs", "test/a.test.mjs", "lib/b.test.mjs"])
        self.assertEqual(leak.stated_paths(["t/a.py"], "."), ["t/a.py"])
        self.assertEqual(leak.stated_paths(["web/t/a.py"], "web/"), ["web/t/a.py", "t/a.py"])

    def test_code_text_items_are_whole_gold_lines(self):
        items = ["return Number.isNaN(when) ? Number(header) : Math.max(0, (when - now) / 1000);",
                 "Retry-After", "// retry after: accept an HTTP date as well as seconds, the way browsers do"]
        self.assertEqual(leak.code_text_items(items, GOLD), items[:1])


if __name__ == "__main__":
    unittest.main()
