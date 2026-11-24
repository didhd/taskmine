"""scan over real histories: every refusal code is reached by a real commit, and candidates split right."""

from __future__ import annotations

from support import NODE_PROFILE, History, SharedTempDir, TempDir, needs_git, recipe

from taskmine import config, scan
from taskmine.vcs import Repo

RECIPE = """
[repo]
name = "example/shop"

[units]
kind = "merges"
branch = "main"

[limits]
max_gold_files = 3
max_gold_lines = 50
max_run_files = 2
max_changed_file_bytes = 2000
""" + NODE_PROFILE + """
[[profiles]]
name = "wide"
run = ["test/*.test.mjs", "web/test/*.test.mjs"]
image = "node:24-bookworm"
command = "node --test --test-reporter=junit --test-reporter-destination={junit} {files}"
"""

T = "import { test } from 'node:test';\ntest('t', () => {});\n"


@needs_git
class Refusals(SharedTempDir):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        h = History(cls.tmp / "repo")
        h.commit("init", {"lib/a.mjs": "export const a = 1;\n", "test/a.test.mjs": T, "README.md": "# shop\n"})
        h.pr(1, {"lib/a.mjs": "export const a = 2;\n", "test/a.test.mjs": T + "// more\n"})
        h.pr(2, {"README.md": "# shop!\n", "test/a.test.mjs": T + "// docs\n"})
        h.pr(3, {"lib/a.mjs": "export const a = 3;\n"})
        h.pr(4, {"lib/a.mjs": "export const a = 4;\n", "test/fixtures/data.json": "{}\n"})
        h.pr(5, {f"lib/f{i}.mjs": "x\n" for i in range(4)} | {"test/a.test.mjs": T + "// 5\n"})
        h.pr(6, {"lib/a.mjs": "export const a = 6;\n", "test/b.test.mjs": T, "test/c.test.mjs": T, "test/d.test.mjs": T})
        h.pr(7, {"lib/blob.mjs": "y" * 3000, "test/a.test.mjs": T + "// 7\n"})
        h.pr(8, {"lib/a.mjs": "export const a = 8;\n", "test/a.test.mjs": T + "// 8a\n"}, branch="again")
        h.pr(8, {"lib/a.mjs": "export const a = 9;\n", "test/a.test.mjs": T + "// 8b\n"}, branch="again2")
        h.pr(9, {"lib/a.mjs": "export const a = 10;\n", "test/e.test.mjs": T, "web/test/w.test.mjs": T})
        h.pr(10, {"lib/a.mjs": "export const a = 11;\n"}, subject="Merge branch 'direct'")
        cls.root = h.write()
        cls.cfg = config.load(recipe(cls.tmp, RECIPE))
        cls.cands = scan.scan(Repo(str(cls.root)), cls.cfg)
        cls.by_unit = {}
        for c in cls.cands:
            cls.by_unit.setdefault(c.unit, []).append(c)

    def one(self, unit):
        (c,) = self.by_unit[unit]
        return c

    def test_every_refusal_has_its_reason(self):
        self.assertIsNone(self.one("pr1").refusal)
        self.assertEqual(self.one("pr2").refusal, "no-code-change")
        self.assertEqual(self.one("pr3").refusal, "no-test-change")
        self.assertEqual(self.one("pr4").refusal, "no-runnable-test")
        self.assertEqual(self.one("pr5").refusal, "gold-too-large")
        self.assertEqual(self.one("pr5").detail, "4 files, 4 lines")
        self.assertEqual(self.one("pr6").refusal, "too-many-run-files")
        self.assertEqual(self.one("pr6").detail, "3 run files")
        self.assertEqual(self.one("pr7").refusal, "large-file")
        self.assertEqual(self.one("pr7").detail, "lib/blob.mjs")
        for c in self.cands:
            if c.refusal:
                self.assertIn(c.refusal, scan.REFUSALS)

    def test_a_subject_that_is_not_a_pr_is_left_out(self):
        (direct,) = [c for c in self.cands if c.subject == "Merge branch 'direct'"]
        self.assertEqual(direct.refusal, "not-a-pr")
        self.assertIsNone(direct.pr)
        self.assertEqual(direct.unit, direct.after[:12])
        self.assertEqual(direct.id, "shop-" + direct.after[:12])

    def test_the_newest_landing_of_a_pr_wins(self):
        landings = self.by_unit["pr8"]
        self.assertEqual([c.refusal for c in landings], [None, "superseded"])
        self.assertEqual(landings[0].subject, "Merge pull request #8 from example/again2")

    def test_the_profile_with_the_most_run_files_is_chosen(self):
        c = self.one("pr9")
        self.assertEqual(c.profile, "wide")
        self.assertEqual(c.run_files, ["test/e.test.mjs", "web/test/w.test.mjs"])

    def test_a_candidate_splits_test_side_from_gold_side(self):
        c = self.one("pr1")
        self.assertEqual(c.id, "shop-pr1")
        self.assertEqual(c.profile, "node-test")
        self.assertEqual(c.gold_files, ["lib/a.mjs"])
        self.assertEqual(c.test_files, ["test/a.test.mjs"])
        self.assertEqual(c.gold_lines, 2)
        commits = {x.sha: x for x in Repo(str(self.root)).merges("main", None)}
        self.assertEqual(c.base, commits[c.after].parents[0])

    def test_candidates_round_trip_through_json(self):
        for c in self.cands:
            self.assertEqual(scan.Candidate.from_json(c.to_json()), c)

    def test_limit_keeps_the_newest_units(self):
        cands = scan.scan(Repo(str(self.root)), self.cfg, limit=2)
        classified = [c for c in cands if c.refusal not in ("not-a-pr", "superseded")]
        self.assertEqual([c.unit for c in classified], ["pr9", "pr8"])


