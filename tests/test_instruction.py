"""instruction.md: fixed template text around named sources, the same bytes every time."""

from __future__ import annotations

import unittest

from taskmine import instruction
from taskmine.contract import Contract

BASE = dict(repo_name="example/shop", title="fix(cart): keep the coupon (#41)", issue=None, issue_limit=4000,
            workdir=".", display_command="node --test test/cart.test.mjs",
            f2p=["test/cart.test.mjs::cart::keeps the coupon"], contract=Contract())


def render(**kw):
    return instruction.render(**{**BASE, **kw})


class Titles(unittest.TestCase):
    def test_tags_and_issue_references_are_removed(self):
        self.assertEqual(instruction.clean_title("[web] [P1] fix: a thing (#12)"), "fix: a thing")
        self.assertEqual(instruction.clean_title("fix: a thing (closes #3) (#4)"), "fix: a thing")
        self.assertEqual(instruction.clean_title("fix: f(x) for (a, b)"), "fix: f(x) for (a, b)")

    def test_a_title_that_is_only_tags_is_kept(self):
        self.assertEqual(instruction.clean_title("  [wip]  "), "[wip]")


class Trim(unittest.TestCase):
    def test_short_text_is_unchanged(self):
        self.assertEqual(instruction.trim("a\r\nb\n", 10), ("a\nb", False))

    def test_cut_at_a_paragraph_break_when_one_is_late_enough(self):
        text = "first paragraph here\n\nsecond paragraph that is long"
        self.assertEqual(instruction.trim(text, 30), ("first paragraph here", True))

    def test_hard_cut_when_the_break_is_too_early(self):
        text = "a\n\n" + "b" * 50
        self.assertEqual(instruction.trim(text, 20), ("a\n\n" + "b" * 17, True))


class Render(unittest.TestCase):
    def test_title_tests_and_sources(self):
        md, sources = render()
        self.assertEqual(sources, ["title", "tests"])
        self.assertTrue(md.startswith("# fix(cart): keep the coupon\n\nThe repository at `/app` is example/shop"))
        self.assertIn("run from the repository root with:\n\n    node --test test/cart.test.mjs\n", md)
        self.assertIn("- cart > keeps the coupon (`test/cart.test.mjs`)", md)
        self.assertTrue(md.endswith("edits to them do not count.\n"))
        self.assertEqual(render(), (md, sources))

    def test_a_workdir_is_named(self):
        md, _ = render(workdir="web")
        self.assertIn("run from `web` with:", md)

    def test_the_issue_is_quoted_and_shortened(self):
        issue = {"number": 7, "title": "Coupon lost", "body": "Steps:\n\n1. add a coupon\n2. reload\n\n" + "x" * 50}
        md, sources = render(issue=issue, issue_limit=36)
        self.assertEqual(sources, ["title", "tests", "issue"])
        self.assertIn("requested in issue #7, quoted as it was written:\n\n> **Coupon lost**\n>\n> Steps:\n>\n"
                      "> 1. add a coupon\n> 2. reload\n>\n> (issue text shortened)\n", md)

    def test_an_issue_without_a_body(self):
        md, _ = render(issue={"number": 8, "title": "Just a title", "body": None})
        self.assertIn("> **Just a title**\n\n## How the change is checked", md)

    def test_long_test_lists_are_capped(self):
        f2p = [f"t/a.test.mjs::case {i}" for i in range(instruction.MAX_TEST_NAMES + 5)]
        md, _ = render(f2p=f2p)
        self.assertIn(f"- case {instruction.MAX_TEST_NAMES - 1} (`t/a.test.mjs`)", md)
        self.assertNotIn(f"- case {instruction.MAX_TEST_NAMES} (", md)
        self.assertIn("- and 5 more in the same files", md)

    def test_the_contract_sections(self):
        con = Contract(new_names=["applyCoupon"], new_modules=["lib/coupon.mjs"], exact_text=["E_COUPON", "a `b` c"])
        md, sources = render(contract=con)
        self.assertEqual(sources, ["title", "tests", "contract"])
        self.assertIn("## Names the tests use that do not exist yet\n\n- module `lib/coupon.mjs`\n- `applyCoupon`\n", md)
        self.assertIn("## Exact text the tests look for\n\n- `E_COUPON`\n- 'a `b` c'\n", md)

    def test_exact_text_alone_still_counts_as_the_contract(self):
        _, sources = render(contract=Contract(exact_text=["E_X"]))
        self.assertEqual(sources, ["title", "tests", "contract"])


if __name__ == "__main__":
    unittest.main()
