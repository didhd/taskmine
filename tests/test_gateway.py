"""The model gateway: request signing and streaming against a real local HTTPS upstream, never AWS.

The gateway runs in a child process with fake static credentials, a temporary HOME and no AWS
configuration, so nothing here can reach a real account. The upstream is an HTTPS server on
127.0.0.1 with a certificate made for the test by `openssl`; the gateway trusts it through
SSL_CERT_FILE. Skipped when botocore or openssl is missing.
"""

from __future__ import annotations

import http.client
import importlib.util
import json
import os
import shutil
import socket
import ssl
import subprocess
import sys
import threading
import time
import tomllib
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from support import SharedTempDir

from taskmine import gateway

SRC = Path(__file__).resolve().parent.parent / "src"
FAKE_KEY = "AKIDTASKMINETEST0000"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Pure(unittest.TestCase):
    def test_only_loopback_and_private_addresses_are_served(self):
        for addr in ("127.0.0.1", "10.0.0.5", "172.17.0.2", "192.168.1.9", "::1", "fd00::1"):
            self.assertTrue(gateway._private(addr), addr)
        for addr in ("8.8.8.8", "1.1.1.1", "2001:4860:4860::8888"):
            self.assertFalse(gateway._private(addr), addr)

    def test_the_litellm_config_serves_each_model_through_converse(self):
        text = gateway.litellm_config(["us.openai.gpt-6.1-terra", "m2"], "us-west-2", "https://x.example", "sk-1")
        self.assertIn('  - model_name: "us.openai.gpt-6.1-terra"\n    litellm_params:\n'
                      '      model: "bedrock/converse/us.openai.gpt-6.1-terra"\n', text)
        self.assertEqual(text.count("aws_region_name: \"us-west-2\""), 2)
        self.assertIn('aws_bedrock_runtime_endpoint: "https://x.example"', text)
        self.assertTrue(text.endswith('general_settings:\n  master_key: "sk-1"\n'))

    def test_credentials_and_hop_headers_are_never_forwarded(self):
        for h in ("authorization", "x-amz-security-token", "x-amz-date", "host", "content-length"):
            self.assertIn(h, gateway.HOP)


