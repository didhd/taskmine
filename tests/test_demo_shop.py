"""The synthetic demo repository (demo/make_shop.py) through the real pipeline, and the text probe failing closed.

The demo repository holds the cases a reader of the demo should see decided: four pull requests
that become tasks, one documentation change left out, and one whose only new test reads source
text. The local executor runs here with a relative scratch directory, the way a person types it.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which("node") and shutil.which("git"), "needs node and git")
class DemoShop(unittest.TestCase):
    def test_demo_repository_decisions(self):
        tmp = Path(tempfile.mkdtemp(prefix="taskmine-shop-"))
        env = dict(os.environ, PYTHONPATH=str(ROOT / "src"))
        try:
            def tm(*args):
                return subprocess.run([sys.executable, "-m", "taskmine", *args], cwd=tmp, env=env,
                                      capture_output=True, text=True, check=True).stdout

            subprocess.run([sys.executable, str(ROOT / "demo" / "make_shop.py"), "shop"], cwd=tmp, check=True,
                           capture_output=True)
            r = ["--recipe", str(ROOT / "demo" / "shop.toml"), "--out", "out"]
            tm("scan", *r, "--repo", "shop")
            tm("build", *r, "--repo", "shop", "--jobs", "2")
            tm("validate", *r, "--executor", "local", "--jobs", "2", "--scratch", "scratch")  # relative on purpose
            verdicts = {p.stem: json.loads(p.read_text()) for p in (tmp / "out" / "validation").glob("*.json")}
            self.assertEqual({k: v["verdict"] for k, v in verdicts.items()},
                             {"shop-pr1": "accepted", "shop-pr2": "accepted", "shop-pr4": "accepted",
                              "shop-pr5": "accepted", "shop-pr6": "rejected"})
            self.assertEqual(verdicts["shop-pr6"]["reason"], "source-text-only")
            self.assertEqual(verdicts["shop-pr6"]["text_probe_substituted"], ["lib/cart.mjs"])
            self.assertEqual(verdicts["shop-pr2"]["fail_to_pass"], ["test/tax.test.mjs::rounds each line to the cent before adding"])
            cands = [json.loads(line) for line in (tmp / "out" / "candidates.jsonl").read_text().splitlines()]
            self.assertEqual({c["unit"]: c["refusal"] for c in cands if c["refusal"]}, {"pr3": "no-test-change"})
            # No GitHub metadata: the title is the pull request's, from the merge commit's body.
            tm("finalize", *r)
            ins = (tmp / "out" / "tasks" / "shop-pr1" / "instruction.md").read_text()
            self.assertTrue(ins.startswith("# Add discount codes (SAVE10, HALF)\n"), ins[:80])
            self.assertIn('"unknown code: FREE"', ins)  # node's doubly escaped JUnit names, shown as written
            self.assertNotIn("&quot;", ins)
            names = ins.split("## Names the tests use that do not exist yet", 1)[1].split("##", 1)[0]
            self.assertNotIn("`unknown`", names)  # the words of a regular expression literal are not names
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_init_on_a_local_clone_needs_no_github_and_shows_no_paths(self):
        tmp = Path(tempfile.mkdtemp(prefix="taskmine-init-"))
        try:
            subprocess.run([sys.executable, str(ROOT / "demo" / "make_shop.py"), "shop"], cwd=tmp, check=True,
                           capture_output=True)
            out = subprocess.run([sys.executable, "-m", "taskmine", "init", "acme/shop", "--clone", "shop",
                                  "--recipe", str(ROOT / "demo" / "shop.toml")], cwd=tmp, capture_output=True, text=True,
                                 env=dict(os.environ, PYTHONPATH=str(ROOT / "src"))).stdout
            self.assertIn("(local clone)", out)
            self.assertIn("clone    shop  (6 merge commits on main)", out)
            self.assertNotIn("GitHub account", out)
            self.assertNotIn(str(tmp), out)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class RegexLiterals(unittest.TestCase):
    def test_a_regular_expression_is_neither_names_nor_text_and_division_stays_code(self):
        from taskmine import contract
        code, strings = contract.split_source(
            "t.test.mjs", "assert.throws(() => f(), /unknown code/);\nconst x = a / b / c;\n"
                          "s.match(/[a/b]+/g);\nreturn /x/.test(y);\nassert.equal(f('half'), 1);")
        self.assertNotIn("unknown", code)
        self.assertIn("a / b / c", code)
        self.assertNotIn("[a", code)
        self.assertIn(".test(y)", code)
        self.assertEqual(strings, [("half", False)])


@unittest.skipUnless(shutil.which("node"), "needs node")
class ProbeFailsClosed(unittest.TestCase):
    def test_probe_logs_an_error_when_it_cannot_list_changed_files(self):
        tmp = Path(tempfile.mkdtemp(prefix="taskmine-probe-"))
        try:
            (tmp / "t.mjs").write_text("import fs from 'node:fs'; fs.readFileSync(new URL(import.meta.url));\n")
            log = tmp / "probe.log"
            env = dict(os.environ, NODE_OPTIONS=f"--import {ROOT / 'src/taskmine/templates/textprobe.mjs'}",
                       TASKMINE_APP_DIR=str(tmp / "not-a-repository"), TASKMINE_TEST_GLOBS='["**/*.mjs"]',
                       TASKMINE_PROBE_LOG=str(log))
            subprocess.run(["node", str(tmp / "t.mjs")], env=env, capture_output=True, timeout=60)
            self.assertTrue(log.read_text().startswith("!error "), log.read_text() if log.exists() else "no log")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
