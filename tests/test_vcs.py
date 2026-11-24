"""vcs.Repo against real repositories: trees, changes, patches, logs, and filtered clones."""

from __future__ import annotations

import subprocess

from support import GIT_ENV, GitRepo, SharedTempDir, TempDir, needs_git

from taskmine.vcs import MISSING_SIZE, GitError, Repo


@needs_git
class ReadingARepository(SharedTempDir):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.g = GitRepo(cls.tmp / "repo")
        cls.g.write("a.txt", "one\n")
        cls.g.write("bin/run.sh", "#!/bin/sh\necho hi\n", mode=0o755)
        cls.g.write("dir with space/é.txt", "accent\n")
        cls.g.symlink("link", "a.txt")
        cls.base = cls.g.commit("base", date="2026-01-01T00:00:00Z")
        cls.g.write("a.txt", "one\ntwo\n")
        cls.g.write("new.bin", b"\x00\x01\x02binary")
        cls.g.remove("bin/run.sh")
        cls.g.git("rm", "-q", "link")
        cls.g.write("link", "now a regular file\n")
        cls.after = cls.g.commit("after", date="2026-02-01T00:00:00Z")
        cls.repo = Repo(str(cls.g.root))

    def test_rev_resolves_and_rejects(self):
        self.assertEqual(self.repo.rev("HEAD"), self.after)
        self.assertEqual(self.repo.rev("HEAD~1"), self.base)
        with self.assertRaises(GitError):
            self.repo.rev("no-such-ref")

    def test_ls_tree_reports_modes_sizes_and_unusual_paths(self):
        entries = {e.path: e for e in self.repo.ls_tree(self.base)}
        self.assertEqual(sorted(entries), ["a.txt", "bin/run.sh", "dir with space/é.txt", "link"])
        self.assertEqual(entries["bin/run.sh"].mode, "100755")
        self.assertEqual(entries["link"].mode, "120000")
        self.assertEqual(entries["a.txt"].size, 4)
        self.assertEqual(entries["a.txt"].kind, "blob")

    def test_changes_have_one_status_per_path_and_no_renames(self):
        ch = {c.path: c for c in self.repo.changes(self.base, self.after)}
        self.assertEqual(ch["a.txt"].status, "M")
        self.assertEqual(ch["new.bin"].status, "A")
        self.assertEqual(ch["bin/run.sh"].status, "D")
        self.assertEqual(ch["link"].status, "T")
        self.assertEqual((ch["link"].old_mode, ch["link"].new_mode), ("120000", "100644"))
        self.assertEqual(ch["new.bin"].old_sha, "0" * 40)

    def test_numstat_counts_lines_and_marks_binary(self):
        st = self.repo.numstat(self.base, self.after)
        self.assertEqual(st["a.txt"], (1, 0))
        self.assertIsNone(st["new.bin"])
        self.assertEqual(st["bin/run.sh"], (0, 2))

    def test_patch_applies_to_the_base_including_binary(self):
        patch = self.repo.patch(self.base, self.after, ["a.txt", "new.bin", "bin/run.sh"])
        self.assertIn(b"GIT binary patch", patch)
        work = self.tmp / "work"
        subprocess.run(["git", "worktree", "add", "-q", "--detach", str(work), self.base],
                       cwd=self.g.root, env=GIT_ENV, check=True, capture_output=True)
        subprocess.run(["git", "apply", "-"], cwd=work, env=GIT_ENV, input=patch, check=True, capture_output=True)
        self.assertEqual((work / "a.txt").read_text(), "one\ntwo\n")
        self.assertEqual((work / "new.bin").read_bytes(), b"\x00\x01\x02binary")
        self.assertFalse((work / "bin/run.sh").exists())
        self.assertEqual(self.repo.patch(self.base, self.after, []), b"")

    def test_cat_blobs_and_sizes(self):
        sha = {e.path: e.sha for e in self.repo.ls_tree(self.after)}
        blobs = self.repo.cat_blobs([sha["a.txt"], sha["new.bin"], sha["a.txt"], "0" * 40, ""])
        self.assertEqual(blobs, {sha["a.txt"]: b"one\ntwo\n", sha["new.bin"]: b"\x00\x01\x02binary"})
        sizes = self.repo.blob_sizes([sha["a.txt"], "1" * 40])
        self.assertEqual(sizes, {sha["a.txt"]: 8, "1" * 40: None})
        with self.assertRaises(GitError):
            self.repo.cat_blobs(["1" * 40])

    def test_logs_since_and_subjects(self):
        commits = self.repo.first_parent_commits("main", None)
        self.assertEqual([c.subject for c in commits], ["after", "base"])
        self.assertEqual(commits[0].parents, (self.base,))
        self.assertEqual([c.subject for c in self.repo.first_parent_commits("main", "2026-01-15")], ["after"])
        self.assertEqual(self.repo.merges("main", None), [])
        self.assertEqual(self.repo.commit_subject(self.base), "base")


@needs_git
class Merges(TempDir):
    def test_merge_units_list_first_parent_and_branch(self):
        g = GitRepo(self.tmp / "repo")
        g.write("x", "1\n")
        g.commit("init")
        m = g.merge_pr(7, "feature", {"x": "2\n"})
        merges = Repo(str(g.root)).merges("main", None)
        self.assertEqual(len(merges), 1)
        self.assertEqual(merges[0].sha, m)
        self.assertEqual(merges[0].subject, "Merge pull request #7 from example/feature")
        self.assertEqual(len(merges[0].parents), 2)


@needs_git
class FilteredClone(TempDir):
    """A blob a partial clone does not hold is reported, never fetched."""

    def test_missing_blobs_read_as_over_every_cap(self):
        src = GitRepo(self.tmp / "src")
        src.write("small.txt", "small\n")
        src.write("big.txt", "x" * 5000)
        src.commit("init")
        src.git("config", "uploadpack.allowFilter", "true")
        clone = self.tmp / "clone"
        subprocess.run(["git", "clone", "-q", "--no-checkout", "--filter=blob:limit=1000",
                        f"file://{src.root}", str(clone)], env=GIT_ENV, check=True, capture_output=True)
        repo = Repo(str(clone))
        entries = {e.path: e for e in repo.ls_tree("HEAD")}
        self.assertEqual(entries["small.txt"].size, 6)
        self.assertEqual(entries["big.txt"].size, MISSING_SIZE)
        self.assertEqual(repo.blob_sizes([entries["big.txt"].sha]), {entries["big.txt"].sha: None})
        with self.assertRaises(GitError):
            repo.cat_blobs([entries["big.txt"].sha])
