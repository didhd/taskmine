#!/usr/bin/env python3
"""taskmine grader: runs inside the task container after the agent, and writes the reward.

Written for any python3 >= 3.7 the container has; the standard library only.

It runs in one of three stages (test.sh passes `--stage`):
- all (the default, what Harbor's own verifier runs): steps 1 to 5 below in one process, in the
  agent's container. The reward is reward.txt.
- untrusted (PR #120's staged grader, as the agent user): steps 2 to 4 on a copy of the repository,
  so the repository itself is left exactly as the agent left it. It writes the JUnit report and
  run.json to --out and no reward: whatever it writes is evidence, never a score.
- trusted (PR #120's staged grader, as the grader user, after every agent process is gone): step 5
  only, from the JUnit report in --in and the task's own files in --tests. It reads nothing from
  the agent's tree, never reads a reward or a status from --in, and writes reward.json to --out.

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
import tempfile
import time
import xml.etree.ElementTree as ET

BASE_TAG = "taskmine-base"
NO_TESTS = "(no tests reported)"
# The trusted stage parses a report that code the agent influenced wrote: a bounded size, and no
# DTD (no entity expansion).
MAX_REPORT_BYTES = 20000000


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


_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _leaf(name):
    """The test's own identifier in a runner's name for it: `TestCart/sub` (Go), `tests::adds_lines` (Rust),
    `Acme.CartTests.AddsLines(x: 1)` (.NET), `addsLines()` (JUnit 5), `test_total[case]` (pytest)."""
    name = re.split(r"[([]", name, maxsplit=1)[0].strip()
    if " " in name:  # a display name, not an identifier: searched for as written
        return None
    name = name.split("/")[0]
    for sep in ("::", "."):
        name = name.split(sep)[-1]
    return name if _WORD.fullmatch(name or "") else None


def file_for(classname, name, run_files, app, cache, suites=(), texts_dir=None):
    """The run file a testcase belongs to when the report has no file attribute.

    Maven and Gradle (surefire), gotestsum, cargo-nextest and dotnet name a testcase by class,
    package or binary only. In order:
    1. a run file's path given as the class or a suite name (vitest, jest);
    2. the class as a path (com.acme.CartTest -> com/acme/CartTest.java);
    3. the last part of the class, binary or package, as the file stem or its directory;
    4. the run file whose text defines the test's own identifier;
    5. the only run file.
    None if still unsure: the testcase then keeps its class as its file.
    """
    if not run_files:
        return None
    for named in (classname or "", *suites):
        named = named.strip()
        if named.startswith("./"):  # str.removeprefix is Python 3.9; this runs on 3.7
            named = named[2:]
        if named in run_files:
            return named
    cls = re.split(r"[$+]", classname or "")[0]
    if cls:
        as_path = cls.replace(".", "/").replace("::", "/")
        hits = [f for f in run_files if os.path.splitext(f)[0].endswith(as_path)]
        if len(hits) == 1:
            return hits[0]
        last = re.split(r"[./:]+", cls)[-1]
        hits = [f for f in run_files if os.path.splitext(os.path.basename(f))[0] == last]
        if len(hits) == 1:
            return hits[0]
        hits = [f for f in run_files if os.path.basename(os.path.dirname(f)) == last]
        if len(hits) == 1:
            return hits[0]
    leaf = _leaf(name or "")
    shown = re.split(r"[([]", name or "", maxsplit=1)[0].strip()
    if leaf or len(shown) > 3:
        pat = re.compile(r"\b%s\b" % re.escape(leaf)) if leaf else re.compile(re.escape(shown))
        hits = []
        for f in run_files:
            if f not in cache:
                try:
                    with open(os.path.join(texts_dir or app, f), encoding="utf-8", errors="replace") as fh:
                        cache[f] = fh.read()
                except OSError:
                    cache[f] = ""
            if pat.search(cache[f]):
                hits.append(f)
        if len(hits) == 1:
            return hits[0]
    return run_files[0] if len(run_files) == 1 else None


def parse_junit(path, app, run_files=(), roots=None, texts_dir=None):
    """{test id: passed|failed|skipped} from a JUnit report.

    In the agent's container (roots None) a file attribute is resolved on disk against app. The
    trusted stage passes the roots the tests ran under instead, and texts_dir (the task's own copy
    of the test files), so that it maps the same ids without reading the agent's tree.
    """
    statuses = {}
    run_files = list(run_files)
    texts = {}
    if not os.path.exists(path):
        return statuses
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError:
        return statuses

    def rel(file_attr):
        if not file_attr:
            return None
        if roots is None:
            p = file_attr if os.path.isabs(file_attr) else os.path.join(app, file_attr)
            return os.path.relpath(os.path.realpath(p), os.path.realpath(app))
        p = os.path.normpath(file_attr)
        if not os.path.isabs(p):
            return p
        for r in roots:
            if p.startswith(r.rstrip("/") + "/"):
                return p[len(r.rstrip("/")) + 1:]
        return os.path.relpath(p, roots[0])

    def walk(node, suites, file_hint):
        for child in node:
            if child.tag == "testsuite":
                walk(child, suites + [child.get("name", "")], rel(child.get("file")) or file_hint)
            elif child.tag == "testcase":
                file = (rel(child.get("file")) or file_hint
                        or file_for(child.get("classname"), child.get("name", ""), run_files, app, texts, suites,
                                    texts_dir)
                        or child.get("classname") or "?")
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


def add_missing(statuses, run_files):
    for f in run_files:
        if not any(k.startswith(f + "::") for k in statuses):
            statuses["%s::%s" % (f, NO_TESTS)] = "failed"


def worst(statuses, ident):
    """A test id that the report holds more than once (`id`, `id #2`, ...) passes only if every copy passed."""
    copies = [v for k, v in statuses.items() if k == ident or (k.startswith(ident + " #") and k[len(ident) + 2:].isdigit())]
    if not copies:
        return None
    return "passed" if all(v == "passed" for v in copies) else "failed"


def grade(statuses, expected):
    """(reward, required, failing): 1 only if every fail-to-pass and pass-to-pass test passed."""
    required = list(expected["fail_to_pass"]) + list(expected["pass_to_pass"])
    failing = [t for t in required if worst(statuses, t) != "passed"]
    return (1 if required and not failing else 0), required, failing


def load(tests):
    with open(os.path.join(tests, "spec.json")) as f:
        spec = json.load(f)
    expected = None
    exp_path = os.path.join(tests, "expected.json")
    if os.path.exists(exp_path):
        with open(exp_path) as f:
            expected = json.load(f)
    return spec, expected


def write_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=1, sort_keys=True)


def stage_all(tests, app, logs):
    """Harbor's own verifier: everything in the agent's container. reward.txt."""
    reward_path = os.path.join(logs, "reward.txt")
    with open(reward_path, "w") as f:
        f.write("0\n")
    spec, expected = load(tests)
    notes = []
    restore_test_side(app, spec.get("test_globs") or [], notes)
    install_hidden_tests(tests, app, spec.get("deleted") or [])
    junit, code, timed_out, seconds = run_command(spec, app, logs)
    statuses = parse_junit(junit, app, spec["run_files"])
    add_missing(statuses, spec["run_files"])
    write_json(os.path.join(logs, "results.json"),
               {"exit_code": code, "timed_out": timed_out, "seconds": seconds, "notes": notes, "statuses": statuses})
    if expected is None:
        print("taskmine grader: calibration run, %d test statuses recorded" % len(statuses))
        return 0
    reward, required, failing = grade(statuses, expected)
    with open(os.path.join(logs, "grade.json"), "w") as f:
        json.dump({"reward": reward, "required": len(required), "failing": failing[:100]}, f, indent=1)
    with open(reward_path, "w") as f:
        f.write("%d\n" % reward)
    print("taskmine grader: reward %d (%d of %d required tests passing)" % (reward, len(required) - len(failing), len(required)))
    return 0


def copy_tree(src, dest):
    """A copy of the agent's repository, symlinks kept as symlinks; `cp -a` when there is one (much faster)."""
    if shutil.which("cp") and subprocess.run(["cp", "-a", os.path.join(src, "."), dest]).returncode == 0:
        return
    shutil.rmtree(dest, ignore_errors=True)
    shutil.copytree(src, dest, symlinks=True)


