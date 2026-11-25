"""The command line as a person runs it: a real `python -m taskmine` process, exit codes and output."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tomllib
import unittest
from pathlib import Path

from support import NODE_PROFILE, TempDir, recipe

import taskmine

ROOT = Path(__file__).resolve().parent.parent


def taskmine_cli(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
    return subprocess.run([sys.executable, "-m", "taskmine", *args], cwd=cwd, env=env, capture_output=True, text=True)


class Version(unittest.TestCase):
    def test_version_flag(self):
        p = taskmine_cli("--version")
        self.assertEqual((p.returncode, p.stdout), (0, f"taskmine {taskmine.__version__}\n"))

    def test_the_version_is_written_in_one_place(self):
        project = tomllib.loads((ROOT / "pyproject.toml").read_text())
        self.assertNotIn("version", project["project"])
        self.assertIn("version", project["project"]["dynamic"])
        self.assertEqual(project["tool"]["setuptools"]["dynamic"]["version"], {"attr": "taskmine.__version__"})

    def test_the_changelog_has_a_section_for_this_version(self):
        self.assertIn(f"\n## [{taskmine.__version__}] - ", (ROOT / "CHANGELOG.md").read_text())


class Help(unittest.TestCase):
    def test_help_lists_every_command(self):
        p = taskmine_cli("--help")
        self.assertEqual(p.returncode, 0)
        for cmd in ("login", "init", "agents", "gateway", "mine", "verify", "bench", "results",
                    "scan", "meta", "build", "validate", "finalize", "check", "report"):
            self.assertIn(cmd, p.stdout)

    def test_no_command_is_a_usage_error(self):
        p = taskmine_cli()
        self.assertEqual(p.returncode, 2)
        self.assertIn("usage: taskmine", p.stderr)


class Errors(TempDir):
    def test_a_recipe_error_is_one_line_and_exit_2(self):
        bad = recipe(self.tmp, '[repo]\nname = "x"\n' + NODE_PROFILE.replace('run = ["test/*.test.mjs"]', 'run = "test/*"'))
        p = taskmine_cli("scan", "--recipe", str(bad), "--repo", str(self.tmp), "--out", str(self.tmp / "out"))
        self.assertEqual(p.returncode, 2)
        self.assertEqual(p.stderr.count("\n"), 1)
        self.assertTrue(p.stderr.startswith("recipe error: "), p.stderr)
        self.assertIn("run must be a list of strings", p.stderr)

    def test_an_everyday_command_outside_a_project(self):
        p = taskmine_cli("mine", cwd=self.tmp)
        self.assertEqual(p.returncode, 2)
        self.assertEqual(p.stderr, "taskmine: no project here: run `taskmine init <owner/repo>` first\n")

    def test_check_on_an_empty_directory_passes_nothing(self):
        p = taskmine_cli("check", str(self.tmp))
        self.assertEqual((p.returncode, p.stdout), (0, "0 of 0 tasks pass the static checks\n"))

    def test_check_reports_a_broken_task_and_fails(self):
        t = self.tmp / "tasks" / "shop-pr1"
        t.mkdir(parents=True)
        (t / "task.toml").write_text("version = '1.0'\n")
        p = taskmine_cli("check", str(self.tmp / "tasks"))
        self.assertEqual(p.returncode, 1)
        self.assertIn("FAIL shop-pr1\n     missing instruction.md\n", p.stdout)
        self.assertTrue(p.stdout.endswith("0 of 1 tasks pass the static checks\n"))

    def test_report_writes_markdown_and_json(self):
        out = self.tmp / "out"
        out.mkdir()
        (out / "candidates.jsonl").write_text(json.dumps({"id": "x-pr1", "refusal": None}) + "\n")
        p = taskmine_cli("report", "--out", str(out))
        self.assertEqual(p.returncode, 0)
        self.assertEqual((out / "report.md").read_text(), p.stdout[:-1])
        self.assertEqual(json.loads((out / "report.json").read_text())["candidates"], 1)


if __name__ == "__main__":
    unittest.main()
