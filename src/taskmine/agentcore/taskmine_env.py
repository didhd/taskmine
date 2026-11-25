"""taskmine's layer over PR #120's AgentCoreEnvironment (pr120/harbor_env/agentcore_env.py, unchanged).

    harbor run -e taskmine_env:TaskmineAgentCoreEnvironment ...

Runs in the harness venv, with Harbor and a boto3 that knows AgentCore. It reads two settings from
the environment, never from a task.

TASKMINE_AC_PLATFORM is the AgentCore platform version that new runtimes get:
- V1 is what PR #120 tested. Every session pulls the image and starts the container.
- V2 starts the container once, when the runtime is created, snapshots the microVM after its first
  healthy /ping, and restores that snapshot for every session. PR #120's shim starts the firewall
  before it answers /ping, so the firewall is part of the snapshot. A V2 runtime is named
  `<V1 name>_v2`, so both can serve the same image side by side.

TASKMINE_AC_WARMUP (V2 only) adds a build hook (taskmine_warmup.py):
- `disk` (or `1`) reads the files sessions will need while the snapshot is built;
- `ram` also copies the repository into a tmpfs, which is part of the memory snapshot.
Those images get their own tag: `w`, then a short hash of the hook, its mode and the Dockerfile tail.
"""

from __future__ import annotations

import functools
import hashlib
import os
import shutil
from pathlib import Path

import p2_v2 as p2
from harbor_env import agentcore_env as ace
from harbor_env.agentcore_env import AgentCoreEnvironment

PLATFORM = os.environ.get("TASKMINE_AC_PLATFORM", "V1")
if PLATFORM not in ("V1", "V2"):
    raise ValueError(f"TASKMINE_AC_PLATFORM must be V1 or V2, not {PLATFORM!r}")
_MODES = {"": None, "0": None, "1": "disk", "disk": "disk", "ram": "ram"}
_mode = os.environ.get("TASKMINE_AC_WARMUP", "")
if _mode not in _MODES:
    raise ValueError(f"TASKMINE_AC_WARMUP must be disk or ram, not {_mode!r}")
WARMUP = _MODES[_mode] if PLATFORM == "V2" else None
HOOKS = {"build": {"warmup": {"action": {"httpPost": {"path": "/warmup", "port": 9090}}}}}
WARMUP_FILE = Path(__file__).resolve().parent / "taskmine_warmup.py"
WARMUP_TAIL = ('\n# ===== taskmine: V2 build hook (taskmine_warmup.py), then PR #120\'s shim =====\n'
               f'ENV TASKMINE_WARMUP_MODE={WARMUP or "disk"}\n'
               'COPY taskmine_warmup.py /opt/taskmine/warmup.py\nCMD ["python3", "/opt/taskmine/warmup.py"]\n')


def _with_hooks(call):
    @functools.wraps(call)
    def wrapped(**kw):
        if kw.get("platformVersion") == "V2":
            kw["hooksConfiguration"] = HOOKS  # an update replaces the hooks, so every call carries them
        return call(**kw)
    return wrapped


if not getattr(p2, "_taskmine_patched", False):  # once per process, even if this module is imported twice
    p2._taskmine_patched = True
    p2.deploy = functools.partial(p2.deploy, platform=PLATFORM)
    if PLATFORM == "V2":
        _v1_name = p2.runtime_name
        p2.runtime_name = lambda task: _v1_name(task) + "_v2"
    if WARMUP:
        _key, _ctx = ace.image_key, ace.env_dir_context
        # The hook changes the image, so its tag does too: `w` and a hash of the hook file and the Dockerfile tail.
        _w = "w" + hashlib.sha256(WARMUP_FILE.read_bytes() + WARMUP_TAIL.encode()).hexdigest()[:2]  # runtime names stop at 48 characters
        ace.image_key = lambda environment_dir, name, verifier: _key(environment_dir, name, verifier) + _w

        def _context(environment_dir):
            inner = _ctx(environment_dir)

            def ctx(tag, module, dest: Path):
                inner(tag, module, dest)
                shutil.copy2(WARMUP_FILE, dest / WARMUP_FILE.name)
                (dest / "Dockerfile").write_text((dest / "Dockerfile").read_text() + WARMUP_TAIL)
            return ctx
        ace.env_dir_context = _context
        p2.ctl.create_agent_runtime = _with_hooks(p2.ctl.create_agent_runtime)
        p2.ctl.update_agent_runtime = _with_hooks(p2.ctl.update_agent_runtime)


class TaskmineAgentCoreEnvironment(AgentCoreEnvironment):
    """AgentCoreEnvironment with the platform version taken from TASKMINE_AC_PLATFORM."""

    def __init__(self, *args, platform: str | None = None, **kwargs):
        super().__init__(*args, platform=platform or PLATFORM, **kwargs)
