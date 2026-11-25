"""The recipe loader: defaults, and every way a recipe is refused with a message that names the problem."""

from __future__ import annotations

from pathlib import Path

from support import NODE_PROFILE, TempDir, recipe

from taskmine import config

REPO = '[repo]\nname = "example/shop"\n'
ROOT = Path(__file__).resolve().parent.parent


class Defaults(TempDir):
    def test_a_minimal_recipe_gets_the_documented_defaults(self):
        cfg = config.load(recipe(self.tmp, REPO + NODE_PROFILE))
        self.assertEqual((cfg.repo_name, cfg.github, cfg.units_kind, cfg.branch, cfg.since),
                         ("example/shop", None, "merges", "main", None))
        self.assertEqual(cfg.pr_patterns, [r"^Merge pull request #(\d+) from ", r"\(#(\d+)\)$"])
        self.assertEqual((cfg.max_file_bytes, cfg.max_gold_files, cfg.max_gold_lines, cfg.max_run_files),
                         (5_000_000, 40, 2000, 6))
        self.assertEqual((cfg.agent_timeout_sec, cfg.storage_mb, cfg.include_issue, cfg.issue_max_chars),
                         (3600.0, 20480, True, 4000))
        self.assertFalse(cfg.allow_source_text)
        self.assertTrue(cfg.tests.match("pkg/tests/test_x.py"))
        self.assertTrue(cfg.docs.match("docs/guide/intro.rst"))
        self.assertIn("png", cfg.binary_ext)
        p = cfg.profile("node-test")
        self.assertEqual((p.workdir, p.install, p.deps, p.deps_normalize, p.text_probe), (".", None, [], None, "static"))
        self.assertEqual((p.timeout_sec, p.cpus, p.memory_mb, p.env, p.setup), (900.0, 2, 4096, {}, None))
        self.assertEqual(p.display_command, "node --test {files}")

    def test_every_key_is_read(self):
        body = REPO + """github = "example/shop"
[units]
kind = "commits"
branch = "trunk"
since = "2026-01-01"
pr_patterns = ['\\(#(\\d+)\\)$']
[snapshot]
max_file_bytes = 10
exclude = ["media/**"]
binary_ext = ["PNG"]
[tests]
paths = ["spec/**"]
docs = ["*.md"]
allow_source_text = true
[limits]
max_gold_files = 1
max_gold_lines = 2
max_run_files = 3
max_changed_file_bytes = 4
[task]
agent_timeout_sec = 60
storage_mb = 100
include_issue = false
issue_max_chars = 50
[[profiles]]
name = "p"
run = ["spec/*.py"]
image = "python:3.12"
workdir = "pkg"
command = "pytest --junitxml={junit} {files}"
install = "pip install ."
deps = ["pyproject.toml"]
deps_normalize = "npm"
timeout_sec = 30
cpus = 4
memory_mb = 512
env = { A = "1", B = 2 }
setup = "apt-get install -y x"
text_probe = "off"
"""
        cfg = config.load(recipe(self.tmp, body))
        self.assertEqual((cfg.github, cfg.units_kind, cfg.branch, cfg.since), ("example/shop", "commits", "trunk", "2026-01-01"))
        self.assertEqual(cfg.binary_ext, frozenset({"png"}))
        self.assertTrue(cfg.snapshot_exclude.match("media/a/b.mp4"))
        self.assertEqual((cfg.max_gold_files, cfg.max_gold_lines, cfg.max_run_files, cfg.max_changed_file_bytes), (1, 2, 3, 4))
        self.assertEqual((cfg.agent_timeout_sec, cfg.storage_mb, cfg.include_issue, cfg.issue_max_chars), (60.0, 100, False, 50))
        self.assertTrue(cfg.allow_source_text)
        p = cfg.profiles[0]
        self.assertEqual((p.workdir, p.install, p.deps, p.deps_normalize, p.timeout_sec, p.cpus, p.memory_mb),
                         ("pkg", "pip install .", ["pyproject.toml"], "npm", 30.0, 4, 512))
        self.assertEqual(p.env, {"A": "1", "B": "2"})
        self.assertEqual(p.text_probe, "off")

    def test_the_shipped_example_recipes_load(self):
        examples = sorted(ROOT.glob("examples/*/taskmine.toml"))
        self.assertTrue(examples)
        for path in examples:
            with self.subTest(path=path.name):
                self.assertTrue(config.load(path).profiles)


