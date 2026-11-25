"""The agent skill and the Claude Code plugin: the Agent Skills format, one copy of the truth, the installer, versions."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import taskmine

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "taskmine"
PACKAGED = ROOT / "src" / "taskmine" / "skills" / "taskmine"


def frontmatter(text: str) -> dict[str, str]:
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    assert m, "SKILL.md starts with YAML frontmatter"
    out = {}
    for line in m.group(1).splitlines():
        key, _, value = line.partition(":")
        out[key.strip()] = value.strip()
    return out


class AgentSkillFormat(unittest.TestCase):
    """https://agentskills.io/specification: what Claude Code, Codex, Cursor and Kiro all accept."""

    def test_frontmatter_follows_the_spec(self):
        text = (SKILL / "SKILL.md").read_text()
        fm = frontmatter(text)
        self.assertEqual(set(fm), {"name", "description"}, "only the two fields every tool accepts")
        self.assertEqual(fm["name"], SKILL.name, "name matches the folder")
        self.assertRegex(fm["name"], r"^[a-z0-9]+(-[a-z0-9]+)*$")
        self.assertLessEqual(len(fm["name"]), 64)
        self.assertTrue(0 < len(fm["description"]) <= 1024, len(fm["description"]))
        self.assertLess(len(text.splitlines()), 500, "the body stays under the recommended 500 lines")

    def test_links_in_the_skill_resolve(self):
        for md in SKILL.rglob("*.md"):
            for target in re.findall(r"\]\(([^)#]+)\)", md.read_text()):
                if "://" not in target:
                    self.assertTrue((md.parent / target).exists(), f"{md.name} links {target}")

    def test_the_skill_names_only_real_commands(self):
        text = "\n".join(p.read_text() for p in SKILL.rglob("*.md"))
        out = subprocess.run([sys.executable, "-m", "taskmine", "--help"], capture_output=True, text=True,
                             env=dict(os.environ, PYTHONPATH=str(ROOT / "src"))).stdout
        commands = set(re.findall(r"^\s{4}([a-z]+)\s", out, re.M)) | set(re.search(r"\{([a-z,]+)\}", out).group(1).split(","))
        for used in set(re.findall(r"`taskmine ([a-z]+)", text)):
            self.assertIn(used, commands, f"the skill names `taskmine {used}`")


class RunsWithoutAnInstall(unittest.TestCase):
    def test_the_skill_pins_the_release_it_ships_in(self):
        pins = set(re.findall(r"github.com/didhd/taskmine@v([0-9.]+)", (SKILL / "SKILL.md").read_text()))
        self.assertEqual(pins, {taskmine.__version__}, "the uvx and uv tool lines name this release")


class OneCopy(unittest.TestCase):
    def test_the_packaged_skill_equals_the_repository_skill(self):
        a = {p.relative_to(SKILL): p.read_bytes() for p in SKILL.rglob("*") if p.is_file()}
        b = {p.relative_to(PACKAGED): p.read_bytes() for p in PACKAGED.rglob("*") if p.is_file() and "__pycache__" not in p.parts}
        self.assertEqual(sorted(a), sorted(b))
        for rel in a:
            self.assertEqual(a[rel], b[rel], f"src/taskmine/skills/taskmine/{rel} differs from skills/taskmine/{rel}")


class Installer(unittest.TestCase):
    def run_tm(self, cwd, *args):
        return subprocess.run([sys.executable, "-m", "taskmine", "skill", *args], cwd=cwd, capture_output=True, text=True,
                              env=dict(os.environ, PYTHONPATH=str(ROOT / "src")))

    def test_project_install_puts_one_copy_where_each_tool_reads(self):
        with tempfile.TemporaryDirectory() as d:
            p = self.run_tm(d, "install", "--project")
            self.assertEqual(p.returncode, 0, p.stderr)
            for where in (".claude/skills", ".agents/skills", ".kiro/skills"):
                self.assertEqual((Path(d) / where / "taskmine" / "SKILL.md").read_bytes(), (SKILL / "SKILL.md").read_bytes(), where)
                self.assertTrue((Path(d) / where / "taskmine" / "references" / "healing.md").exists())
            self.assertFalse((Path(d) / ".cursor").exists(), "Cursor reads .agents/skills: no second copy")
            self.assertNotIn(d, p.stdout, "paths are shown relative to the project")

    def test_dry_run_writes_nothing_and_unknown_tools_are_refused(self):
        with tempfile.TemporaryDirectory() as d:
            p = self.run_tm(d, "install", "--project", "--for", "kiro", "--dry-run")
            self.assertEqual(p.returncode, 0)
            self.assertIn(".kiro/skills/taskmine", p.stdout)
            self.assertEqual(list(Path(d).iterdir()), [])
            self.assertEqual(self.run_tm(d, "install", "--project", "--for", "vim").returncode, 2)


class PluginManifests(unittest.TestCase):
    def test_versions_agree_with_the_package(self):
        plugin = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())
        market = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text())
        self.assertEqual(plugin["name"], "taskmine")
        self.assertEqual(plugin["version"], taskmine.__version__)
        entry = next(p for p in market["plugins"] if p["name"] == "taskmine")
        self.assertEqual(entry["version"], taskmine.__version__)
        self.assertEqual(entry["source"], "./")


if __name__ == "__main__":
    unittest.main()
