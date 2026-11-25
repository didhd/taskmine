"""The grader that ships in every task, run the way Harbor runs it: `bash tests/test.sh` in a real git checkout."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from support import GIT_ENV, GitRepo, TempDir, needs_git

TEMPLATES = Path(__file__).resolve().parent.parent / "src" / "taskmine" / "templates"

JUNIT = """<?xml version="1.0"?>
<testsuites>
  <testsuite name="outer" file="t/a_test.py">
    <testsuite name="inner">
      <testcase name="passes"/>
      <testcase name="fails"><failure message="no"/></testcase>
    </testsuite>
    <testcase name="dup"/>
    <testcase name="dup"/>
    <testcase name="skips"><skipped/></testcase>
    <testcase name="errors"><error/></testcase>
  </testsuite>
  <testcase name="loose" file="{app}/t/b_test.py"/>
</testsuites>
"""

# A stand-in test runner: it writes the JUnit report it is given and records the files it was asked to run.
RUNNER = """import pathlib, sys
junit, files = sys.argv[1], sys.argv[2:]
pathlib.Path(junit).write_text(pathlib.Path(sys.argv[0]).with_name("report.xml").read_text())
pathlib.Path("ran.txt").write_text(" ".join(files))
"""


@needs_git
class Grader(TempDir):
    def setUp(self):
        super().setUp()
        self.app = GitRepo(self.tmp / "app")
        self.app.write("t/a_test.py", "base a\n")
        self.app.write("t/b_test.py", "base b\n")
        self.app.write("t/helper.py", "base helper\n")
        self.app.write("t/old_test.py", "deleted by the change\n")
        self.app.write("src/x.py", "base x\n")
        self.app.commit("base")
        self.app.git("tag", "taskmine-base")
        self.tests = self.tmp / "tests"
        shutil.copytree(TEMPLATES, self.tests, ignore=shutil.ignore_patterns("*.mjs", "Dockerfile.tmpl", "solve.sh"))
        (self.tests / "files" / "t").mkdir(parents=True)
        (self.tests / "files" / "t" / "a_test.py").write_text("hidden a\n")
        tool = self.tmp / "runner"
        tool.mkdir()
        (tool / "run.py").write_text(RUNNER)
        (tool / "report.xml").write_text(JUNIT.format(app=os.path.realpath(self.app.root)))
        self.spec = {"workdir": ".", "command": f"{sys.executable} {tool / 'run.py'} {{junit}} {{files}}",
                     "run_files": ["t/a_test.py", "t/b_test.py", "t/c_test.py"], "test_globs": ["t/**"],
                     "deleted": ["t/old_test.py"], "timeout_sec": 60, "env": {"GRADER_ENV": "1"}}
        self.logs = self.tmp / "logs"

    def grade(self, expected: dict | None = None) -> dict:
        (self.tests / "spec.json").write_text(json.dumps(self.spec))
        if expected is not None:
            (self.tests / "expected.json").write_text(json.dumps(expected))
        env = dict(GIT_ENV, TASKMINE_APP=str(self.app.root), TASKMINE_LOGS=str(self.logs))
        subprocess.run(["bash", str(self.tests / "test.sh")], env=env, check=True, capture_output=True)
        return json.loads((self.logs / "results.json").read_text())

    def reward(self) -> str:
        return (self.logs / "reward.txt").read_text().strip()

    def test_statuses_come_from_the_junit_report(self):
        res = self.grade()
        self.assertEqual(res["statuses"], {
            "t/a_test.py::outer::inner::passes": "passed",
            "t/a_test.py::outer::inner::fails": "failed",
            "t/a_test.py::outer::dup": "passed",
            "t/a_test.py::outer::dup #2": "passed",
            "t/a_test.py::outer::skips": "skipped",
            "t/a_test.py::outer::errors": "failed",
            "t/b_test.py::loose": "passed",
            "t/c_test.py::(no tests reported)": "failed",
        })
        self.assertEqual((res["exit_code"], res["timed_out"]), (0, False))
        self.assertEqual((self.app.root / "ran.txt").read_text(), "t/a_test.py t/b_test.py t/c_test.py")

    def test_a_calibration_run_records_statuses_and_rewards_nothing(self):
        self.grade()
        self.assertEqual(self.reward(), "0")
        self.assertFalse((self.logs / "grade.json").exists())

    def test_reward_one_only_when_every_required_test_passes(self):
        self.grade({"fail_to_pass": ["t/a_test.py::outer::inner::passes"], "pass_to_pass": ["t/b_test.py::loose"]})
        self.assertEqual(self.reward(), "1")
        self.grade({"fail_to_pass": ["t/a_test.py::outer::inner::passes", "t/a_test.py::outer::skips"],
                    "pass_to_pass": ["t/b_test.py::gone"]})
        self.assertEqual(self.reward(), "0")
        grade = json.loads((self.logs / "grade.json").read_text())
        self.assertEqual(grade, {"reward": 0, "required": 3, "failing": ["t/a_test.py::outer::skips", "t/b_test.py::gone"]})

    def test_an_empty_requirement_is_never_a_pass(self):
        self.grade({"fail_to_pass": [], "pass_to_pass": []})
        self.assertEqual(self.reward(), "0")

    def test_the_test_side_is_put_back_before_running(self):
        self.app.write("t/helper.py", "agent edited the helper\n")
        self.app.write("t/a_test.py", "agent's own version of the hidden test\n")
        self.app.write("t/new_test.py", "agent added a test\n")
        self.app.write("t/extra/more.py", "agent added a directory\n")
        self.app.write("src/x.py", "agent's fix\n")
        res = self.grade()
        self.assertEqual((self.app.root / "t/helper.py").read_text(), "base helper\n")
        self.assertEqual((self.app.root / "t/a_test.py").read_text(), "hidden a\n")
        self.assertFalse((self.app.root / "t/new_test.py").exists())
        self.assertFalse((self.app.root / "t/extra/more.py").exists())
        self.assertFalse((self.app.root / "t/old_test.py").exists())
        self.assertEqual((self.app.root / "src/x.py").read_text(), "agent's fix\n")
        self.assertEqual(res["notes"], ["restored 2 and removed 2 test-side files the agent changed"])

    def test_without_the_base_tag_nothing_is_restored_and_it_says_so(self):
        self.app.git("tag", "-d", "taskmine-base")
        self.app.write("t/helper.py", "agent edited the helper\n")
        res = self.grade()
        self.assertEqual(res["notes"], ["base tag missing: test-side files were not restored"])
        self.assertEqual((self.app.root / "t/helper.py").read_text(), "agent edited the helper\n")

    def test_a_run_past_its_timeout_is_killed_with_its_children(self):
        pidfile = self.tmp / "child.pid"
        self.spec["command"] = f"sleep 60 & echo $! > {pidfile}; wait"
        self.spec["timeout_sec"] = 1
        t0 = time.time()
        res = self.grade({"fail_to_pass": ["t/a_test.py::x"], "pass_to_pass": []})
        self.assertLess(time.time() - t0, 20)
        self.assertTrue(res["timed_out"])
        self.assertEqual(self.reward(), "0")
        pid = int(pidfile.read_text())
        for _ in range(50):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.1)
        else:
            self.fail("the runner's child survived the timeout")

    def test_the_profile_environment_reaches_the_runner(self):
        self.spec["command"] = "echo \"$GRADER_ENV\" > env.txt; true {junit} {files}"
        self.grade()
        self.assertEqual((self.app.root / "env.txt").read_text(), "1\n")
