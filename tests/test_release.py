"""scripts/release.py, the checks the release workflow runs: tag, notes and the built distributions."""

from __future__ import annotations

import importlib.util
import io
import tarfile
import tempfile
import zipfile
from pathlib import Path

from support import TempDir

import taskmine

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("release", ROOT / "scripts" / "release.py")
release = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(release)

CHANGELOG = """# Changelog

## [Unreleased]

## [1.2.0] - 2027-03-01

### Added

- A thing.

## [1.1.0] - Unreleased

### Fixed

- Another thing.

[Unreleased]: https://example.com/compare/v1.2.0...HEAD
[1.2.0]: https://example.com/releases/tag/v1.2.0
"""


class Tags(TempDir):
    def test_the_version_is_read_without_importing_the_package(self):
        self.assertEqual(release.version(), taskmine.__version__)

    def test_check_tag(self):
        self.assertEqual(release.check_tag("v" + taskmine.__version__), "v" + taskmine.__version__)
        for bad in (taskmine.__version__, "v0.0.0", "v" + taskmine.__version__ + "rc1"):
            with self.assertRaises(release.ReleaseError):
                release.check_tag(bad)


class Notes(TempDir):
    def test_a_section_without_its_heading_or_link_references(self):
        self.assertEqual(release.notes("1.2.0", CHANGELOG), "### Added\n\n- A thing.\n")
        self.assertEqual(release.notes("1.1.0", CHANGELOG), "### Fixed\n\n- Another thing.\n")

    def test_a_release_needs_a_dated_section(self):
        self.assertEqual(release.notes("1.2.0", CHANGELOG, dated=True), "### Added\n\n- A thing.\n")
        with self.assertRaises(release.ReleaseError):
            release.notes("1.1.0", CHANGELOG, dated=True)
        with self.assertRaises(release.ReleaseError):
            release.notes("9.9.9", CHANGELOG)
        with self.assertRaises(release.ReleaseError):
            release.notes("Unreleased", CHANGELOG)

    def test_the_real_changelog_has_notes_for_this_version(self):
        text = release.notes(taskmine.__version__, (ROOT / "CHANGELOG.md").read_text())
        self.assertIn("### Added", text)


class Dist(TempDir):
    def make(self, missing: str | None = None, drop_exec: str | None = None, meta_version: str | None = None,
             extra: str | None = None) -> Path:
        ver = taskmine.__version__
        files = release.tracked_package_files()
        dist = Path(tempfile.mkdtemp(dir=self.tmp))
        with zipfile.ZipFile(dist / f"taskmine-{ver}-py3-none-any.whl", "w") as z:
            for path, executable in files.items():
                if path == missing:
                    continue
                info = zipfile.ZipInfo(path)
                info.external_attr = (0o755 if executable and path != drop_exec else 0o644) << 16
                z.writestr(info, b"x")
            if extra:
                z.writestr(extra, b"x")
            z.writestr(f"taskmine-{ver}.dist-info/METADATA", f"Name: taskmine\nVersion: {meta_version or ver}\n")
        with tarfile.open(dist / f"taskmine-{ver}.tar.gz", "w:gz") as t:
            for path in files:
                info = tarfile.TarInfo(f"taskmine-{ver}/src/{path}")
                info.size = 1
                t.addfile(info, io.BytesIO(b"x"))
        return dist

    def test_a_complete_build_passes(self):
        files = release.tracked_package_files()
        self.assertIn("taskmine/templates/grader.py", files)
        self.assertIn("taskmine/__init__.py", files)
        self.assertEqual(release.check_dist(self.make()), [])

    def test_a_missing_file_a_lost_mode_and_a_wrong_version(self):
        self.assertEqual(release.check_dist(self.make(missing="taskmine/templates/textprobe.mjs")),
                         ["wheel is missing taskmine/templates/textprobe.mjs"])
        self.assertEqual(release.check_dist(self.make(meta_version="0.0.1"))[:1],
                         [f"wheel METADATA does not say Version: {taskmine.__version__}"])
        executables = [p for p, x in release.tracked_package_files().items() if x]
        if executables:
            self.assertEqual(release.check_dist(self.make(drop_exec=executables[0])),
                             [f"wheel has {executables[0]} without its executable bit"])

    def test_a_file_git_does_not_track(self):
        self.assertEqual(release.check_dist(self.make(extra="taskmine/__pycache__/cli.cpython-312.pyc")),
                         ["wheel has taskmine/__pycache__/cli.cpython-312.pyc, which git does not track (a build leftover?)"])

    def test_an_empty_dist_directory(self):
        (self.tmp / "dist").mkdir()
        self.assertEqual(len(release.check_dist(self.tmp / "dist")), 1)
