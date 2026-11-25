"""Shared test helpers: real git repositories and real files in temporary directories.

Nothing here fakes git. A `GitRepo` runs the git binary with the user's and the system's git
configuration switched off, so a developer's signing or hook settings never change a test.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

GIT_ENV = dict(
    os.environ,
    GIT_AUTHOR_NAME="t",
    GIT_AUTHOR_EMAIL="t@example.com",
    GIT_COMMITTER_NAME="t",
    GIT_COMMITTER_EMAIL="t@example.com",
    GIT_AUTHOR_DATE="2026-01-01T00:00:00Z",
    GIT_COMMITTER_DATE="2026-01-01T00:00:00Z",
    GIT_CONFIG_NOSYSTEM="1",
    GIT_CONFIG_GLOBAL=os.devnull,
    LC_ALL="C",
)

needs_git = unittest.skipUnless(shutil.which("git"), "needs git")


class GitRepo:
    """A repository on disk, driven by the git CLI."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.git("init", "-q", "-b", "main")

    def git(self, *args: str, date: str | None = None, input: bytes | None = None) -> str:
        env = GIT_ENV if date is None else dict(GIT_ENV, GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date)
        p = subprocess.run(["git", *args], cwd=self.root, env=env, input=input, capture_output=True, check=False)
        if p.returncode != 0:
            raise AssertionError(f"git {' '.join(args)} failed: {p.stderr.decode(errors='replace')}")
        return p.stdout.decode("utf-8", "replace")

    def write(self, rel: str, data: str | bytes, mode: int | None = None) -> None:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if p.is_symlink():
            p.unlink()
        p.write_bytes(data.encode() if isinstance(data, str) else data)
        if mode is not None:
            os.chmod(p, mode)

    def symlink(self, rel: str, target: str) -> None:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if p.exists() or p.is_symlink():
            p.unlink()
        os.symlink(target, p)

    def remove(self, rel: str) -> None:
        self.git("rm", "-q", "--", rel)

    def commit(self, message: str, date: str | None = None) -> str:
        self.git("add", "-A")
        self.git("commit", "-q", "--allow-empty", "-m", message, date=date)
        return self.head()

    def head(self) -> str:
        return self.git("rev-parse", "HEAD").strip()

    def merge_pr(self, number: int, branch: str, files: dict[str, str | bytes], delete: tuple[str, ...] = (),
                 subject: str | None = None, date: str | None = None) -> str:
        """A pull request: a branch off main with one commit, merged with --no-ff."""
        self.git("checkout", "-q", "-b", branch, "main")
        for rel, data in files.items():
            self.write(rel, data)
        for rel in delete:
            self.remove(rel)
        self.commit(f"change on {branch}", date=date)
        self.git("checkout", "-q", "main")
        msg = subject or f"Merge pull request #{number} from example/{branch}"
        self.git("merge", "-q", "--no-ff", branch, "-m", msg, date=date)
        return self.head()


class History:
    """A whole history written by one `git fast-import` run: real commits and merges, few processes.

    Process start-up dominates a test that drives git one command at a time (about 30 ms a call on
    a laptop with endpoint protection). fast-import builds the same objects from one stream.
    Every commit is one minute after the previous one, so `git log` order is the order written.
    """

    def __init__(self, root: Path, start: int = 1767225600):  # 2026-01-01T00:00:00Z
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._stream: list[bytes] = []
        self._marks = 0
        self._tips: dict[str, str] = {}
        self._time = start
        self.shas: dict[str, str] = {}

    @staticmethod
    def _data(b: bytes) -> bytes:
        return b"data %d\n%s\n" % (len(b), b)

    def commit(self, message: str, files: dict | None = None, delete: tuple[str, ...] = (),
               branch: str = "main", parents: list[str] | None = None, when: int | None = None) -> str:
        """`files` maps a path to str/bytes (mode 100644), or to (mode, str/bytes); a 160000 mode takes a sha."""
        self._marks += 1
        mark = f":{self._marks}"
        self._time = when if when is not None else self._time + 60
        who = b"t <t@example.com> %d +0000\n" % self._time
        out = [f"commit refs/heads/{branch}\nmark {mark}\n".encode(), b"author " + who, b"committer " + who,
               self._data(message.encode())]
        ps = parents if parents is not None else ([self._tips[branch]] if branch in self._tips else [])
        if ps:
            out.append(f"from {ps[0]}\n".encode())
        for p in ps[1:]:
            out.append(f"merge {p}\n".encode())
        for path, val in (files or {}).items():
            mode, data = val if isinstance(val, tuple) else ("100644", val)
            if mode == "160000":
                out.append(f"M 160000 {data} {path}\n".encode())
                continue
            out.append(f"M {mode} inline {path}\n".encode())
            out.append(self._data(data.encode() if isinstance(data, str) else data))
        for path in delete:
            out.append(f"D {path}\n".encode())
        self._stream.append(b"".join(out) + b"\n")
        self._tips[branch] = mark
        return mark

    def pr(self, number: int, files: dict, delete: tuple[str, ...] = (), subject: str | None = None,
           branch: str | None = None) -> str:
        """A pull request merged with --no-ff: one commit on a branch, then a two-parent merge into main."""
        b = branch or f"pr{number}-{self._marks + 1}"
        main = self._tips["main"]
        side = self.commit(f"change for #{number}", files, delete, branch=b, parents=[main])
        return self.commit(subject or f"Merge pull request #{number} from example/{b}", files, delete,
                           branch="main", parents=[main, side])

    def write(self) -> Path:
        """Create the repository and import the history. Marks resolve to shas in `self.shas`."""
        marks = self.root / ".git" / "taskmine-marks"
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.root)], env=GIT_ENV, check=True, capture_output=True)
        subprocess.run(["git", "fast-import", "--quiet", f"--export-marks={marks}"], cwd=self.root, env=GIT_ENV,
                       input=b"".join(self._stream) + b"done\n", check=True, capture_output=True)
        for line in marks.read_text().splitlines():
            m, sha = line.split()
            self.shas[m] = sha
        return self.root


class TempDir(unittest.TestCase):
    """A test case with a fresh temporary directory, `self.tmp`, removed afterwards."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="taskmine-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)


class SharedTempDir(unittest.TestCase):
    """One temporary directory, `cls.tmp`, for every test in the class (for fixtures built once)."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp(prefix="taskmine-test-"))
        cls.addClassCleanup(shutil.rmtree, cls.tmp, True)


def recipe(tmp: Path, body: str, name: str = "taskmine.toml") -> Path:
    p = tmp / name
    p.write_text(body)
    return p


NODE_PROFILE = """
[[profiles]]
name = "node-test"
run = ["test/*.test.mjs"]
image = "node:24-bookworm"
command = "node --test --test-reporter=junit --test-reporter-destination={junit} {files}"
display_command = "node --test {files}"
"""

PY_PROFILE = """
[[profiles]]
name = "pytest"
run = ["tests/test_*.py"]
image = "python:3.12-bookworm"
command = "python -m pytest -o junit_family=xunit1 --junitxml={junit} {files}"
display_command = "python -m pytest {files}"
"""
