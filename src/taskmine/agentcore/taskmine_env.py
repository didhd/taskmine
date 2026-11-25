"""taskmine's layer over PR #120's AgentCoreEnvironment (pr120/harbor_env/agentcore_env.py, unchanged).

    harbor run -e taskmine_env:TaskmineAgentCoreEnvironment ...

Runs in the harness venv (Harbor and a boto3 that knows AgentCore). It changes one thing, read from
the environment, never from a task: TASKMINE_AC_PLATFORM, the AgentCore platform version that new
runtimes get.
- V1 is what PR #120 tested: every session pulls the image and starts the container.
- V2 starts the container once, when the runtime is created, snapshots the microVM after its first
  healthy /ping, and restores that snapshot for every session. PR #120's shim starts the firewall
  before it answers /ping, so the firewall is part of the snapshot.

A V2 runtime is named `<V1 name>_v2`, so both can serve the same image side by side.
"""

from __future__ import annotations

import functools
import os

import p2_v2 as p2
from harbor_env.agentcore_env import AgentCoreEnvironment

PLATFORM = os.environ.get("TASKMINE_AC_PLATFORM", "V1")
if PLATFORM not in ("V1", "V2"):
    raise ValueError(f"TASKMINE_AC_PLATFORM must be V1 or V2, not {PLATFORM!r}")

if getattr(p2.deploy, "func", None) is None:  # once per process, even if this module is imported twice
    p2.deploy = functools.partial(p2.deploy, platform=PLATFORM)
    if PLATFORM == "V2":
        _v1_name = p2.runtime_name
        p2.runtime_name = lambda task: _v1_name(task) + "_v2"


class TaskmineAgentCoreEnvironment(AgentCoreEnvironment):
    """AgentCoreEnvironment with the platform version taken from TASKMINE_AC_PLATFORM."""

    def __init__(self, *args, platform: str | None = None, **kwargs):
        super().__init__(*args, platform=platform or PLATFORM, **kwargs)
