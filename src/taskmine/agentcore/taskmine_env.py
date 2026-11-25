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

Grading is always staged (PR #120's `test.sh --stage untrusted|trusted`). Where PR #120 would grade
a trial unsealed instead (staged_grader=false, a test.sh without --stage, an image without the
grader user or the root-only baseline), this environment refuses to grade it: the trial ends in an
error, never in a reward that the agent's code could have written.
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
TOOLCHAIN_VARS = ("CARGO_HOME|RUSTUP_HOME|RUST_VERSION|GOPATH|GOROOT|GOTOOLCHAIN|GOFLAGS|JAVA_HOME|JAVA_VERSION|MAVEN_HOME|"
                  "MAVEN_CONFIG|MAVEN_OPTS|GRADLE_HOME|GRADLE_USER_HOME|NUGET_PACKAGES|DOTNET_[A-Z_]+|ASPNETCORE_[A-Z_]+|"
                  "GEM_HOME|GEM_PATH|BUNDLE_[A-Z_]+|RUBY_[A-Z_]+|PHP_[A-Z_]+|PHPIZE_DEPS|COMPOSER_[A-Z_]+|NODE_[A-Z_]+|"
                  "NPM_CONFIG_[A-Z_]+|YARN_[A-Z_]+|PYTHONPATH|PYTHONUNBUFFERED|PIP_[A-Z_]+|CI")
# The trusted grading stage starts from as-grader, which finds bash through the PATH in task.env. A
# folder on that PATH that the agent can write (the ruby image's GEM_HOME is mode 1777) would let it
# plant the program the trusted stage runs. Such a folder outside the agent's own trees loses its
# group and world write bit; one inside them (/app, /work, /home, /tmp, /logs) fails the build.
PATH_GUARD = ("RUN P=$(sed -n 's/^PATH=//p' /opt/firewall/task.env | tail -1); IFS=:; for d in $P; do "
              "case \"$d\" in /*) ;; *) echo \"taskmine: PATH entry '$d' is not absolute\" >&2; exit 1 ;; esac; "
              "e=$d; while [ ! -e \"$e\" ]; do e=$(dirname \"$e\"); done; "
              "if setpriv --reuid=2000 --regid=2000 --clear-groups test -w \"$e\" "
              "|| setpriv --reuid=2001 --regid=2001 --clear-groups test -w \"$e\"; then "
              "case \"$e\" in /app|/app/*|/work|/work/*|/home|/home/*|/tmp|/tmp/*|/var/tmp|/var/tmp/*|/logs|/logs/*) "
              "echo \"taskmine: PATH entry $d is in $e, which the agent can write, so the trusted grading stage could run "
              "its programs\" >&2; exit 1 ;; esac; "
              "chown root:root \"$e\" && chmod go-w \"$e\" && echo \"taskmine: $e (on PATH) is no longer writable by the agent\"; fi; "
              "[ -d \"$e\" ] && find \"$e\" -maxdepth 1 ! -type l \\( -perm -0002 -o -perm -0020 \\) -exec chmod go-w {} +; "
              "done; true\n")
ENV_TAIL = ("\n# ===== taskmine: the image's toolchain variables for the agent and the grader =====\n"
            f"RUN printenv | grep -E '^({TOOLCHAIN_VARS})=' >> /opt/firewall/task.env; true\n"
            "# ===== taskmine: no folder on PATH that the agent can write =====\n" + PATH_GUARD)
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
    # PR #120's layer gives sessions only PATH, VIRTUAL_ENV, UV_*, LANG, LC_ALL and HOME from the image
    # (its as-agent starts commands with `env -i`). Toolchains that live outside those (rustup's
    # RUSTUP_HOME and CARGO_HOME, Ruby's GEM_HOME, a NuGet or Maven cache path, a JDK's JAVA_HOME) then
    # break in the session: append their variables to its task.env, after the layer has written it.
    _base_key, _base_ctx = ace.image_key, ace.env_dir_context
    _e = "e" + hashlib.sha256(ENV_TAIL.encode()).hexdigest()[:2]
    ace.image_key = lambda environment_dir, name, verifier: _base_key(environment_dir, name, verifier) + _e

    def _env_context(environment_dir):
        inner = _base_ctx(environment_dir)

        def ctx(tag, module, dest: Path):
            inner(tag, module, dest)
            (dest / "Dockerfile").write_text((dest / "Dockerfile").read_text() + ENV_TAIL)
        return ctx
    ace.env_dir_context = _env_context
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
    """AgentCoreEnvironment with the platform version taken from TASKMINE_AC_PLATFORM, which grades in stages or not at all."""

    def __init__(self, *args, platform: str | None = None, **kwargs):
        super().__init__(*args, platform=platform or PLATFORM, **kwargs)

    async def _grade_shared(self, command, cwd, env):
        """PR #120's staged grade. Where PR #120 would return None (grade unsealed, as uid agent), refuse."""
        reason = "staged_grader=false" if not self._staged_grader else await self._unstaged_reason()
        if reason is not None:
            why = f"staged grading is not possible here ({reason}), and taskmine does not grade unsealed on AgentCore"
            self._event(event="grader_mode", mode="refused", reason=why, command=command[:300])
            self._event(event="grader_refused", reason=why, command=command[:300])
            raise RuntimeError(f"not grading: {why}")
        return await super()._grade_shared(command, cwd, env)
