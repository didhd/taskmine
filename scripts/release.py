"""Release checks, used by CI and by the release workflow. Standard library only.

    python scripts/release.py version              the package version (read from the source, not imported)
    python scripts/release.py check-tag v0.1.0     fail unless the tag is v + that version
    python scripts/release.py notes 0.1.0          print that version's CHANGELOG.md section
    python scripts/release.py notes 0.1.0 --dated  ... and fail unless the section has a release date
    python scripts/release.py check-dist dist      every tracked file under src/taskmine is in the wheel
                                                   and the sdist, scripts keep their mode, versions agree
"""

from __future__ import annotations

import argparse
import ast
import re
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = "src/taskmine"


class ReleaseError(RuntimeError):
    pass


def version(root: Path = ROOT) -> str:
    tree = ast.parse((root / PACKAGE / "__init__.py").read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "__version__" for t in node.targets):
            return ast.literal_eval(node.value)
    raise ReleaseError(f"no __version__ in {PACKAGE}/__init__.py")


def check_tag(tag: str, root: Path = ROOT) -> str:
    want = "v" + version(root)
    if tag != want:
        raise ReleaseError(f"tag {tag} does not match the package version: expected {want}")
    return want


_HEADING = re.compile(r"^## \[(?P<v>[^\]]+)\](?: - (?P<date>.+))?\s*$")


def notes(ver: str, changelog: str, dated: bool = False) -> str:
    """The body of `## [ver] - date`, without the heading and the link references at the end."""
    lines = changelog.splitlines()
    start = None
    for i, line in enumerate(lines):
        m = _HEADING.match(line)
        if m and m["v"] == ver:
            if dated and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", (m["date"] or "").strip()):
                raise ReleaseError(f"CHANGELOG.md: [{ver}] has no release date ({line.strip()!r}); date it before tagging")
            start = i + 1
            break
    if start is None:
        raise ReleaseError(f"CHANGELOG.md has no section for {ver}")
    body = []
    for line in lines[start:]:
        if line.startswith("## ") or re.match(r"^\[[^\]]+\]: ", line):
            break
        body.append(line)
    text = "\n".join(body).strip()
    if not text:
        raise ReleaseError(f"CHANGELOG.md: the section for {ver} is empty")
    return text + "\n"


def tracked_package_files(root: Path = ROOT) -> dict[str, bool]:
    """{path under taskmine/: executable} for every file git tracks under src/taskmine."""
    out = subprocess.run(["git", "-C", str(root), "ls-files", "-s", "--", PACKAGE], capture_output=True, text=True,
                         check=True).stdout
    files = {}
    for line in out.splitlines():
        meta, path = line.split("\t", 1)
        files[path[len("src/"):]] = meta.split()[0] == "100755"
    return files


def check_dist(dist: Path, root: Path = ROOT) -> list[str]:
    """Problems with the built wheel and sdist. Empty means both carry the whole package."""
    ver = version(root)
    want = tracked_package_files(root)
    problems = []
    wheels = sorted(dist.glob(f"taskmine-{ver}-*.whl"))
    sdists = sorted(dist.glob(f"taskmine-{ver}.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        return [f"expected one wheel and one sdist for {ver} in {dist}, found {[p.name for p in dist.iterdir()]}"]
    with zipfile.ZipFile(wheels[0]) as z:
        infos = {i.filename: i for i in z.infolist()}
        meta = z.read(f"taskmine-{ver}.dist-info/METADATA").decode()
    if f"\nVersion: {ver}\n" not in meta:
        problems.append(f"wheel METADATA does not say Version: {ver}")
    for path, executable in sorted(want.items()):
        info = infos.get(path)
        if info is None:
            problems.append(f"wheel is missing {path}")
        elif executable and not (info.external_attr >> 16) & 0o111:
            problems.append(f"wheel has {path} without its executable bit")
    extra = [n for n in infos if n.startswith("taskmine/") and not n.endswith("/") and n not in want]
    problems += [f"wheel has {n}, which git does not track (a build leftover?)" for n in sorted(extra)]
    with tarfile.open(sdists[0]) as t:
        prefix = f"taskmine-{ver}/src/"
        names = {m.name[len(prefix):] for m in t.getmembers() if m.name.startswith(prefix)}
    problems += [f"sdist is missing {path}" for path in sorted(set(want) - names)]
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="release.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("version")
    p = sub.add_parser("check-tag")
    p.add_argument("tag")
    p = sub.add_parser("notes")
    p.add_argument("version")
    p.add_argument("--dated", action="store_true")
    p = sub.add_parser("check-dist")
    p.add_argument("dist")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "version":
            print(version())
        elif a.cmd == "check-tag":
            print(f"{check_tag(a.tag)} matches the package version")
        elif a.cmd == "notes":
            sys.stdout.write(notes(a.version, (ROOT / "CHANGELOG.md").read_text(), a.dated))
        else:
            problems = check_dist(Path(a.dist))
            for pr in problems:
                print(pr)
            if problems:
                return 1
            print(f"wheel and sdist carry all {len(tracked_package_files())} package files of taskmine {version()}")
    except ReleaseError as e:
        print(f"release: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