@unittest.skipUnless(importlib.util.find_spec("botocore") and shutil.which("openssl"), "needs botocore and openssl")
class Signer(SharedTempDir):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.cert, cls.key = cls.tmp / "cert.pem", cls.tmp / "key.pem"
        made = subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                               "-subj", "/CN=127.0.0.1", "-keyout", str(cls.key), "-out", str(cls.cert),
                               "-addext", "subjectAltName=IP:127.0.0.1",
                               "-addext", "basicConstraints=critical,CA:TRUE",
                               "-addext", "keyUsage=critical,digitalSignature,keyCertSign"], capture_output=True)
        if made.returncode != 0:
            raise unittest.SkipTest("this openssl cannot make a test certificate: " + made.stderr.decode()[-200:])
        cls.seen: list[dict] = []
        cls.upstream = cls.start_upstream()
        cls.addClassCleanup(cls.upstream.server_close)
        cls.addClassCleanup(cls.upstream.shutdown)
        cls.port = free_port()
        cls.root = cls.tmp / ".taskmine"
        home = cls.tmp / "home"
        home.mkdir()
        env = {"PATH": os.environ.get("PATH", ""), "HOME": str(home), "PYTHONPATH": str(SRC),
               "AWS_ACCESS_KEY_ID": FAKE_KEY, "AWS_SECRET_ACCESS_KEY": "fake-secret", "AWS_SESSION_TOKEN": "fake-token",
               "AWS_EC2_METADATA_DISABLED": "true", "SSL_CERT_FILE": str(cls.cert)}
        driver = ("import sys, time\nfrom pathlib import Path\nfrom taskmine import gateway\n"
                  "gateway.start(Path(sys.argv[1]), '127.0.0.1', '127.0.0.1', int(sys.argv[2]), 'us-west-2', sys.argv[3], [])\n"
                  "time.sleep(120)\n")
        cls.proc = subprocess.Popen([sys.executable, "-c", driver, str(cls.root), str(cls.port),
                                     f"https://127.0.0.1:{cls.upstream.server_port}"],
                                    env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        cls.addClassCleanup(cls.stop)
        for _ in range(200):
            if (cls.root / "gateway.toml").exists() or cls.proc.poll() is not None:
                break
            time.sleep(0.05)
        if not (cls.root / "gateway.toml").exists():
            raise AssertionError("the gateway did not start: " + cls.proc.stdout.read().decode(errors="replace"))
        time.sleep(0.1)  # gateway.toml is written right after the server thread starts

    @classmethod
    def stop(cls):
        cls.proc.kill()
        cls.proc.wait()
        cls.proc.stdout.close()

    def setUp(self):
        self.seen.clear()

    @classmethod
    def start_upstream(cls) -> ThreadingHTTPServer:
        seen = cls.seen

        class Upstream(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("content-length") or 0))
                seen.append({"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}, "body": body})
                status = 429 if b"too many" in body else 200
                payload = b'{"part": 1}\n{"part": 2}\n'
                self.send_response(status)
                self.send_header("content-type", "application/vnd.amazon.eventstream")
                self.send_header("x-amzn-requestid", "req-1")
                self.send_header("content-length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        srv = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cls.cert, cls.key)
        srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        return srv

    def post(self, path: str, body: bytes, headers: dict) -> tuple[int, dict, bytes]:
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        c.request("POST", path, body=body, headers=headers)
        r = c.getresponse()
        out = r.status, {k.lower(): v for k, v in r.getheaders()}, r.read()
        c.close()
        return out

    def test_gateway_toml_says_where_it_listens_and_is_private(self):
        info = tomllib.loads((self.root / "gateway.toml").read_text())["gateway"]
        self.assertEqual(info["bedrock_url"], f"http://127.0.0.1:{self.port}")
        self.assertEqual(info["region"], "us-west-2")
        self.assertNotIn("openai_url", info)
        self.assertEqual((self.root / "gateway.toml").stat().st_mode & 0o777, 0o600)

    def test_a_request_is_signed_on_the_host_and_streamed_back(self):
        body = json.dumps({"messages": [{"role": "user", "content": "hi"}]}).encode()
        path = "/model/us.anthropic.claude-sonnet-5-5/invoke-with-response-stream?x=1"
        status, headers, data = self.post(path, body, {
            "content-type": "application/json", "authorization": "Bearer from-the-agent-container",
            "x-amz-security-token": "planted", "anthropic-version": "bedrock-2023-05-31"})
        self.assertEqual(status, 200)
        self.assertEqual(data, b'{"part": 1}\n{"part": 2}\n')
        self.assertEqual(headers["transfer-encoding"], "chunked")
        self.assertEqual(headers["x-amzn-requestid"], "req-1")
        (req,) = self.seen
        self.assertEqual(req["path"], path)
        self.assertEqual(req["body"], body)
        auth = req["headers"]["authorization"]
        self.assertTrue(auth.startswith(f"AWS4-HMAC-SHA256 Credential={FAKE_KEY}/"), auth)
        self.assertIn("/us-west-2/bedrock/aws4_request", auth)
        self.assertEqual(req["headers"]["x-amz-security-token"], "fake-token")
        self.assertEqual(req["headers"]["anthropic-version"], "bedrock-2023-05-31")
        self.assertNotIn("from-the-agent-container", json.dumps(req["headers"]))

    def test_upstream_errors_pass_through_with_their_status(self):
        status, _, data = self.post("/model/m/invoke", b'{"too many": true}', {"content-type": "application/json"})
        self.assertEqual(status, 429)
        self.assertEqual(data, b'{"part": 1}\n{"part": 2}\n')