@needs_git
class OtherHistories(TempDir):
    def test_deleted_tests_and_submodules(self):
        h = History(self.tmp / "repo")
        h.commit("init", {"lib/a.mjs": "export const a = 1;\n", "test/a.test.mjs": T, "test/old.test.mjs": T})
        h.pr(1, {"lib/a.mjs": "export const a = 2;\n", "test/a.test.mjs": T + "//\n"}, delete=("test/old.test.mjs",))
        h.pr(2, {"vendor/dep": ("160000", "1" * 40)})
        root = h.write()
        cfg = config.load(recipe(self.tmp, '[repo]\nname = "example/x"\n' + NODE_PROFILE))
        by = {c.unit: c for c in scan.scan(Repo(str(root)), cfg)}
        self.assertEqual(by["pr1"].test_deleted, ["test/old.test.mjs"])
        self.assertEqual(by["pr1"].test_files, ["test/a.test.mjs"])
        self.assertEqual(by["pr2"].refusal, "submodule-change")

    def test_squash_commits_are_units_and_merges_are_refused(self):
        h = History(self.tmp / "repo")
        h.commit("init (#1)", {"lib/a.mjs": "export const a = 1;\n", "test/a.test.mjs": T}, when=1767225600)
        h.commit("feat: two (#12)", {"lib/a.mjs": "export const a = 2;\n", "test/a.test.mjs": T + "//\n"},
                 when=1772323200)  # 2026-03-01
        h.pr(13, {"lib/a.mjs": "export const a = 3;\n"}, branch="m")
        root = h.write()
        cfg = config.load(recipe(self.tmp, '[repo]\nname = "example/x"\n[units]\nkind = "commits"\n'
                                           'since = "2026-02-01"\n' + NODE_PROFILE))
        by = {c.subject: c for c in scan.scan(Repo(str(root)), cfg)}
        self.assertEqual(sorted(by), ["Merge pull request #13 from example/m", "feat: two (#12)"])
        self.assertEqual((by["feat: two (#12)"].unit, by["feat: two (#12)"].refusal), ("pr12", None))
        self.assertEqual(by["Merge pull request #13 from example/m"].refusal, "not-two-parents")

    def test_custom_pr_patterns(self):
        h = History(self.tmp / "repo")
        h.commit("init", {"lib/a.mjs": "1\n", "test/a.test.mjs": T})
        h.pr(5, {"lib/a.mjs": "2\n", "test/a.test.mjs": T + "//\n"}, subject="Merge PR #5 (abc)")
        h.pr(6, {"lib/a.mjs": "3\n", "test/a.test.mjs": T + "///\n"}, subject="Merge pull request #6 from example/train/x")
        root = h.write()
        cfg = config.load(recipe(self.tmp, '[repo]\nname = "example/x"\n[units]\npr_patterns = '
                                           "['^Merge PR #(\\d+) \\(', '^Merge pull request #(\\d+) from (?!example/train/)']\n"
                                 + NODE_PROFILE))
        by = {c.subject: c for c in scan.scan(Repo(str(root)), cfg)}
        self.assertEqual(by["Merge PR #5 (abc)"].pr, 5)
        self.assertEqual(by["Merge pull request #6 from example/train/x"].refusal, "not-a-pr")
