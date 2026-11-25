"""The task's test.sh in PR #120's stages, run for real on a synthetic task.

The staged grader on AgentCore runs `test.sh --stage untrusted` as the agent user, checks that the
repository did not change, copies the stage's regular files (minus reward*) into a root-owned IN,
and runs `test.sh --stage trusted` as the grader user with IN and a fresh OUT; only OUT/reward.json
becomes Harbor's reward. These tests do the same steps with local processes: the agent's repository
is a real git repository at the base tag, the test runner is a small stdlib script in it.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from support import GitRepo, TempDir, needs_git

from taskmine import emit

TEMPLATES = Path(emit.__file__).resolve().parent / "templates"
TRUSTED_PY = next((p for p in ("/usr/bin/python3", "/usr/local/bin/python3")
                   if os.path.isfile(p) and os.stat(os.path.realpath(p)).st_uid == 0), None)
needs_trusted_python = __import__("unittest").skipUnless(
    TRUSTED_PY, "the trusted stage needs a root-owned /usr/bin/python3 or /usr/local/bin/python3")

# A test runner that writes JUnit, and a cache file into its working directory, as real runners do.
RUNNER = '''import sys, traceback
junit, files = sys.argv[1], sys.argv[2:]
open(".runner-cache", "w").write("cache")
cases = []
for f in files:
    ns = {"__name__": "t"}
    exec(compile(open(f).read(), f, "exec"), ns)
    for name in sorted(n for n in ns if n.startswith("test_")):
        try:
            ns[name]()
            cases.append('<testcase classname="t" name="%s" file="%s"/>' % (name, f))
        except Exception:
            cases.append('<testcase classname="t" name="%s" file="%s"><failure message="x"/></testcase>' % (name, f))
open(junit, "w").write("<testsuites>%s</testsuites>" % "".join(cases))
'''
HIDDEN = "import sys\nsys.path.insert(0, '.')\nimport calc\n\ndef test_add():\n    assert calc.add(2, 3) == 5\n\n" \
         "def test_mul():\n    assert calc.mul(2, 3) == 6\n"
GOLD = "def add(a, b):\n    return a + b\n\n\ndef mul(a, b):\n    return a * b\n"
EXPECTED = {"fail_to_pass": ["tests/test_calc.py::test_mul"], "pass_to_pass": ["tests/test_calc.py::test_add"]}


def tree_digest(root: Path) -> str:
    """Every file outside .git, its path, mode and bytes: what PR #120's root patch would see change."""
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root)
        if rel.parts[0] == ".git":
            continue
        h.update(str(rel).encode() + b"\0" + oct(p.lstat().st_mode).encode() + b"\0")
        if p.is_symlink():
            h.update(os.readlink(p).encode())
        elif p.is_file():
            h.update(p.read_bytes())
    return h.hexdigest()


