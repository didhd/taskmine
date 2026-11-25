"""The sealed repository snapshot that becomes `environment/repo.tar.gz`.

The snapshot is the base commit's tree and nothing else: no `.git` history (which would hold the
merged answer), no files from the change. Binary files and files over the size cap are left out
because a coding agent cannot use them and they dominate the size (in the dogfood monorepo: 1.35 GB of
tree, 297 MB of text). Files the gold patch touches are always kept, or the patch could not
apply.

The archive is byte-for-byte reproducible from (repository, base, recipe): entries sorted, fixed
mtime, uid/gid 0, gzip header without a name or time. That is what lets a dataset be validated on
one machine and rebuilt on another with the same sha256, instead of copying gigabytes around.

`verify_seal` re-reads the finished archive, not the builder's intent: every member must be the
base tree's own blob at that path, and no member may equal a blob that only exists after the
change.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import tarfile
from dataclasses import dataclass, field

from taskmine.config import Config
from taskmine.vcs import Repo, TreeEntry

SNAPSHOT_MTIME = 946684800  # 2000-01-01T00:00:00Z: fixed, so the archive is reproducible


def git_blob_sha(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def looks_binary(data: bytes) -> bool:
    return b"\0" in data[:8192]


@dataclass
class Snapshot:
    data: bytes
    sha256: str
    members: dict[str, str]  # path -> blob sha
    left_out: dict[str, int] = field(default_factory=dict)  # reason -> count
    text: dict[str, bytes] = field(default_factory=dict)  # path -> content, text files only


def _extension(path: str) -> str:
    name = path.rsplit("/", 1)[-1]
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def select(cfg: Config, entries: list[TreeEntry], keep: set[str]) -> tuple[list[TreeEntry], dict[str, int]]:
    chosen, left_out = [], {}

    def drop(reason: str) -> None:
        left_out[reason] = left_out.get(reason, 0) + 1

    for e in entries:
        if e.kind != "blob":
            drop("submodule")
            continue
        if e.path in keep:
            chosen.append(e)
        elif cfg.snapshot_exclude.match(e.path):
            drop("excluded")
        elif _extension(e.path) in cfg.binary_ext:
            drop("binary-extension")
        elif e.size is not None and e.size > cfg.max_file_bytes:
            drop("over-size")
        else:
            chosen.append(e)
    return chosen, left_out


def build(repo: Repo, cfg: Config, base: str, keep: set[str]) -> Snapshot:
    entries = repo.ls_tree(base)
    chosen, left_out = select(cfg, entries, keep)
    blobs = repo.cat_blobs([e.sha for e in chosen])

    members: dict[str, str] = {}
    text: dict[str, bytes] = {}
    final = []
    for e in chosen:
        data = blobs[e.sha]
        if e.path not in keep and e.mode != "120000" and looks_binary(data):
            left_out["binary-content"] = left_out.get("binary-content", 0) + 1
            continue
        final.append(e)
        members[e.path] = e.sha
        if e.mode != "120000" and not looks_binary(data):
            text[e.path] = data

    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.PAX_FORMAT) as tar:
        dirs = sorted({"/".join(e.path.split("/")[:i]) for e in final for i in range(1, e.path.count("/") + 1)})
        for d in dirs:
            info = tarfile.TarInfo(d)
            info.type = tarfile.DIRTYPE
            info.mode = 0o755
            info.mtime = SNAPSHOT_MTIME
            tar.addfile(info)
        for e in sorted(final, key=lambda x: x.path):
            data = blobs[e.sha]
            info = tarfile.TarInfo(e.path)
            info.mtime = SNAPSHOT_MTIME
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            if e.mode == "120000":
                info.type = tarfile.SYMTYPE
                info.linkname = data.decode("utf-8", "surrogateescape")
                info.mode = 0o777
                tar.addfile(info)
            else:
                info.mode = 0o755 if e.mode == "100755" else 0o644
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
    gz = io.BytesIO()
    with gzip.GzipFile(filename="", fileobj=gz, mode="wb", compresslevel=6, mtime=0) as f:
        f.write(raw.getvalue())
    data = gz.getvalue()
    return Snapshot(data, hashlib.sha256(data).hexdigest(), members, left_out, text)


def verify_seal(data: bytes, base_tree: dict[str, str], after_only_blobs: set[str]) -> list[str]:
    """Problems found by reading the archive itself. Empty means sealed."""
    problems = []
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        for m in tar.getmembers():
            if m.isdir():
                continue
            if m.name.startswith("/") or ".." in m.name.split("/"):
                problems.append(f"{m.name}: path escapes the archive root")
                continue
            if m.issym():
                content = m.linkname.encode("utf-8", "surrogateescape")
            elif m.isfile():
                content = tar.extractfile(m).read()
            else:
                problems.append(f"{m.name}: unexpected member type")
                continue
            sha = git_blob_sha(content)
            if base_tree.get(m.name) != sha:
                problems.append(f"{m.name}: not the base commit's content")
            if sha in after_only_blobs:
                problems.append(f"{m.name}: holds content that only exists after the change")
            if m.name == ".git" or m.name.startswith(".git/"):
                problems.append(f"{m.name}: repository history in the snapshot")
    return problems
