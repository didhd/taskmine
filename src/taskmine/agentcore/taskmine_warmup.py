"""The container command of a V2 image with taskmine's build hook: a warmup endpoint, then PR #120's shim.

AgentCore Runtime V2 builds a snapshot of the microVM once, then restores it for every session.
Image files are fetched block by block the first time a restored session reads them, and a test
run that reads thousands of small files (node_modules) pays for every one. AgentCore calls a build
hook twice while it builds the snapshot:
- `memory`: before the snapshot. Nothing to load here.
- `disk`: on a test restore. Reading the files that sessions will read shapes what restored
  sessions fetch first. This is where the files are read.
The two calls share 120 seconds, so the reads stop after 90, checked between 1 MB reads: a deadline
checked only between files ran one call to 121 s and failed the runtime (measured).

The endpoint listens on :9090 in the main network namespace. The agent runs in PR #120's empty
namespace and cannot reach it. It only reads files and answers with counts.
"""

import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 9090
ROOTS = ("/app", "/installed-agent", "/usr/local/lib/node_modules", "/opt/uv-python")
BUDGET_SECS = 90


def read_all(deadline: float) -> dict:
    files = size = 0
    for root in ROOTS:
        for dirpath, _dirs, names in os.walk(root):
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


class Hook(BaseHTTPRequestHandler):
    def do_POST(self):
        kind = self.headers.get("X-Amzn-Bedrock-AgentCore-Runtime-Warmup-Type", "").lower()
        t0 = time.time()
        out = read_all(t0 + BUDGET_SECS) if kind == "disk" else {}
        out.update(kind=kind, secs=round(time.time() - t0, 1))
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
