"""The only module that runs git. Every call reads; nothing here writes to the mined repository.

`GIT_NO_LAZY_FETCH=1` makes a partial clone fail on a missing blob instead of trying to fetch it
from a promisor remote that may not exist on this machine: taskmine runs on throwaway boxes
from a filtered clone, and a silent network fetch there would hang or read the wrong place.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass

_ENV = dict(
    os.environ,
    LC_ALL="C",
    GIT_NO_LAZY_FETCH="1",
    GIT_TERMINAL_PROMPT="0",
    GIT_PAGER="cat",
    GIT_CONFIG_NOSYSTEM="1",
)

NULL_SHA = "0" * 40

# The size given to a blob a filtered clone does not hold. A snapshot treats it as over the size cap,
# which it is whenever the clone's blob filter is above [snapshot] max_file_bytes: then a draft built
# from the filtered clone is byte-identical to one built from a full clone.
MISSING_SIZE = 1 << 62


class GitError(RuntimeError):
    pass


@dataclass(frozen=True)
class TreeEntry:
    mode: str
    kind: str  # blob | tree | commit (a submodule)
    sha: str
    size: int | None
    path: str


@dataclass(frozen=True)
class Change:
    status: str  # A M D T (renames are disabled, so never R or C)
    path: str
    old_sha: str
    new_sha: str
    old_mode: str
    new_mode: str


@dataclass(frozen=True)
class Commit:
    sha: str
    parents: tuple[str, ...]
    subject: str
    date: str


def decode_path(raw: bytes) -> str:
    return raw.decode("utf-8", "surrogateescape")


def encode_path(path: str) -> bytes:
    return path.encode("utf-8", "surrogateescape")


class Repo:
    def __init__(self, path: str):
        self.path = os.path.abspath(path)

    def _run(self, args: list[str], input: bytes | None = None) -> bytes:
        cmd = ["git", "--literal-pathspecs", "-C", self.path, "-c", "core.quotepath=off", *args]
        proc = subprocess.run(cmd, input=input, capture_output=True, env=_ENV)
        if proc.returncode != 0:
            err = proc.stderr.decode(errors="replace").strip()
            raise GitError(f"git {' '.join(args[:2])} failed ({proc.returncode}): {err[:600]}")
        return proc.stdout

    def rev(self, spec: str) -> str:
        return self._run(["rev-parse", "--verify", spec + "^{commit}"]).decode().strip()

    def ls_tree(self, treeish: str) -> list[TreeEntry]:
        out = self._run(["ls-tree", "-r", "-z", "-l", "--full-tree", treeish])
        entries = []
        for rec in out.split(b"\0"):
            if not rec:
                continue
            meta, path = rec.split(b"\t", 1)
            mode, kind, sha, size = meta.split()
            # `BAD` is a blob this (filtered) clone does not hold; MISSING_SIZE sorts above every cap.
            n = None if size == b"-" else MISSING_SIZE if size == b"BAD" else int(size)
            entries.append(TreeEntry(mode.decode(), kind.decode(), sha.decode(), n, decode_path(path)))
        return entries

    def blob_sizes(self, shas: list[str]) -> dict[str, int | None]:
        """Size per blob; None when the object is missing (a filtered clone)."""
        uniq = [s for s in dict.fromkeys(shas) if s and s != NULL_SHA]
        if not uniq:
            return {}
        out = self._run(["cat-file", "--batch-check"], input=("\n".join(uniq) + "\n").encode())
        sizes: dict[str, int | None] = {}
        for line, sha in zip(out.decode().splitlines(), uniq):
            parts = line.split()
            sizes[sha] = None if parts[-1] == "missing" else int(parts[2])
        return sizes

    def cat_blobs(self, shas: list[str]) -> dict[str, bytes]:
        uniq = [s for s in dict.fromkeys(shas) if s and s != NULL_SHA]
        result: dict[str, bytes] = {}
        for i in range(0, len(uniq), 4000):
            chunk = uniq[i : i + 4000]
            out = self._run(["cat-file", "--batch"], input=("\n".join(chunk) + "\n").encode())
            pos = 0
            for sha in chunk:
                nl = out.index(b"\n", pos)
                header = out[pos:nl].split()
                if header[-1] == b"missing":
                    raise GitError(f"object {sha} is missing from {self.path} (a filtered clone without it?)")
                size = int(header[2])
                start = nl + 1
                result[sha] = out[start : start + size]
                pos = start + size + 1
        return result

    def changes(self, base: str, after: str) -> list[Change]:
        out = self._run(["diff", "--raw", "-z", "--no-renames", "--no-abbrev", base, after])
        parts = out.split(b"\0")
        res = []
        i = 0
        while i + 1 < len(parts):
            meta = parts[i].decode()
            if not meta.startswith(":"):
                i += 1
                continue
            path = decode_path(parts[i + 1])
            i += 2
            old_mode, new_mode, old_sha, new_sha, status = meta[1:].split()
            res.append(Change(status[0], path, old_sha, new_sha, old_mode, new_mode))
        return res

    def numstat(self, base: str, after: str) -> dict[str, tuple[int, int] | None]:
        """Lines added and deleted per path; None for a binary file."""
        out = self._run(["diff", "--numstat", "-z", "--no-renames", base, after])
        res: dict[str, tuple[int, int] | None] = {}
        for rec in out.split(b"\0"):
            if not rec:
                continue
            added, deleted, path = rec.split(b"\t", 2)
            res[decode_path(path)] = None if added == b"-" else (int(added), int(deleted))
        return res

    def patch(self, base: str, after: str, paths: list[str]) -> bytes:
        """A `git apply`-able patch for exactly these paths, binary hunks included."""
        if not paths:
            return b""
        return self._run(
            [
                "-c", "diff.noprefix=false", "-c", "diff.mnemonicPrefix=false",
                "diff", "--binary", "--full-index", "--no-renames", "--no-color",
                "--no-ext-diff", "--no-textconv", base, after, "--", *paths,
            ]
        )

    def merges(self, branch: str, since: str | None) -> list[Commit]:
        args = ["log", branch, "--merges", "--format=%H%x1f%P%x1f%s%x1f%cI%x1e"]
        if since:
            args.insert(2, f"--since={since}")
        return self._parse_log(self._run(args))

    def first_parent_commits(self, branch: str, since: str | None) -> list[Commit]:
        args = ["log", branch, "--first-parent", "--format=%H%x1f%P%x1f%s%x1f%cI%x1e"]
        if since:
            args.insert(2, f"--since={since}")
        return self._parse_log(self._run(args))

    def commit_subject(self, sha: str) -> str:
        return self._run(["log", "-1", "--format=%s", sha]).decode("utf-8", "replace").strip()

    @staticmethod
    def _parse_log(out: bytes) -> list[Commit]:
        commits = []
        for rec in out.decode("utf-8", "replace").split("\x1e"):
            rec = rec.strip("\n")
            if not rec:
                continue
            sha, parents, subject, date = rec.split("\x1f")
            commits.append(Commit(sha, tuple(parents.split()), subject, date))
        return commits
