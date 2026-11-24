"""`gateway`: a model gateway on the Docker host, so no cloud credential enters an agent container.

Agents run inside task containers next to code they did not write. Handing them AWS keys would let
anything in that container spend on, or read from, the account. The gateway keeps the keys on the
host:

- Bedrock pass-through for Claude Code. Claude Code runs in its own Bedrock mode, with
  `ANTHROPIC_BEDROCK_BASE_URL` set to this gateway and `CLAUDE_CODE_SKIP_BEDROCK_AUTH=1`. The
  gateway signs each request with the host's credentials (SigV4; an instance role refreshes by
  itself) and forwards it unchanged to Bedrock, streaming the answer back.
- An OpenAI Responses endpoint for Codex, through LiteLLM (an optional dependency), which turns
  Codex's Responses calls into Bedrock Converse calls for the configured models.

Requests are accepted only from loopback and private addresses (Docker's networks).
`.taskmine/gateway.toml` records where each endpoint listens; `bench` reads it.

Needs `botocore` (pip install botocore) for signing, and `litellm[proxy]` for the Codex endpoint.
"""

from __future__ import annotations

import http.client
import ipaddress
import json
import secrets
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

HOP = {"connection", "keep-alive", "transfer-encoding", "te", "trailer", "upgrade", "proxy-connection", "host",
       "content-length", "authorization", "x-amz-date", "x-amz-security-token", "x-amz-content-sha256"}


def _private(addr: str) -> bool:
    ip = ipaddress.ip_address(addr)
    return ip.is_loopback or ip.is_private


def bedrock_signer(listen: str, port: int, region: str, endpoint: str, log=print) -> ThreadingHTTPServer:
    from botocore.auth import SigV4Auth
    from botocore.awsrequest import AWSRequest
    from botocore.session import get_session

    session = get_session()
    creds = session.get_credentials()
    if creds is None:
        raise SystemExit("gateway: no AWS credentials on this host")
    up = urlsplit(endpoint)
    counter = {"n": 0}

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):  # quiet; one line per request below
            pass

        def _forward(self):
            if not _private(self.client_address[0]):
                self.send_error(403)
                return
            n = int(self.headers.get("content-length") or 0)
            body = self.rfile.read(n) if n else b""
            headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP}
            req = AWSRequest(method=self.command, url=f"{endpoint}{self.path}", data=body, headers=headers)
            SigV4Auth(creds.get_frozen_credentials(), "bedrock", region).add_auth(req)
            conn = http.client.HTTPSConnection(up.hostname, up.port or 443, timeout=600)
            conn.request(self.command, self.path, body=body, headers=dict(req.headers.items()))
            resp = conn.getresponse()
            counter["n"] += 1
            log(f"gateway: {self.command} {self.path.split('?')[0]} -> {resp.status} (#{counter['n']})")
            self.send_response(resp.status)
            for k, v in resp.getheaders():
                if k.lower() not in HOP:
                    self.send_header(k, v)
            self.send_header("transfer-encoding", "chunked")
            self.end_headers()
            while True:
                chunk = resp.read1(65536) if hasattr(resp, "read1") else resp.read(65536)
                if not chunk:
                    break
                self.wfile.write(b"%x\r\n%s\r\n" % (len(chunk), chunk))
                self.wfile.flush()
            self.wfile.write(b"0\r\n\r\n")
            conn.close()

        do_POST = _forward
        do_GET = _forward

    srv = ThreadingHTTPServer((listen, port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def litellm_config(models: list[str], region: str, endpoint: str, master_key: str) -> str:
    """LiteLLM proxy config: each model served under its own id, through Bedrock Converse."""
    lines = ["model_list:"]
    for m in models:
        lines += [
            f"  - model_name: {json.dumps(m)}",
            "    litellm_params:",
            f"      model: {json.dumps('bedrock/converse/' + m)}",
            f"      aws_region_name: {json.dumps(region)}",
            f"      aws_bedrock_runtime_endpoint: {json.dumps(endpoint)}",
        ]
    lines += ["litellm_settings:", "  drop_params: true", "general_settings:", f"  master_key: {json.dumps(master_key)}"]
    return "\n".join(lines) + "\n"


def start(root: Path, listen: str, advertise: str, port: int, region: str, endpoint: str,
          codex_models: list[str], log=print) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    info = {"bedrock_url": f"http://{advertise}:{port}", "region": region, "endpoint": endpoint}
    srv = bedrock_signer(listen, port, region, endpoint, log)
    proc = None
    if codex_models:
        key = "sk-taskmine-" + secrets.token_hex(12)
        cfg = root / "litellm.yaml"
        cfg.write_text(litellm_config(codex_models, region, endpoint, key))
        cfg.chmod(0o600)
        lport = port + 1
        proc = subprocess.Popen([sys.executable, "-m", "litellm", "--config", str(cfg), "--host", listen,
                                 "--port", str(lport)], stdout=open(root / "litellm.log", "w"), stderr=subprocess.STDOUT)
        info.update(openai_url=f"http://{advertise}:{lport}/v1", openai_key=key)
    lines = ["[gateway]"] + [f"{k} = {json.dumps(v)}" for k, v in info.items()]
    (root / "gateway.toml").write_text("\n".join(lines) + "\n")
    (root / "gateway.toml").chmod(0o600)
    return {"server": srv, "litellm": proc, **info}
