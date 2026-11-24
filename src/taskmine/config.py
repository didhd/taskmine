"""The repository recipe: `taskmine.toml`.

A recipe is written once per repository and says four things the tool cannot guess:
which commits are change units, which files are the test side, how a test runner is
installed and invoked (one `[[profiles]]` entry per runner), and what to leave out of the
snapshot. Everything else is mechanical.

Unknown keys are an error, never ignored: a misspelled `exclude` that silently did nothing
would put files into every task, and the person would only find out from a leak report.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from taskmine.globs import GlobSet


class ConfigError(ValueError):
    pass


DEFAULT_BINARY_EXT = (
    "png jpg jpeg gif webp ico icns bmp tif tiff heic avif psd sketch mp4 mov webm m4a mp3 wav ogg flac "
    "zip gz tgz xz bz2 br zst 7z rar jar war pdf woff woff2 ttf otf eot dmg exe msi wasm bin onnx gguf "
    "safetensors pt so dylib dll node a o class pyc car riv lottie keystore p12 der"
).split()

DEFAULT_TEST_PATHS = [
    "**/tests/**",
    "**/test/**",
    "**/__tests__/**",
    "**/*.test.*",
    "**/*.spec.*",
    "**/test_*.py",
    "**/*_test.py",
    "**/*_test.go",
]

DEFAULT_DOC_PATHS = ["**/*.md", "**/*.mdx", "**/*.txt", "**/CHANGELOG*", "**/LICENSE*", "docs/**"]


@dataclass
class Profile:
    name: str
    run: GlobSet
    image: str
    workdir: str
    command: str
    display_command: str
    install: str | None
    deps: list[str]
    deps_normalize: str | None
    timeout_sec: float
    cpus: int
    memory_mb: int
    env: dict[str, str]
    setup: str | None
    text_probe: str = "static"


@dataclass
class Config:
    path: Path
    repo_name: str
    github: str | None
    units_kind: str
    branch: str
    since: str | None
    pr_patterns: list[str]
    max_file_bytes: int
    snapshot_exclude: GlobSet
    binary_ext: frozenset[str]
    tests: GlobSet
    docs: GlobSet
    max_gold_files: int
    max_gold_lines: int
    max_run_files: int
    max_changed_file_bytes: int
    profiles: list[Profile] = field(default_factory=list)
    agent_timeout_sec: float = 3600.0
    storage_mb: int = 20480
    include_issue: bool = True
    allow_source_text: bool = False
    issue_max_chars: int = 4000

    def profile(self, name: str) -> Profile:
        for p in self.profiles:
            if p.name == name:
                return p
        raise ConfigError(f"no profile named {name!r} in {self.path}")


_SECTIONS = {
    "repo": {"name", "github"},
    "units": {"kind", "branch", "since", "pr_patterns"},
    "snapshot": {"max_file_bytes", "exclude", "binary_ext"},
    "tests": {"paths", "docs", "allow_source_text"},
    "limits": {"max_gold_files", "max_gold_lines", "max_run_files", "max_changed_file_bytes"},
    "task": {"agent_timeout_sec", "storage_mb", "include_issue", "issue_max_chars"},
    "profiles": None,
}
_PROFILE_KEYS = {
    "name", "run", "image", "workdir", "command", "display_command", "install", "deps",
    "deps_normalize", "timeout_sec", "cpus", "memory_mb", "env", "setup", "text_probe",
}


def _check_keys(where: str, got: dict, allowed: set[str]) -> None:
    unknown = sorted(set(got) - allowed)
    if unknown:
        raise ConfigError(f"{where}: unknown key(s) {', '.join(unknown)}; allowed: {', '.join(sorted(allowed))}")


def load(path: str | Path) -> Config:
    path = Path(path)
    try:
        data = tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise ConfigError(f"{path}: {e}") from e
    _check_keys(str(path), data, set(_SECTIONS))
    for sec, keys in _SECTIONS.items():
        if keys is not None and sec in data:
            _check_keys(f"{path} [{sec}]", data[sec], keys)

    repo = data.get("repo", {})
    units = data.get("units", {})
    snap = data.get("snapshot", {})
    tests = data.get("tests", {})
    limits = data.get("limits", {})
    task = data.get("task", {})
    if "name" not in repo:
        raise ConfigError(f"{path}: [repo] name is required (it names the tasks and their provenance)")
    kind = units.get("kind", "merges")
    if kind not in ("merges", "commits"):
        raise ConfigError(f"{path}: [units] kind must be 'merges' or 'commits', not {kind!r}")

    profiles = []
    for i, p in enumerate(data.get("profiles", [])):
        _check_keys(f"{path} [[profiles]] #{i + 1}", p, _PROFILE_KEYS)
        for req in ("name", "run", "image", "command"):
            if req not in p:
                raise ConfigError(f"{path} [[profiles]] #{i + 1}: {req!r} is required")
        if "{files}" not in p["command"] or "{junit}" not in p["command"]:
            raise ConfigError(
                f"{path} profile {p['name']!r}: command must contain {{files}} (the test files to run) "
                "and {junit} (where the runner writes its JUnit XML report)"
            )
        if p.get("text_probe", "static") not in ("node", "static", "off"):
            raise ConfigError(f"{path} profile {p['name']!r}: text_probe must be 'node', 'static' or 'off'")
        norm = p.get("deps_normalize")
        if norm not in (None, "npm"):
            raise ConfigError(f"{path} profile {p['name']!r}: deps_normalize must be 'npm' or absent")
        profiles.append(
            Profile(
                name=p["name"],
                run=GlobSet(p["run"]),
                image=p["image"],
                workdir=p.get("workdir", "."),
                command=p["command"],
                display_command=p.get("display_command", p["command"]),
                install=p.get("install"),
                deps=list(p.get("deps", [])),
                deps_normalize=norm,
                timeout_sec=float(p.get("timeout_sec", 900)),
                cpus=int(p.get("cpus", 2)),
                memory_mb=int(p.get("memory_mb", 4096)),
                env={str(k): str(v) for k, v in p.get("env", {}).items()},
                setup=p.get("setup"),
                text_probe=p.get("text_probe", "static"),
            )
        )
    if not profiles:
        raise ConfigError(f"{path}: at least one [[profiles]] entry is required")

    return Config(
        path=path,
        repo_name=repo["name"],
        github=repo.get("github"),
        units_kind=kind,
        branch=units.get("branch", "main"),
        since=units.get("since"),
        pr_patterns=list(
            units.get("pr_patterns", [r"^Merge pull request #(\d+) from ", r"\(#(\d+)\)$"])
        ),
        max_file_bytes=int(snap.get("max_file_bytes", 5_000_000)),
        snapshot_exclude=GlobSet(snap.get("exclude", [])),
        binary_ext=frozenset(e.lower() for e in snap.get("binary_ext", DEFAULT_BINARY_EXT)),
        tests=GlobSet(tests.get("paths", DEFAULT_TEST_PATHS)),
        docs=GlobSet(tests.get("docs", DEFAULT_DOC_PATHS)),
        max_gold_files=int(limits.get("max_gold_files", 40)),
        max_gold_lines=int(limits.get("max_gold_lines", 2000)),
        max_run_files=int(limits.get("max_run_files", 6)),
        max_changed_file_bytes=int(limits.get("max_changed_file_bytes", 20_000_000)),
        profiles=profiles,
        agent_timeout_sec=float(task.get("agent_timeout_sec", 3600)),
        storage_mb=int(task.get("storage_mb", 20480)),
        include_issue=bool(task.get("include_issue", True)),
        allow_source_text=bool(tests.get("allow_source_text", False)),
        issue_max_chars=int(task.get("issue_max_chars", 4000)),
    )
