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
import re
from dataclasses import dataclass
from pathlib import Path

# Harbor ships no default Strands agent: its runner calls a factory. taskmine's gives the agent the
# runner's shell and file editor (templates/taskmine_strands.py).
STRANDS_FACTORY = ["--ak", f"factory_path={(Path(__file__).parent / 'templates' / 'taskmine_strands.py').resolve()}",
                   "--ak", "factory=taskmine_strands:create_agent"]


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
    "strands": Kind("strands", ("bedrock", "openai"), "Strands Agents SDK"),
    "opencode": Kind("opencode", ("bedrock", "anthropic", "openai"), "OpenCode"),
    "aider": Kind("aider", ("anthropic", "openai"), "Aider"),
    "goose": Kind("goose", ("anthropic", "openai", "bedrock"), "Goose"),
}

# Only these pairs have been run end to end; the rest are passed to Harbor as they are.
VERIFIED = {("claude-code", "bedrock"), ("codex", "bedrock")}

# Agents that speak an OpenAI API: on Bedrock they reach the model through the gateway's LiteLLM.
OPENAI_PROTOCOL = {"codex", "opencode", "strands"}


def entry(agent: str, provider: str, model: str, name: str | None, env: dict[str, str]) -> dict:
    if agent not in CATALOG:
        raise AgentError(f"unknown agent {agent!r}; known: {', '.join(CATALOG)}")
    kind = CATALOG[agent]
    if provider not in kind.providers:
        raise AgentError(f"{agent} runs with: {', '.join(kind.providers)} (not {provider!r})")
    m = model.split("/")[-1].lower()
    m = re.sub(r"^(us|eu|apac|jp|au|global)\.", "", m)        # cross-region inference profile prefix
    m = re.sub(r"^(anthropic|openai|meta|amazon|mistral|deepseek|qwen)\.", "", m)
    m = re.sub(r"^claude-", "", m)
    slug = re.sub(r"[^a-z0-9.]+", "-", m).strip("-")
    return {"name": name or f"{agent}-{slug}", "agent": agent, "provider": provider, "model": model,
            "env": dict(env)}




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
    if a["agent"] in ("oracle", "nop"):  # Harbor's reference agents: no model, no credentials
        return ["-a", a["agent"]], {}
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
    elif a["agent"] in OPENAI_PROTOCOL and provider == "bedrock":
        if "openai_url" not in gw:
            raise AgentError(f"the gateway has no OpenAI endpoint: restart it after adding the {a['agent']} agent")
        if a["agent"] == "opencode":
            # OpenCode's own provider config: an OpenAI-compatible provider pointing at the gateway.
            cfg = {"provider": {"gateway": {
                "npm": "@ai-sdk/openai-compatible", "name": "taskmine gateway",
                "options": {"baseURL": gw["openai_url"], "apiKey": "{env:TASKMINE_GATEWAY_KEY}"},
                "models": {model: {"name": model}}}}}
            args += ["-m", f"gateway/{model}", "--ak", "opencode_config=" + json.dumps(cfg, separators=(",", ":"))]
            ae["TASKMINE_GATEWAY_KEY"] = gw["openai_key"]
        elif a["agent"] == "strands":
            args += ["-m", f"openai/{model}", *STRANDS_FACTORY]
            env.update(OPENAI_BASE_URL=gw["openai_url"], OPENAI_API_KEY=gw["openai_key"])
        else:
            args += ["-m", model]
            env.update(OPENAI_BASE_URL=gw["openai_url"], OPENAI_API_KEY=gw["openai_key"])
    else:
        args += ["-m", model]
    ae.update(a.get("env") or {})
    for k, v in ae.items():
        args += ["--ae", f"{k}={v}"]
    return args, env
