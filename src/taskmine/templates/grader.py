#!/usr/bin/env python3
"""taskmine grader: runs inside the task container after the agent, and writes the reward.

Written for any python3 >= 3.7 the container has; the standard library only.

1. The reward is written as 0 before anything else, so a crash anywhere below fails the task
   instead of leaving a reward an agent could have planted.
2. The test side is put back. Every test-side path (spec.json `test_globs`) that differs from the
   base commit (tag `taskmine-base`, created when the image was built) is restored from it, or
   removed if the agent created it. Then the hidden test files (`files/`) are copied in and the
   test files the change deleted are removed. An agent therefore cannot pass by editing tests or
   test helpers; it can still tamper with the runtime itself (node_modules, the interpreter), which
   a verifier sharing the agent's container cannot rule out.
3. The profile's command runs and writes a JUnit XML report.
4. The report becomes one status per test id: `<file>::<suite>::...::<test name>`.
5. With `expected.json`, the reward is 1 only if every fail-to-pass and pass-to-pass test passed.
   Without it (calibration, while the task is being validated) the statuses are recorded and the
   reward stays 0.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

BASE_TAG = "taskmine-base"
NO_TESTS = "(no tests reported)"


def compile_glob(pattern):
    out, i = [], 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("/**", i) and i + 3 == len(pattern):
            out.append("(?:/.*)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("^" + "".join(out) + "$")


def git(app, *args, check=False):
    return subprocess.run(
        ["git", "--literal-pathspecs", "-C", app, *args],
        capture_output=True,
        check=check,
        env=dict(os.environ, GIT_PAGER="cat", LC_ALL="C"),
    )


def restore_test_side(app, globs, notes):
    patterns = [compile_glob(g) for g in globs]
    is_test = lambda p: any(c.match(p) for c in patterns)
    if git(app, "rev-parse", "--verify", "-q", BASE_TAG + "^{commit}").returncode != 0:
        notes.append("base tag missing: test-side files were not restored")
        return
    changed = git(app, "diff", "--name-only", "-z", "--no-renames", BASE_TAG).stdout.split(b"\0")
    untracked = git(app, "ls-files", "--others", "--exclude-standard", "-z").stdout.split(b"\0")
    restore, remove = [], []
    for raw in changed:
        p = raw.decode("utf-8", "surrogateescape")
        if p and is_test(p):
            if git(app, "cat-file", "-e", BASE_TAG + ":" + p).returncode == 0:
                restore.append(p)
            else:
                remove.append(p)
    for raw in untracked:
        p = raw.decode("utf-8", "surrogateescape")
        if p and is_test(p):
            remove.append(p)
    for i in range(0, len(restore), 200):
        git(app, "checkout", BASE_TAG, "--", *restore[i : i + 200])
    for p in remove:
        full = os.path.join(app, p)
        if os.path.isdir(full) and not os.path.islink(full):
            shutil.rmtree(full, ignore_errors=True)
        elif os.path.lexists(full):
            os.remove(full)
    if restore or remove:
        notes.append("restored %d and removed %d test-side files the agent changed" % (len(restore), len(remove)))


def install_hidden_tests(tests_dir, app, deleted):
    src_root = os.path.join(tests_dir, "files")
    for root, _dirs, files in os.walk(src_root):
        for name in files:
            src = os.path.join(root, name)
            rel = os.path.relpath(src, src_root)
            dst = os.path.join(app, rel)
            if os.path.lexists(dst) and (os.path.isdir(dst) and not os.path.islink(dst)):
                shutil.rmtree(dst)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copyfile(src, dst)
            shutil.copymode(src, dst)
    for rel in deleted:
        full = os.path.join(app, rel)
        if os.path.lexists(full):
            os.remove(full)


def run_command(spec, app, logs):
    workdir = os.path.normpath(os.path.join(app, spec.get("workdir") or "."))
    junit = os.path.join(logs, "junit.xml")
    if os.path.exists(junit):
        os.remove(junit)
    files = " ".join(shlex.quote(os.path.relpath(os.path.join(app, f), workdir)) for f in spec["run_files"])
    cmd = spec["command"].replace("{files}", files).replace("{junit}", shlex.quote(junit))
    env = dict(os.environ)
    env.update(spec.get("env") or {})
    started = time.time()
    with open(os.path.join(logs, "test-output.log"), "wb") as out:
        out.write(("$ cd %s && %s\n" % (workdir, cmd)).encode())
        out.flush()
        proc = subprocess.Popen(
            ["bash", "-c", cmd], cwd=workdir, stdout=out, stderr=subprocess.STDOUT, env=env,
            start_new_session=True,
        )
        timed_out = False
        try:
            code = proc.wait(timeout=float(spec.get("timeout_sec", 900)))
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                pass
            code = proc.wait()
    return junit, code, timed_out, round(time.time() - started, 2)


def parse_junit(path, app):
    statuses = {}
    if not os.path.exists(path):
        return statuses
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError:
        return statuses

    def rel(file_attr):
        if not file_attr:
            return None
        p = file_attr if os.path.isabs(file_attr) else os.path.join(app, file_attr)
        return os.path.relpath(os.path.realpath(p), os.path.realpath(app))

    def walk(node, suites, file_hint):
        for child in node:
            if child.tag == "testsuite":
                walk(child, suites + [child.get("name", "")], rel(child.get("file")) or file_hint)
            elif child.tag == "testcase":
                file = rel(child.get("file")) or file_hint or child.get("classname") or "?"
                ident = "::".join([file] + [s for s in suites if s] + [child.get("name", "")])
                n, base = 2, ident
                while ident in statuses:
                    ident = "%s #%d" % (base, n)
                    n += 1
                tags = {c.tag for c in child}
                if tags & {"failure", "error"}:
                    statuses[ident] = "failed"
                elif "skipped" in tags:
                    statuses[ident] = "skipped"
                else:
                    statuses[ident] = "passed"

    walk(root if root.tag == "testsuites" else [root], [], None)
    return statuses


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tests", required=True)
    ap.add_argument("--app", required=True)
    ap.add_argument("--logs", required=True)
    a = ap.parse_args()
    reward_path = os.path.join(a.logs, "reward.txt")
    with open(reward_path, "w") as f:
        f.write("0\n")

    with open(os.path.join(a.tests, "spec.json")) as f:
        spec = json.load(f)
    expected = None
    exp_path = os.path.join(a.tests, "expected.json")
    if os.path.exists(exp_path):
        with open(exp_path) as f:
            expected = json.load(f)

    notes = []
    restore_test_side(a.app, spec.get("test_globs") or [], notes)
    install_hidden_tests(a.tests, a.app, spec.get("deleted") or [])
    junit, code, timed_out, seconds = run_command(spec, a.app, a.logs)
    statuses = parse_junit(junit, a.app)
    for f in spec["run_files"]:
        if not any(k.startswith(f + "::") for k in statuses):
            statuses["%s::%s" % (f, NO_TESTS)] = "failed"

    result = {"exit_code": code, "timed_out": timed_out, "seconds": seconds, "notes": notes, "statuses": statuses}
    with open(os.path.join(a.logs, "results.json"), "w") as f:
        json.dump(result, f, indent=1, sort_keys=True)

    if expected is None:
        print("taskmine grader: calibration run, %d test statuses recorded" % len(statuses))
        return 0
    required = list(expected["fail_to_pass"]) + list(expected["pass_to_pass"])
    failing = [t for t in required if statuses.get(t) != "passed"]
    reward = 1 if required and not failing else 0
    with open(os.path.join(a.logs, "grade.json"), "w") as f:
        json.dump({"reward": reward, "required": len(required), "failing": failing[:100]}, f, indent=1)
    with open(reward_path, "w") as f:
        f.write("%d\n" % reward)
    print("taskmine grader: reward %d (%d of %d required tests passing)" % (reward, len(required) - len(failing), len(required)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