@needs_git
class Stages(TempDir):
    def setUp(self) -> None:
        super().setUp()
        self.app = self.tmp / "app"
        r = GitRepo(self.app)
        r.write("calc.py", "def add(a, b):\n    return a + b\n")
        r.write("run_tests.py", RUNNER)
        r.write("tests/test_calc.py", "import sys\nsys.path.insert(0, '.')\nimport calc\n\ndef test_add():\n"
                                      "    assert calc.add(2, 3) == 5\n")
        r.write("tests/helper.py", "HELPER = 1\n")
        r.commit("base")
        r.git("tag", "taskmine-base")
        self.repo = r
        self.tests = self.tmp / "tests"
        (self.tests / "files" / "tests").mkdir(parents=True)
        shutil.copy2(TEMPLATES / "test.sh", self.tests / "test.sh")
        shutil.copy2(TEMPLATES / "grader.py", self.tests / "grader.py")
        (self.tests / "files" / "tests" / "test_calc.py").write_text(HIDDEN)
        (self.tests / "spec.json").write_text(json.dumps({
            "workdir": ".", "command": f"{sys.executable} run_tests.py {{junit}} {{files}}",
            "run_files": ["tests/test_calc.py"], "test_globs": ["tests/**"], "deleted": [], "timeout_sec": 60, "env": {}}))
        (self.tests / "expected.json").write_text(json.dumps(EXPECTED))

    def sh(self, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
        e = dict(os.environ, TASKMINE_APP=str(self.app))
        e.update(env or {})
        return subprocess.run(["bash", str(self.tests / "test.sh"), *args], cwd=self.app, env=e,
                              capture_output=True, text=True, timeout=120)

    def untrusted(self) -> Path:
        out = self.tmp / "untrusted-out"
        out.mkdir()
        before = tree_digest(self.app)
        p = self.sh("--stage", "untrusted", env={"OUT": str(out)})
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(tree_digest(self.app), before, "the untrusted stage changed the repository")
        self.assertFalse(list(out.glob("reward*")), "the untrusted stage wrote a reward")
        return out

    def root_copy(self, out: Path, plant: dict | None = None) -> Path:
        """What PR #120's root does between the stages: regular files only, never a reward* name."""
        inp = self.tmp / "in"
        inp.mkdir()
        for f in out.rglob("*"):
            if f.is_file() and not f.is_symlink() and not f.name.startswith("reward"):
                dest = inp / f.relative_to(out)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, dest)
        for name, text in (plant or {}).items():
            (inp / name).write_text(text)
        return inp

    def trusted(self, inp: Path, env: dict | None = None) -> tuple[dict | None, Path, subprocess.CompletedProcess]:
        out = self.tmp / "grade-out"
        out.mkdir(exist_ok=True)
        p = self.sh("--stage", "trusted", env={"IN": str(inp), "OUT": str(out), **(env or {})})
        reward = out / "reward.json"
        return (json.loads(reward.read_text()) if reward.exists() else None), out, p

    def oracle(self) -> None:
        self.repo.write("calc.py", GOLD)

    @needs_trusted_python
    def test_oracle_passes_and_nop_fails(self):
        nop, out, p = self.trusted(self.root_copy(self.untrusted()))
        self.assertEqual(nop, {"reward": 0.0}, p.stdout + p.stderr)
        self.assertFalse((out / "reward.txt").exists())
        shutil.rmtree(self.tmp / "untrusted-out")
        shutil.rmtree(self.tmp / "in")
        self.oracle()
        oracle, out, p = self.trusted(self.root_copy(self.untrusted()))
        self.assertEqual(oracle, {"reward": 1.0}, p.stdout + p.stderr)
        grade = json.loads((out / "grade.json").read_text())
        self.assertEqual((grade["stage"], grade["required"], grade["failing"]), ("trusted", 2, []))

    @needs_trusted_python
    def test_the_untrusted_stage_leaves_the_repository_as_the_agent_left_it(self):
        # The agent edited a test helper and added a test-side file; the grader restores both, but only in its copy.
        self.oracle()
        self.repo.write("tests/helper.py", "HELPER = 2\n")
        self.repo.write("tests/extra.py", "x = 1\n")
        out = self.untrusted()  # asserts the digest is unchanged
        self.assertEqual((self.app / "tests" / "helper.py").read_text(), "HELPER = 2\n")
        self.assertFalse((self.app / ".runner-cache").exists())
        run = json.loads((out / "run.json").read_text())
        self.assertIn("restored 1 and removed 1 test-side files the agent changed", " ".join(run["notes"]))
        self.assertTrue((out / "junit.xml").exists())

    @needs_trusted_python
    def test_nothing_the_agent_could_write_becomes_the_score(self):
        # A nop agent's evidence, with every result-shaped file planted in IN.
        planted = {"reward.json": '{"reward": 1.0}', "reward.txt": "1\n", "grade.json": '{"reward": 1}',
                   "results.json": json.dumps({"statuses": {t: "passed" for t in EXPECTED["fail_to_pass"]}})}
        inp = self.root_copy(self.untrusted(), planted)
        run = json.loads((inp / "run.json").read_text())
        (inp / "run.json").write_text(json.dumps({**run, "exit_code": 0}))
        out = self.tmp / "grade-out"
        out.mkdir()
        (out / "reward.json").write_text('{"reward": 1.0}')  # a stale reward is removed before grading
        reward, out, p = self.trusted(inp)
        self.assertEqual(reward, {"reward": 0.0}, p.stdout + p.stderr)
        self.assertEqual(sorted(x.name for x in out.glob("reward*")), ["reward.json"])

    @needs_trusted_python
    def test_a_forged_passing_duplicate_does_not_pass_a_failing_test(self):
        inp = self.root_copy(self.untrusted())
        junit = (inp / "junit.xml").read_text()
        # The forged passing copy comes first, so it takes the plain id and the real failing one becomes "#2".
        forged = junit.replace("<testsuites>", '<testsuites><testcase classname="t" name="test_mul" file="tests/test_calc.py"/>', 1)
        (inp / "junit.xml").write_text(forged)
        reward, out, p = self.trusted(inp)
        self.assertEqual(reward, {"reward": 0.0}, p.stdout + p.stderr)
        self.assertIn("tests/test_calc.py::test_mul", json.loads((out / "grade.json").read_text())["failing"])

    @needs_trusted_python
    def test_a_report_with_a_dtd_or_none_at_all_scores_0(self):
        self.oracle()
        inp = self.root_copy(self.untrusted())
        junit = (inp / "junit.xml").read_text()
        (inp / "junit.xml").write_text('<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]>' + junit)
        reward, out, _ = self.trusted(inp)
        self.assertEqual(reward, {"reward": 0.0})
        self.assertEqual(json.loads((out / "grade.json").read_text())["report"], "the JUnit report declares a DTD or entities")
        (inp / "junit.xml").unlink()
        reward, out, _ = self.trusted(inp)
        self.assertEqual(reward, {"reward": 0.0})
        self.assertEqual(json.loads((out / "grade.json").read_text())["report"], "no JUnit report from the untrusted stage")

    @needs_trusted_python
    def test_the_trusted_stage_reads_nothing_from_the_repository_or_path(self):
        self.oracle()
        inp = self.root_copy(self.untrusted())
        # The repository is gone, and the first python3 on PATH is one that would write reward 1.
        shutil.rmtree(self.app)
        self.app.mkdir()
        evil = self.tmp / "evil-bin"
        evil.mkdir()
        (evil / "python3").write_text('#!/bin/sh\necho \'{"reward": 1.0}\' > "$OUT/reward.json"; echo planted > "$OUT/planted"\n')
        (evil / "python3").chmod(0o755)
        reward, out, p = self.trusted(inp, env={"PATH": f"{evil}:{os.environ['PATH']}"})
        self.assertEqual(reward, {"reward": 1.0}, p.stdout + p.stderr)  # graded from the report and tests/files alone
        self.assertFalse((out / "planted").exists(), "the trusted stage ran a python3 from PATH")

    def test_an_unknown_stage_is_a_usage_error(self):
        p = self.sh("--stage", "both")
        self.assertEqual(p.returncode, 2)
        self.assertIn("usage: test.sh [--stage untrusted|trusted]", p.stderr)

    def test_harbors_own_verifier_still_runs_every_step_and_writes_reward_txt(self):
        logs = self.tmp / "logs"
        p = self.sh(env={"TASKMINE_LOGS": str(logs)})
        self.assertEqual((logs / "reward.txt").read_text(), "0\n", p.stdout + p.stderr)
        self.oracle()
        p = self.sh(env={"TASKMINE_LOGS": str(logs)})
        self.assertEqual((logs / "reward.txt").read_text(), "1\n", p.stdout + p.stderr)
        self.assertFalse((logs / "reward.json").exists())

    @needs_trusted_python
    def test_both_paths_give_the_same_test_ids(self):
        self.oracle()
        staged = json.loads((self.trusted(self.root_copy(self.untrusted()))[1] / "results.json").read_text())["statuses"]
        logs = self.tmp / "logs"
        self.sh(env={"TASKMINE_LOGS": str(logs)})
        self.assertEqual(json.loads((logs / "results.json").read_text())["statuses"], staged)

