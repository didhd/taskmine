"""The project workspace (.taskmine/) and the starter recipe `init` writes."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

from support import History, TempDir, needs_git, recipe

from taskmine import config, project, workflow


class Workspace(TempDir):
    def test_save_and_load_round_trip(self):
        root = self.tmp / ".taskmine"
        p = project.Project(root, "acme/shop", self.tmp / "clone", self.tmp / "taskmine.toml", root / "out", [
            {"name": "claude-code-sonnet-5-5", "agent": "claude-code", "provider": "bedrock",
             "model": "us.anthropic.claude-sonnet-5-5", "env": {"B": "2", "A": 'quote " here'}},
            {"name": "codex-x", "agent": "codex", "provider": "openai", "model": "gpt", "env": {}},
        ])
        project.save(p)
        back = project.load(root)
        self.assertEqual((back.repo, back.clone, back.recipe, back.out), (p.repo, p.clone, p.recipe, p.out))
        self.assertEqual(back.agents[0]["env"], {"A": 'quote " here', "B": "2"})
        self.assertNotIn("env", back.agents[1])
        self.assertEqual([a["name"] for a in back.agents], ["claude-code-sonnet-5-5", "codex-x"])
        text = (root / "agents.toml").read_text()
        self.assertTrue(text.startswith("# Agents to benchmark."))
        self.assertIn('env = { A = "quote \\" here", B = "2" }', text)
        self.assertEqual(tomllib.loads((root / "project.toml").read_text())["project"]["repo"], "acme/shop")

    def test_no_project_is_a_clear_error(self):
        with self.assertRaises(project.ProjectError) as cm:
            project.load(self.tmp / ".taskmine")
        self.assertIn("taskmine init", str(cm.exception))

    def test_a_missing_gh_is_a_clear_error(self):
        empty = self.tmp / "empty-path"
        empty.mkdir()
        old = os.environ["PATH"]
        os.environ["PATH"] = str(empty)
        try:
            with self.assertRaises(project.ProjectError) as cm:
                project.gh_user()
        finally:
            os.environ["PATH"] = old
        self.assertIn("https://cli.github.com", str(cm.exception))


@needs_git
class StarterRecipe(TempDir):
    def test_the_starter_recipe_loads_and_finds_each_runner(self):
        h = History(self.tmp / "clone")
        h.commit("init", {"test/a.test.mjs": "x", "web/test/b.test.js": "x", "pkg/tests/test_c.py": "x",
                          "requirements.txt": "pytest\n", "lib/x.mjs": "x"})
        h.write()
        text = workflow._starter_recipe("acme/shop", self.tmp / "clone")
        cfg = config.load(recipe(self.tmp, text))
        self.assertEqual((cfg.repo_name, cfg.github, cfg.units_kind, cfg.branch), ("acme/shop", "acme/shop", "merges", "main"))
        self.assertEqual([p.name for p in cfg.profiles], ["node-test", "node-web-test", "pytest"])
        node, web, py = cfg.profiles
        self.assertEqual(node.text_probe, "node")
        self.assertTrue(web.run.match("web/test/b.test.js"))
        self.assertTrue(py.run.match("pkg/tests/test_c.py"))
        self.assertIn("junit_family=xunit1", py.command)

    def test_a_repository_without_tests_gets_a_note_and_no_profile(self):
        h = History(self.tmp / "clone")
        h.commit("init", {"lib/x.mjs": "x"})
        h.write()
        text = workflow._starter_recipe("acme/shop", self.tmp / "clone")
        self.assertIn("No test runner found", text)
        with self.assertRaises(config.ConfigError):
            config.load(recipe(self.tmp, text))

    def test_tilde_shortens_home_paths(self):
        self.assertEqual(workflow.tilde(Path.home() / "x"), "~/x")
        self.assertEqual(workflow.tilde("/elsewhere/x"), "/elsewhere/x")