def stage_untrusted(tests, app, out):
    """Run the tests on a copy of the repository and write the evidence: the JUnit report and run.json.

    PR #120's grader compares the agent's patch before and after this stage, over every file in the
    repository, and gives no grade if it changed. Running on a copy leaves the repository as the
    agent left it however much the test run writes (caches, build output, restored test files).
    """
    os.makedirs(out, exist_ok=True)
    spec, _expected = load(tests)
    scratch = tempfile.mkdtemp(prefix="taskmine-grade-")
    try:
        repo = os.path.join(scratch, "repo")
        os.mkdir(repo)
        copy_tree(app, repo)
        notes = ["ran on a copy of the repository at %s" % repo]
        restore_test_side(repo, spec.get("test_globs") or [], notes)
        install_hidden_tests(tests, repo, spec.get("deleted") or [])
        _junit, code, timed_out, seconds = run_command(spec, repo, out)
        write_json(os.path.join(out, "run.json"), {"exit_code": code, "timed_out": timed_out, "seconds": seconds,
                                                   "notes": notes, "root": repo})
        print("taskmine grader: untrusted stage, tests ran (exit %s%s); no reward is written here"
              % (code, ", timed out" if timed_out else ""))
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    return 0


def report_problem(path):
    """Why the trusted stage will not parse this report, or None."""
    if not os.path.isfile(path) or os.path.islink(path):
        return "no JUnit report from the untrusted stage"
    if os.path.getsize(path) > MAX_REPORT_BYTES:
        return "the JUnit report is over %d bytes" % MAX_REPORT_BYTES
    with open(path, "rb") as f:
        head = f.read()
    if b"<!DOCTYPE" in head.upper() or b"<!ENTITY" in head.upper():
        return "the JUnit report declares a DTD or entities"
    return None


