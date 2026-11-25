"""The container command of a V2 image with taskmine's build hook: a warmup endpoint, then PR #120's shim.

AgentCore Runtime V2 builds a snapshot of the microVM once, then restores it for every session.
Image files are fetched block by block the first time a restored session reads them ("most
noticeable in the first sessions after you create or update your agent"), and a test run that reads
thousands of small files (node_modules) pays for every one. AgentCore calls a build hook twice
while it builds the snapshot; the two calls share 120 seconds:
- `memory`, before the snapshot. What is in memory when it returns is in the snapshot, including a
  RAM-backed file system. In `ram` mode the repository (`/app`, with its installed dependencies)
  is copied into a tmpfs that is then bound over `/app`, so sessions read it at memory speed.
- `disk`, on a test restore: "do once what your agent does on its first request". It runs what a
  validation run starts with (git in the repository, node, python), then reads the harness files
  for the time left. Reads stop after 90 s, checked between 1 MB reads: a deadline checked only
  between files ran one call to 121 s and failed the runtime (measured).

TASKMINE_WARMUP_MODE (baked into the image by taskmine_env.py): `disk` or `ram`.

The endpoint listens on :9090 in the main network namespace. The agent runs in PR #120's empty
namespace and cannot reach it. It only reads and copies files and answers with counts.
"""

import json
import os
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 9090
MODE = os.environ.get("TASKMINE_WARMUP_MODE", "disk")
APP = "/app"
RAM = "/run/taskmine-ram"
HARNESS = ("/usr/local/bin/node", "/usr/local/lib/node_modules", "/installed-agent", "/opt/uv-python")
FIRST_WORK = (["git", "-C", APP, "status", "--short"], ["node", "-e", "require('fs'); require('child_process')"],
              ["python3", "-c", "import json, subprocess, xml.etree.ElementTree"])
BUDGET_SECS = 90


def read_tree(roots, deadline: float) -> dict:
    files = size = 0
    for root in roots:
        walk = [(os.path.dirname(root), [], [os.path.basename(root)])] if os.path.isfile(root) else os.walk(root)
        for dirpath, _dirs, names in walk:
            for name in names:
                if time.time() > deadline:
                    return {"files": files, "bytes": size, "complete": False}
                try:
                    with open(os.path.join(dirpath, name), "rb") as f:
                        while chunk := f.read(1 << 20):
                            size += len(chunk)
                            if time.time() > deadline:
                                return {"files": files, "bytes": size, "complete": False}
                    files += 1
                except OSError:
                    pass
    return {"files": files, "bytes": size, "complete": True}


def app_to_ram(deadline: float) -> dict:
    """Copy /app into a tmpfs and bind it over /app, keeping its owner and mode (the agent user owns it)."""
    if os.path.ismount(APP):
        return {"ram": "already mounted"}
    total = 0
    for dirpath, _dirs, names in os.walk(APP):
        for n in names:
            try:
                total += os.lstat(os.path.join(dirpath, n)).st_size
            except OSError:
                pass
    st = os.stat(APP)
    os.makedirs(RAM, exist_ok=True)
    size_mb = total // 2**20 * 2 + 1024  # room for what a run writes (patches, test output)
    subprocess.run(["mount", "-t", "tmpfs", "-o", f"size={size_mb}m,mode=0755", "tmpfs", RAM], check=True)
    subprocess.run(["cp", "-a", APP + "/.", RAM + "/"], check=True, timeout=max(1.0, deadline - time.time()))
    os.chown(RAM, st.st_uid, st.st_gid)
    os.chmod(RAM, st.st_mode & 0o7777)
    subprocess.run(["mount", "--bind", RAM, APP], check=True)
    return {"ram_bytes": total, "tmpfs_mb": size_mb}


def first_work(deadline: float) -> list:
    out = []
    for cmd in FIRST_WORK:
        t0 = time.time()
        try:
            rc = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                timeout=max(1.0, min(20.0, deadline - time.time()))).returncode
        except (OSError, subprocess.TimeoutExpired) as e:
            rc = type(e).__name__
        out.append([cmd[0], rc, round(time.time() - t0, 1)])
    return out


class Hook(BaseHTTPRequestHandler):
    def do_POST(self):
        kind = self.headers.get("X-Amzn-Bedrock-AgentCore-Runtime-Warmup-Type", "").lower()
        t0 = time.time()
        deadline = t0 + BUDGET_SECS
        out = {"kind": kind, "mode": MODE}
        try:
            if kind == "memory" and MODE == "ram":
                out.update(app_to_ram(t0 + 60))
            elif kind == "disk":
                out["first_work"] = first_work(deadline)
                roots = HARNESS if MODE == "ram" else (APP, *HARNESS)
                out.update(read_tree(roots, deadline))
        except Exception as e:  # never fail the runtime for an optimization: report it and answer 2xx
            out["error"] = f"{type(e).__name__}: {e}"[:300]
        out["secs"] = round(time.time() - t0, 1)
        print("taskmine warmup " + json.dumps(out), flush=True)  # in the runtime's build logs
        body = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


if os.fork() == 0:
    ThreadingHTTPServer(("0.0.0.0", PORT), Hook).serve_forever()
else:
    os.execv(sys.executable, [sys.executable, "/opt/shim/main.py"])
