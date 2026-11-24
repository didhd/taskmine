"""taskmine's layer over PR #120's AgentCoreEnvironment (pr120/harbor_env/agentcore_env.py, unchanged).

    harbor run -e taskmine_env:TaskmineAgentCoreEnvironment ...

Runs in the harness venv (Harbor and a boto3 that knows AgentCore). It changes one thing, read from
the environment, never from a task: TASKMINE_AC_PLATFORM, the AgentCore platform version that new
runtimes get. V1 is what PR #120 tested and caps an image near 2 GB compressed; V2 allows 10 GB and
restores each session from a snapshot of the initialized environment.
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


class TaskmineAgentCoreEnvironment(AgentCoreEnvironment):
    """AgentCoreEnvironment with the platform version taken from TASKMINE_AC_PLATFORM."""

    def __init__(self, *args, platform: str | None = None, **kwargs):
        super().__init__(*args, platform=platform or PLATFORM, **kwargs)