def stage_trusted(tests, inp, out):
    """The score, from the task's own files and the untrusted stage's JUnit report only.

    Nothing from --in is taken as a result: not reward*, grade.json or results.json, which the
    staged grader does not even copy, and not run.json's exit code. run.json gives only the folder
    the tests ran in, to map file paths in the report to test ids, as stage_all does on disk.
    reward.json is written last; if this stage fails before it, there is no reward and the trial
    is reported as an error, not as a score.
    """
    os.makedirs(out, exist_ok=True)
    for name in ("reward.json", "reward.txt"):
        if os.path.lexists(os.path.join(out, name)):
            os.remove(os.path.join(out, name))
    spec, expected = load(tests)
    if expected is None:
        print("taskmine grader: no expected.json, so there is nothing to grade against")
        return 3
    roots = ["/app", "/work/repo"]
    try:
        with open(os.path.join(inp, "run.json")) as f:
            ran_in = json.load(f).get("root")
        if isinstance(ran_in, str) and os.path.isabs(ran_in):
            roots.insert(0, os.path.normpath(ran_in))
    except (OSError, ValueError, AttributeError):
        pass
    junit = os.path.join(inp, "junit.xml")
    problem = report_problem(junit)
    statuses = {} if problem else parse_junit(junit, "/", spec["run_files"], roots=roots,
                                              texts_dir=os.path.join(tests, "files"))
    add_missing(statuses, spec["run_files"])
    reward, required, failing = grade(statuses, expected)
    write_json(os.path.join(out, "results.json"), {"statuses": statuses, "notes": [problem] if problem else []})
    write_json(os.path.join(out, "grade.json"), {"reward": reward, "required": len(required), "failing": failing[:100],
                                                 "stage": "trusted", "report": problem or "parsed"})
    write_json(os.path.join(out, "reward.json"), {"reward": float(reward)})
    print("taskmine grader: trusted stage, reward %d (%d of %d required tests passing)%s"
          % (reward, len(required) - len(failing), len(required), "; " + problem if problem else ""))
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", choices=("all", "untrusted", "trusted"), default="all")
    ap.add_argument("--tests", required=True)
    ap.add_argument("--app")
    ap.add_argument("--logs")
    ap.add_argument("--in", dest="inp")
    ap.add_argument("--out")
    a = ap.parse_args()
    if a.stage == "all":
        return stage_all(a.tests, a.app, a.logs)
    if a.stage == "untrusted":
        return stage_untrusted(a.tests, a.app, a.out)
    return stage_trusted(a.tests, a.inp, a.out)


if __name__ == "__main__":
    sys.exit(main())
