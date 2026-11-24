"""End to end on a real git repository with real merges and a real test runner (node --test).

Nothing is faked: the repository is created with git, the tests run under Node, the grader runs
the way Harbor runs it (bash tests/test.sh), and the verdicts come from those executions. Docker
is the only thing replaced, by the local executor; the Docker path is exercised on a real
dataset (see docs/DOGFOOD.md).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from taskmine import cli, emit
from taskmine.check import check_task

RECIPE = """
[repo]
name = "example/calc"

[units]
kind = "merges"
branch = "main"

[[profiles]]
name = "node-test"
run = ["test/*.test.mjs"]
image = "node:24-bookworm"
command = "node --test --test-reporter=junit --test-reporter-destination={junit} --test-reporter=spec --test-reporter-destination=stdout {files}"
display_command = "node --test {files}"
timeout_sec = 120
"""

GIT_ENV = dict(
    os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com", GIT_COMMITTER_NAME="t",
    GIT_COMMITTER_EMAIL="t@example.com", GIT_AUTHOR_DATE="2026-01-01T00:00:00Z",
    GIT_COMMITTER_DATE="2026-01-01T00:00:00Z", GIT_CONFIG_NOSYSTEM="1", HOME=tempfile.gettempdir(),
)


def sh(cwd: Path, *args: str) -> str:
    return subprocess.run(args, cwd=cwd, env=GIT_ENV, check=True, capture_output=True, text=True).stdout


def write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def make_repo(root: Path) -> None:
    sh(root, "git", "init", "-q", "-b", "main")
    write(root, "lib/calc.mjs", "export function add(a, b) {\n  return a + b;\n}\n")
    write(root, "test/calc.test.mjs",
          "import { test } from 'node:test';\nimport assert from 'node:assert/strict';\n"
          "import { add } from '../lib/calc.mjs';\n\n"
          "test('adds two numbers', () => assert.equal(add(2, 3), 5));\n")
    write(root, "README.md", "# calc\n")
    sh(root, "git", "add", "-A")
    sh(root, "git", "commit", "-q", "-m", "init")

    # PR 1: a new function and its test (fail-to-pass), plus one more test in an existing file
    # that already passes (pass-to-pass).
    sh(root, "git", "checkout", "-q", "-b", "mul")
    write(root, "lib/calc.mjs",
          "export function add(a, b) {\n  return a + b;\n}\n\n"
          "export function multiplyExact(a, b) {\n  if (!Number.isInteger(a) || !Number.isInteger(b)) "
          "throw new RangeError('multiplyExact needs integers');\n  return a * b;\n}\n")
    write(root, "test/mul.test.mjs",
          "import { test } from 'node:test';\nimport assert from 'node:assert/strict';\n"
          "import * as calc from '../lib/calc.mjs';\n\n"
          "test('multiplies two integers', () => assert.equal(calc.multiplyExact(4, 5), 20));\n"
          "test('refuses a fraction', () => assert.throws(() => calc.multiplyExact(1.5, 2), "
          "{ message: 'multiplyExact needs integers' }));\n")
    write(root, "test/calc.test.mjs",
          (root / "test/calc.test.mjs").read_text() + "test('adds negatives', () => assert.equal(add(-2, -3), -5));\n")
    sh(root, "git", "add", "-A")
    sh(root, "git", "commit", "-q", "-m", "feat: multiplyExact")
    sh(root, "git", "checkout", "-q", "main")
    sh(root, "git", "merge", "-q", "--no-ff", "mul", "-m", "Merge pull request #1 from example/mul")

    # PR 2: documentation only, with a test that already passes: nothing to ask for.
    sh(root, "git", "checkout", "-q", "-b", "docs")
    write(root, "README.md", "# calc\n\nAdds and multiplies.\n")
    write(root, "test/readme.test.mjs", "import { test } from 'node:test';\ntest('ok', () => {});\n")
    sh(root, "git", "add", "-A")
    sh(root, "git", "commit", "-q", "-m", "docs")
    sh(root, "git", "checkout", "-q", "main")
    sh(root, "git", "merge", "-q", "--no-ff", "docs", "-m", "Merge pull request #2 from example/docs")

    # PR 3: a code change whose new test already passes before it: no fail-to-pass test.
    sh(root, "git", "checkout", "-q", "-b", "refactor")
    write(root, "lib/calc.mjs", (root / "lib/calc.mjs").read_text().replace("return a + b;", "return b + a;"))
    write(root, "test/commute.test.mjs",
          "import { test } from 'node:test';\nimport assert from 'node:assert/strict';\n"
          "import { add } from '../lib/calc.mjs';\n\ntest('add commutes', () => assert.equal(add(1, 2), add(2, 1)));\n")
    sh(root, "git", "add", "-A")
    sh(root, "git", "commit", "-q", "-m", "refactor")
    sh(root, "git", "checkout", "-q", "main")
    sh(root, "git", "merge", "-q", "--no-ff", "refactor", "-m", "Merge pull request #3 from example/refactor")


@unittest.skipUnless(shutil.which("node") and shutil.which("git"), "needs node and git")
class EndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="taskmine-e2e-"))
        cls.repo = cls.tmp / "repo"
        cls.repo.mkdir()
        make_repo(cls.repo)
        cls.recipe = cls.tmp / "taskmine.toml"
        cls.recipe.write_text(RECIPE)
        cls.out = cls.tmp / "out"
        r = ["--recipe", str(cls.recipe), "--out", str(cls.out)]
        assert cli.main(["scan", *r, "--repo", str(cls.repo)]) == 0
        assert cli.main(["build", *r, "--repo", str(cls.repo), "--jobs", "1"]) == 0
        assert cli.main(["validate", *r, "--executor", "local", "--jobs", "1",
                         "--scratch", str(cls.tmp / "scratch")]) == 0
        assert cli.main(["finalize", *r]) == 0

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def cands(self):
        return {json.loads(l)["unit"]: json.loads(l) for l in (self.out / "candidates.jsonl").read_text().splitlines()}

    def test_scan_splits_test_side_from_gold_side(self):
        c = self.cands()
        self.assertIsNone(c["pr1"]["refusal"])
        self.assertEqual(c["pr1"]["run_files"], ["test/calc.test.mjs", "test/mul.test.mjs"])
        self.assertEqual(c["pr1"]["gold_files"], ["lib/calc.mjs"])
        self.assertEqual(c["pr2"]["refusal"], "no-code-change")
        self.assertIsNone(c["pr3"]["refusal"])

    def test_validation_decides_by_execution(self):
        v1 = json.loads((self.out / "validation" / "calc-pr1.json").read_text())
        self.assertEqual(v1["verdict"], "accepted", v1)
        self.assertEqual(v1["fail_to_pass"], [
            "test/mul.test.mjs::multiplies two integers",
            "test/mul.test.mjs::refuses a fraction",
        ])
        self.assertEqual(v1["pass_to_pass"], ["test/calc.test.mjs::adds negatives", "test/calc.test.mjs::adds two numbers"])
        kinds = [r["kind"] for r in v1["runs"]]
        self.assertEqual(kinds, ["base", "gold", "base", "gold", "gold", "nop", "oracle"])
        self.assertEqual([r["reward"] for r in v1["runs"] if r["kind"] in ("nop", "oracle")], [0.0, 1.0])
        v3 = json.loads((self.out / "validation" / "calc-pr3.json").read_text())
        self.assertEqual(v3["verdict"], "rejected")
        self.assertEqual(v3["reason"], "no-fail-to-pass")

    def test_finished_task_is_harbor_shaped_and_passes_check(self):
        task = self.out / "tasks" / "calc-pr1"
        self.assertEqual(check_task(task), [])
        self.assertFalse((self.out / "tasks" / "calc-pr3").exists())
        text = (task / "instruction.md").read_text()
        self.assertIn("multiplies two integers", text)
        self.assertIn("`multiplyExact`", text)  # a new name the hidden tests use
        self.assertIn("`multiplyExact needs integers`", text)  # exact text they check
        self.assertNotIn("Number.isInteger", text)  # but not the implementation

    def test_snapshot_holds_the_base_and_not_the_answer(self):
        import tarfile
        with tarfile.open(self.out / "tasks" / "calc-pr1" / "environment" / "repo.tar.gz") as tar:
            names = tar.getnames()
            calc = tar.extractfile("lib/calc.mjs").read().decode()
        self.assertNotIn("test/mul.test.mjs", names)
        self.assertNotIn("multiplyExact", calc)
        self.assertFalse(any(n == ".git" or n.startswith(".git/") for n in names))

    def test_drafts_are_reproducible(self):
        other = self.tmp / "rebuild"
        r = ["--recipe", str(self.recipe), "--out", str(other)]
        other.mkdir()
        shutil.copy(self.out / "candidates.jsonl", other / "candidates.jsonl")
        self.assertEqual(cli.main(["build", *r, "--repo", str(self.repo), "--ids", "calc-pr1"]), 0)
        a = json.loads((self.out / "drafts" / "calc-pr1" / "draft.json").read_text())["draft_digest"]
        b = emit.draft_digest(other / "drafts" / "calc-pr1")
        self.assertEqual(a, b)

    def test_grader_restores_tests_an_agent_edited(self):
        """An agent that writes a passing fake of the hidden test, and a wrong implementation, scores 0."""
        task = self.out / "tasks" / "calc-pr1"
        app = self.tmp / "cheat" / "app"
        app.mkdir(parents=True)
        import tarfile
        with tarfile.open(task / "environment" / "repo.tar.gz") as tar:
            tar.extractall(app, filter="tar")
        sh(app, "bash", "-c", "git init -q && git add -A && git commit -q -m base && git tag taskmine-base")
        write(app, "lib/calc.mjs", (app / "lib/calc.mjs").read_text() + "export const multiplyExact = () => 0;\n")
        write(app, "test/mul.test.mjs", "import { test } from 'node:test';\ntest('multiplies two integers', () => {});\n"
              "test('refuses a fraction', () => {});\n")
        write(app, "test/calc.test.mjs", "import { test } from 'node:test';\ntest('adds two numbers', () => {});\n"
              "test('adds negatives', () => {});\n")
        logs = self.tmp / "cheat" / "logs"
        env = dict(os.environ, TASKMINE_APP=str(app), TASKMINE_LOGS=str(logs))
        subprocess.run(["bash", str(task / "tests" / "test.sh")], env=env, check=True, capture_output=True)
        self.assertEqual((logs / "reward.txt").read_text().strip(), "0")
        grade = json.loads((logs / "grade.json").read_text())
        self.assertIn("test/mul.test.mjs::multiplies two integers", grade["failing"])
        self.assertNotIn("test/calc.test.mjs::adds negatives", grade["failing"])


if __name__ == "__main__":
    unittest.main()
