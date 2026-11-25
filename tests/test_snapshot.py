"""The sealed snapshot: what is left out and why, byte-for-byte reproducibility, and the seal check."""

from __future__ import annotations

import io
import subprocess
import tarfile

from support import GIT_ENV, NODE_PROFILE, History, SharedTempDir, needs_git, recipe

from taskmine import config, snapshot
from taskmine.vcs import Repo

RECIPE = """
[repo]
name = "example/snap"

[snapshot]
max_file_bytes = 1000
exclude = ["media/**"]
""" + NODE_PROFILE


def tar_bytes(members: list[tuple[tarfile.TarInfo, bytes | None]]) -> bytes:
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w:gz") as tar:
        for info, data in members:
            if data is not None:
                info.size = len(data)
            tar.addfile(info, io.BytesIO(data) if data is not None else None)
    return raw.getvalue()


@needs_git
class Snapshots(SharedTempDir):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        h = History(cls.tmp / "repo")
        cls.base = h.commit("base", {
            "src/app.mjs": "export const app = 1;\n",
            "bin/run.sh": ("100755", "#!/bin/sh\necho run\n"),
            "current": ("120000", "src/app.mjs"),
            "logo.png": b"\x89PNG not really",
            "data.dat": b"text then \x00 a nul byte",
            "big.mjs": "z" * 1500,
            "kept-big.mjs": "k" * 1500,
            "media/clip.txt": "excluded by the recipe\n",
            "vendor/dep": ("160000", "2" * 40),
        })
        h.commit("after", {"src/app.mjs": "export const app = 2;\n"})
        cls.root = h.write()
        cls.repo = Repo(str(cls.root))
        cls.cfg = config.load(recipe(cls.tmp, RECIPE))
        cls.snap = snapshot.build(cls.repo, cls.cfg, h.shas[cls.base], {"kept-big.mjs"})
        cls.base_sha = h.shas[cls.base]

    def members(self):
        with tarfile.open(fileobj=io.BytesIO(self.snap.data), mode="r:gz") as tar:
            return {m.name: m for m in tar.getmembers()}

    def test_what_is_left_out_and_why(self):
        self.assertEqual(sorted(self.snap.members), ["bin/run.sh", "current", "kept-big.mjs", "src/app.mjs"])
        self.assertEqual(self.snap.left_out, {"submodule": 1, "excluded": 1, "binary-extension": 1,
                                              "over-size": 1, "binary-content": 1})
        self.assertEqual(sorted(self.snap.text), ["bin/run.sh", "kept-big.mjs", "src/app.mjs"])

    def test_the_archive_is_normalised(self):
        m = self.members()
        self.assertEqual(list(m), sorted(m, key=lambda n: (m[n].isfile() or m[n].issym(), n)))
        self.assertEqual(m["bin/run.sh"].mode, 0o755)
        self.assertEqual(m["src/app.mjs"].mode, 0o644)
        self.assertTrue(m["current"].issym())
        self.assertEqual(m["current"].linkname, "src/app.mjs")
        for info in m.values():
            self.assertEqual(info.mtime, snapshot.SNAPSHOT_MTIME)
            self.assertEqual((info.uid, info.gid, info.uname, info.gname), (0, 0, "", ""))
        header = self.snap.data[:10]
        self.assertEqual(header[4:8], b"\0\0\0\0")  # gzip MTIME is zero
        self.assertEqual(header[3] & 0x08, 0)  # no FNAME field

    def test_a_rebuild_is_byte_identical(self):
        again = snapshot.build(self.repo, self.cfg, self.base_sha, {"kept-big.mjs"})
        self.assertEqual(again.sha256, self.snap.sha256)
        self.assertEqual(again.data, self.snap.data)

    def test_a_filtered_clone_builds_the_same_snapshot(self):
        self.repo._run(["config", "uploadpack.allowFilter", "true"])
        clone = self.tmp / "filtered"
        subprocess.run(["git", "clone", "-q", "--bare", "--filter=blob:limit=900", f"file://{self.root}", str(clone)],
                       env=GIT_ENV, check=True, capture_output=True)
        snap = snapshot.build(Repo(str(clone)), self.cfg, self.base_sha, set())
        full = snapshot.build(self.repo, self.cfg, self.base_sha, set())
        self.assertEqual(snap.sha256, full.sha256)
        self.assertNotIn("big.mjs", snap.members)

    def test_the_seal_holds_for_the_base_tree(self):
        base_tree = {e.path: e.sha for e in self.repo.ls_tree(self.base_sha) if e.kind == "blob"}
        self.assertEqual(snapshot.verify_seal(self.snap.data, base_tree, set()), [])

    def test_the_seal_reports_content_from_the_change(self):
        base_tree = {e.path: e.sha for e in self.repo.ls_tree(self.base_sha) if e.kind == "blob"}
        after = snapshot.git_blob_sha(b"export const app = 2;\n")
        info = tarfile.TarInfo("src/app.mjs")
        bad = tar_bytes([(info, b"export const app = 2;\n")])
        problems = snapshot.verify_seal(bad, base_tree, {after})
        self.assertEqual(problems, ["src/app.mjs: not the base commit's content",
                                    "src/app.mjs: holds content that only exists after the change"])

    def test_the_seal_reports_history_escapes_and_odd_members(self):
        git_dir = tarfile.TarInfo(".git/HEAD")
        escape = tarfile.TarInfo("../outside")
        fifo = tarfile.TarInfo("pipe")
        fifo.type = tarfile.FIFOTYPE
        bad = tar_bytes([(git_dir, b"ref: refs/heads/main\n"), (escape, b"x"), (fifo, None)])
        problems = snapshot.verify_seal(bad, {}, set())
        self.assertIn(".git/HEAD: repository history in the snapshot", problems)
        self.assertIn("../outside: path escapes the archive root", problems)
        self.assertIn("pipe: unexpected member type", problems)

    def test_git_blob_sha_matches_git(self):
        sha = subprocess.run(["git", "hash-object", "--stdin"], input=b"hello\n", capture_output=True, check=True,
                             env=GIT_ENV).stdout.decode().strip()
        self.assertEqual(snapshot.git_blob_sha(b"hello\n"), sha)
        self.assertTrue(snapshot.looks_binary(b"a\0b"))
        self.assertFalse(snapshot.looks_binary(b"plain text"))
