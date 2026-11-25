"""The repository recipe: `taskmine.toml`.

A recipe is written once per repository and says four things the tool cannot guess:
which commits are change units, which files are the test side, how a test runner is
installed and invoked (one `[[profiles]]` entry per runner), and what to leave out of the
snapshot. Everything else is mechanical.

Unknown keys are an error, never ignored: a misspelled `exclude` that silently did nothing
would put files into every task, and the person would only find out from a leak report.
"""

from __future__ import annotations

import datetime
import re
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


_KIND_NAMES = {str: "a string", int: "an integer", float: "a number", bool: "true or false",
               list: "a list of strings", dict: "a table"}


def _get(where: str, d: dict, key: str, default, kind: type):
    """`d[key]` if it has the expected TOML type, else a ConfigError naming the key.

    Read with Python's own conversions instead, a string where a list belongs becomes a list of
    one-character globs (`run = "tests/*.py"` matches every top-level file) and `"no"` is true.
    """
    if key not in d:
        return default
    v = d[key]
    if kind is float and isinstance(v, int) and not isinstance(v, bool):
        return float(v)
    if kind is str and key == "since" and isinstance(v, (datetime.date, datetime.datetime)):
        return v.isoformat()  # an unquoted TOML date
    ok = isinstance(v, kind) and not (kind in (int, float) and isinstance(v, bool))
    if ok and kind is list:
        ok = all(isinstance(x, str) for x in v)
    if not ok:
        raise ConfigError(f"{where}: {key} must be {_KIND_NAMES[kind]}, not {v!r}")
    return v


def _patterns(where: str, patterns: list[str]) -> list[str]:
    for pat in patterns:
        try:
            rx = re.compile(pat)
        except re.error as e:
            raise ConfigError(f"{where}: pr_patterns {pat!r} is not a regular expression: {e}") from e
        if rx.groups < 1:
            raise ConfigError(f"{where}: pr_patterns {pat!r} needs a group that captures the pull request number")
    return patterns


def load(path: str | Path) -> Config:
    path = Path(path)
    try:
        data = tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise ConfigError(f"{path}: {e}") from e
    _check_keys(str(path), data, set(_SECTIONS))
    for sec, keys in _SECTIONS.items():
        if keys is not None and sec in data:
            if not isinstance(data[sec], dict):
                raise ConfigError(f"{path}: [{sec}] must be a table")
            _check_keys(f"{path} [{sec}]", data[sec], keys)
    raw_profiles = data.get("profiles", [])
    if not isinstance(raw_profiles, list) or not all(isinstance(p, dict) for p in raw_profiles):
        raise ConfigError(f"{path}: profiles must be [[profiles]] entries (an array of tables)")

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
    w = {sec: f"{path} [{sec}]" for sec in _SECTIONS}
    github = _get(w["repo"], repo, "github", None, str)
    if github is not None and github.count("/") != 1:
        raise ConfigError(f"{w['repo']}: github must be owner/name, not {github!r}")

    profiles = []
    for i, p in enumerate(raw_profiles):
        where = f"{path} [[profiles]] #{i + 1}"
        _check_keys(where, p, _PROFILE_KEYS)
        for req in ("name", "run", "image", "command"):
            if req not in p:
                raise ConfigError(f"{where}: {req!r} is required")
        command = _get(where, p, "command", None, str)
        if "{files}" not in command or "{junit}" not in command:
            raise ConfigError(
                f"{path} profile {p['name']!r}: command must contain {{files}} (the test files to run) "
                "and {junit} (where the runner writes its JUnit XML report)"
            )
        if p.get("text_probe", "static") not in ("node", "static", "off"):
            raise ConfigError(f"{path} profile {p['name']!r}: text_probe must be 'node', 'static' or 'off'")
        norm = p.get("deps_normalize")
        if norm not in (None, "npm"):
            raise ConfigError(f"{path} profile {p['name']!r}: deps_normalize must be 'npm' or absent")
        env = _get(where, p, "env", {}, dict)
        profiles.append(
            Profile(
                name=_get(where, p, "name", None, str),
                run=GlobSet(_get(where, p, "run", None, list)),
                image=_get(where, p, "image", None, str),
                workdir=_get(where, p, "workdir", ".", str),
                command=command,
                display_command=_get(where, p, "display_command", command, str),
                install=_get(where, p, "install", None, str),
                deps=list(_get(where, p, "deps", [], list)),
                deps_normalize=norm,
                timeout_sec=_get(where, p, "timeout_sec", 900.0, float),
                cpus=_get(where, p, "cpus", 2, int),
                memory_mb=_get(where, p, "memory_mb", 4096, int),
                env={str(k): str(v) for k, v in env.items()},
                setup=_get(where, p, "setup", None, str),
                text_probe=p.get("text_probe", "static"),
            )
        )
    if not profiles:
        raise ConfigError(f"{path}: at least one [[profiles]] entry is required")

    return Config(
        path=path,
        repo_name=_get(w["repo"], repo, "name", None, str),
        github=github,
        units_kind=kind,
        branch=_get(w["units"], units, "branch", "main", str),
        since=_get(w["units"], units, "since", None, str),
        pr_patterns=_patterns(w["units"], list(
            _get(w["units"], units, "pr_patterns", [r"^Merge pull request #(\d+) from ", r"\(#(\d+)\)$"], list)
        )),
        max_file_bytes=_get(w["snapshot"], snap, "max_file_bytes", 5_000_000, int),
        snapshot_exclude=GlobSet(_get(w["snapshot"], snap, "exclude", [], list)),
        binary_ext=frozenset(e.lower() for e in _get(w["snapshot"], snap, "binary_ext", DEFAULT_BINARY_EXT, list)),
        tests=GlobSet(_get(w["tests"], tests, "paths", DEFAULT_TEST_PATHS, list)),
        docs=GlobSet(_get(w["tests"], tests, "docs", DEFAULT_DOC_PATHS, list)),
        max_gold_files=_get(w["limits"], limits, "max_gold_files", 40, int),
        max_gold_lines=_get(w["limits"], limits, "max_gold_lines", 2000, int),
        max_run_files=_get(w["limits"], limits, "max_run_files", 6, int),
        max_changed_file_bytes=_get(w["limits"], limits, "max_changed_file_bytes", 20_000_000, int),
        profiles=profiles,
        agent_timeout_sec=_get(w["task"], task, "agent_timeout_sec", 3600.0, float),
        storage_mb=_get(w["task"], task, "storage_mb", 20480, int),
        include_issue=_get(w["task"], task, "include_issue", True, bool),
        allow_source_text=_get(w["tests"], tests, "allow_source_text", False, bool),
        issue_max_chars=_get(w["task"], task, "issue_max_chars", 4000, int),
    )