class Refusals(TempDir):
    def refused(self, body: str, *needles: str) -> str:
        with self.assertRaises(config.ConfigError) as cm:
            config.load(recipe(self.tmp, body))
        msg = str(cm.exception)
        for n in needles:
            self.assertIn(n, msg)
        return msg

    def test_unknown_keys_are_errors_at_every_level(self):
        self.refused(REPO + NODE_PROFILE + "[extra]\n", "unknown key(s) extra")
        self.refused(REPO + "[snapshot]\nexclued = []\n" + NODE_PROFILE, "[snapshot]", "exclued", "allowed: binary_ext")
        self.refused(REPO + NODE_PROFILE + "workdr = 'x'\n", "[[profiles]] #1", "workdr")

    def test_required_parts(self):
        self.refused(NODE_PROFILE, "[repo] name is required")
        self.refused(REPO, "at least one [[profiles]] entry")
        self.refused(REPO + '[[profiles]]\nname = "p"\nrun = ["t/*"]\nimage = "i"\n', "'command' is required")

    def test_bad_values(self):
        self.refused(REPO + '[units]\nkind = "squash"\n' + NODE_PROFILE, "kind must be 'merges' or 'commits'")
        self.refused(REPO + '[[profiles]]\nname = "p"\nrun = ["t/*"]\nimage = "i"\ncommand = "run {files}"\n',
                     "{junit}")
        self.refused(REPO + NODE_PROFILE + 'text_probe = "always"\n', "text_probe must be")
        self.refused(REPO + NODE_PROFILE + 'deps_normalize = "yarn"\n', "deps_normalize must be")

    def test_a_string_where_a_list_belongs_is_refused(self):
        # Read as a list of characters, `run = "test/*.test.mjs"` made `*` a glob of its own: every
        # top-level file counted as a runnable test.
        prof = NODE_PROFILE.replace('run = ["test/*.test.mjs"]', 'run = "test/*.test.mjs"')
        self.refused(REPO + prof, "run must be a list of strings")
        self.refused(REPO + '[snapshot]\nexclude = "media/**"\n' + NODE_PROFILE, "[snapshot]", "exclude must be a list")
        self.refused(REPO + '[tests]\npaths = "spec/**"\n' + NODE_PROFILE, "paths must be a list")
        self.refused(REPO + NODE_PROFILE + 'deps = "package.json"\n', "deps must be a list")
        self.refused(REPO + '[units]\npr_patterns = "^Merge (\\\\d+)"\n' + NODE_PROFILE, "pr_patterns must be a list")

    def test_wrong_types_are_recipe_errors_not_crashes(self):
        self.refused("repo = 5\n" + NODE_PROFILE, "[repo] must be a table")
        self.refused(REPO + '[profiles]\nname = "p"\n', "array of tables")
        self.refused(REPO + NODE_PROFILE + 'cpus = "two"\n', "cpus must be an integer, not 'two'")
        self.refused(REPO + NODE_PROFILE + "timeout_sec = true\n", "timeout_sec must be a number")
        self.refused(REPO + NODE_PROFILE + 'env = ["A=1"]\n', "env must be a table")
        self.refused(REPO + '[task]\ninclude_issue = "no"\n' + NODE_PROFILE, "include_issue must be true or false")
        self.refused('[repo]\nname = 5\n' + NODE_PROFILE, "name must be a string")
        self.refused(REPO + 'github = "shop"\n' + NODE_PROFILE, "github must be owner/name")

    def test_pr_patterns_are_checked_when_the_recipe_loads(self):
        self.refused(REPO + "[units]\npr_patterns = ['(unclosed']\n" + NODE_PROFILE, "not a regular expression")
        self.refused(REPO + "[units]\npr_patterns = ['^Merge']\n" + NODE_PROFILE, "needs a group")

    def test_an_unquoted_toml_date_is_accepted_for_since(self):
        cfg = config.load(recipe(self.tmp, REPO + "[units]\nsince = 2026-06-01\n" + NODE_PROFILE))
        self.assertEqual(cfg.since, "2026-06-01")

    def test_unreadable_or_invalid_toml(self):
        with self.assertRaises(config.ConfigError):
            config.load(self.tmp / "missing.toml")
        self.refused("[repo\nname = 1\n", str(self.tmp / "taskmine.toml"))

    def test_an_unknown_profile_name(self):
        cfg = config.load(recipe(self.tmp, REPO + NODE_PROFILE))
        with self.assertRaises(config.ConfigError):
            cfg.profile("nope")
