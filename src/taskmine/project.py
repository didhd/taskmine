"""The project workspace: `.taskmine/` next to wherever you run taskmine.

    .taskmine/project.toml   which repository, which clone, which recipe (written by `init`)
    .taskmine/agents.toml    the agents to benchmark (written by `agents add`)
    .taskmine/out/           the dataset: candidates, drafts, validation, tasks/, bench runs

Both files are plain TOML that `init` and `agents` write and you may edit by hand.
"""

from __future__ import annotations

import json
import subprocess
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from taskmine import tomlw

HOME = Path(".taskmine")


class ProjectError(RuntimeError):
    pass


@dataclass
class Project:
    root: Path
    repo: str
    clone: Path
    recipe: Path
    out: Path
    agents: list[dict] = field(default_factory=list)


def gh(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(["gh", *args], capture_output=True, text=True, check=check)
    except FileNotFoundError as e:
        raise ProjectError("the GitHub CLI `gh` is not installed: https://cli.github.com") from e


def gh_user() -> str | None:
    p = gh("api", "user", "--jq", ".login", check=False)
    return p.stdout.strip() if p.returncode == 0 and p.stdout.strip() else None


def load(root: Path = HOME) -> Project:
    pf = root / "project.toml"
    if not pf.exists():
        raise ProjectError("no project here: run `taskmine init <owner/repo>` first")
    d = tomllib.loads(pf.read_text())["project"]
    agents = []
    af = root / "agents.toml"
    if af.exists():
        agents = tomllib.loads(af.read_text()).get("agents", [])
    return Project(root, d["repo"], Path(d["clone"]), Path(d["recipe"]), Path(d["out"]), agents)


def save(p: Project) -> None:
    p.root.mkdir(parents=True, exist_ok=True)
    (p.root / "project.toml").write_text(tomlw.dumps({"project": {
        "repo": p.repo, "clone": str(p.clone), "recipe": str(p.recipe), "out": str(p.out)}}))
    save_agents(p)


def save_agents(p: Project) -> None:
    lines = ["# Agents to benchmark. `taskmine agents add` writes this file; edit it freely.", ""]
    for a in p.agents:
        lines.append("[[agents]]")
        for k in ("name", "agent", "model", "provider"):
            if a.get(k) is not None:
                lines.append(f"{k} = {json.dumps(a[k])}")
        if a.get("env"):
            lines.append("env = { " + ", ".join(f"{k} = {json.dumps(v)}" for k, v in sorted(a["env"].items())) + " }")
        lines.append("")
    (p.root / "agents.toml").write_text("\n".join(lines))
