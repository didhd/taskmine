"""Coding agents to benchmark, and how each one is run under Harbor.

An agent entry is a Harbor agent plus a model and a provider:

    taskmine agents add claude-code --provider bedrock --model us.anthropic.claude-sonnet-5-5
    taskmine agents add codex       --provider bedrock --model openai.gpt-oss-120b-1:0

The provider decides how the agent reaches its model. For `bedrock` the agent talks to the
gateway on the Docker host (`taskmine gateway`), which holds the AWS credentials; the task container
never sees one. For `anthropic` and `openai` Harbor passes ANTHROPIC_API_KEY / OPENAI_API_KEY from
your environment.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass


class AgentError(ValueError):
    pass


@dataclass(frozen=True)
class Kind:
    harbor: str  # Harbor's agent name
    providers: tuple[str, ...]
    note: str


CATALOG: dict[str, Kind] = {
    "claude-code": Kind("claude-code", ("bedrock", "anthropic"), "Anthropic's Claude Code CLI"),
    "codex": Kind("codex", ("bedrock", "openai"), "OpenAI's Codex CLI"),
    "cursor": Kind("cursor-cli", ("cursor",), "Cursor's agent CLI"),
    "gemini": Kind("gemini-cli", ("google",), "Google's Gemini CLI"),
    "strands": Kind("strands", ("bedrock",), "Strands Agents SDK"),
    "opencode": Kind("opencode", ("anthropic", "openai", "bedrock"), "OpenCode"),
    "aider": Kind("aider", ("anthropic", "openai"), "Aider"),
    "goose": Kind("goose", ("anthropic", "openai", "bedrock"), "Goose"),
}

# Only these pairs have been run end to end; the rest are passed to Harbor as they are.
VERIFIED = {("claude-code", "bedrock"), ("codex", "bedrock")}


def entry(agent: str, provider: str, model: str, name: str | None, env: dict[str, str]) -> dict:
    if agent not in CATALOG:
        raise AgentError(f"unknown agent {agent!r}; known: {', '.join(CATALOG)}")
    kind = CATALOG[agent]
    if provider not in kind.providers:
        raise AgentError(f"{agent} runs with: {', '.join(kind.providers)} (not {provider!r})")
    m = model.split("/")[-1].lower()
    m = re.sub(r"^(us|eu|apac|jp|au|global)\.", "", m)        # cross-region inference profile prefix
    m = re.sub(r"^(anthropic|openai|meta|amazon|mistral|deepseek|qwen)\.", "", m)
    m = re.sub(r"^claude-", "", m) if agent == "claude-code" else m
    slug = re.sub(r"[^a-z0-9.]+", "-", m).strip("-")
    return {"name": name or f"{agent}-{slug}", "agent": agent, "provider": provider, "model": model,
            "env": dict(env)}


def aws_credentials() -> dict[str, str]:
    """AWS credentials for the agent containers: the environment's, else the AWS CLI's resolution."""
    keys = ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN")
    if os.environ.get("AWS_ACCESS_KEY_ID") and os.environ.get("AWS_SECRET_ACCESS_KEY"):
        return {k: os.environ[k] for k in keys if os.environ.get(k)}
    if os.environ.get("AWS_BEARER_TOKEN_BEDROCK"):
        return {"AWS_BEARER_TOKEN_BEDROCK": os.environ["AWS_BEARER_TOKEN_BEDROCK"]}
    p = subprocess.run(["aws", "configure", "export-credentials", "--format", "process"],
                       capture_output=True, text=True)
    if p.returncode != 0:
        raise AgentError("no AWS credentials for a bedrock agent: set them in the environment, "
                         "or configure the AWS CLI (`aws configure export-credentials` failed)")
    d = json.loads(p.stdout)
    out = {"AWS_ACCESS_KEY_ID": d["AccessKeyId"], "AWS_SECRET_ACCESS_KEY": d["SecretAccessKey"]}
    if d.get("SessionToken"):
        out["AWS_SESSION_TOKEN"] = d["SessionToken"]
    return out


def gateway_info(root) -> dict:
    import tomllib
    from pathlib import Path

    f = Path(root) / "gateway.toml"
    return tomllib.loads(f.read_text())["gateway"] if f.exists() else {}


def harbor_args(a: dict, region: str, root=".taskmine") -> tuple[list[str], dict[str, str]]:
    """(harbor run arguments for this agent, extra environment for the harbor process).

    Bedrock agents go through the gateway (`taskmine gateway`): the agent container gets the
    gateway's address and never an AWS credential.
    """
    kind = CATALOG[a["agent"]]
    env: dict[str, str] = {}
    args = ["-a", kind.harbor]
    provider, model = a["provider"], a["model"]
    ae: dict[str, str] = {}
    gw = gateway_info(root) if provider == "bedrock" else {}
    if provider == "bedrock" and not gw:
        raise AgentError("bedrock agents need the gateway: run `taskmine gateway` on this host first")
    if a["agent"] == "claude-code":
        if provider == "bedrock":
            args += ["-m", f"bedrock/{model}"]
            ae.update(CLAUDE_CODE_USE_BEDROCK="1", CLAUDE_CODE_SKIP_BEDROCK_AUTH="1",
                      ANTHROPIC_BEDROCK_BASE_URL=gw["bedrock_url"], AWS_REGION=gw.get("region", region))
        else:
            args += ["-m", model]
    elif a["agent"] == "codex":
        args += ["-m", model]
        if provider == "bedrock":
            if "openai_url" not in gw:
                raise AgentError("the gateway has no OpenAI endpoint: restart it after adding the codex agent")
            env.update(OPENAI_BASE_URL=gw["openai_url"], OPENAI_API_KEY=gw["openai_key"])
    else:
        args += ["-m", model]
    ae.update(a.get("env") or {})
    for k, v in ae.items():
        args += ["--ae", f"{k}={v}"]
    return args, env
