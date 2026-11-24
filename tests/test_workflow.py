"""The everyday commands' pure parts: agent entries, what Harbor is given, the results table, cast joins."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from taskmine import agents, bench


class Agents(unittest.TestCase):
    def test_names_drop_profile_and_vendor_prefixes(self):
        self.assertEqual(agents.entry("claude-code", "bedrock", "us.anthropic.claude-sonnet-5-5", None, {})["name"],
                         "claude-code-sonnet-5-5")
        self.assertEqual(agents.entry("codex", "bedrock", "us.openai.gpt-6.1-terra", None, {})["name"],
                         "codex-gpt-6.1-terra")

    def test_unknown_agent_or_provider_is_refused(self):
        with self.assertRaises(agents.AgentError):
            agents.entry("nope", "bedrock", "m", None, {})
        with self.assertRaises(agents.AgentError):
            agents.entry("cursor", "bedrock", "m", None, {})

    def test_bedrock_agents_get_the_gateway_and_never_a_credential(self):
        with tempfile.TemporaryDirectory() as root:
            cc = agents.entry("claude-code", "bedrock", "us.anthropic.claude-sonnet-5-5", None,
                              {"ANTHROPIC_SMALL_FAST_MODEL": "us.anthropic.claude-haiku-5-5"})
            with self.assertRaises(agents.AgentError):  # no gateway yet
                agents.harbor_args(cc, "us-west-2", root)
            (Path(root) / "gateway.toml").write_text(
                '[gateway]\nbedrock_url = "http://10.0.0.5:8790"\nregion = "us-west-2"\n'
                'openai_url = "http://10.0.0.5:8791/v1"\nopenai_key = "sk-local"\n')
            args, env = agents.harbor_args(cc, "us-west-2", root)
            self.assertEqual(args[:4], ["-a", "claude-code", "-m", "bedrock/us.anthropic.claude-sonnet-5-5"])
            ae = [args[i + 1] for i, x in enumerate(args) if x == "--ae"]
            self.assertIn("ANTHROPIC_BEDROCK_BASE_URL=http://10.0.0.5:8790", ae)
            self.assertIn("CLAUDE_CODE_SKIP_BEDROCK_AUTH=1", ae)
            self.assertFalse([x for x in ae + list(env) if x.startswith("AWS_ACCESS_KEY") or x.startswith("AWS_SECRET")])
            cx = agents.entry("codex", "bedrock", "us.openai.gpt-6.1-terra", None, {})
            args, env = agents.harbor_args(cx, "us-west-2", root)
            self.assertEqual(args, ["-a", "codex", "-m", "us.openai.gpt-6.1-terra"])
            self.assertEqual(env, {"OPENAI_BASE_URL": "http://10.0.0.5:8791/v1", "OPENAI_API_KEY": "sk-local"})


class Results(unittest.TestCase):
    def test_errors_are_not_counted_as_failures(self):
        with tempfile.TemporaryDirectory() as d:
            run = Path(d)
            for agent, task, reward, exc in [("a", "t1", 1.0, None), ("a", "t2", 0.0, None), ("b", "t1", None, "Timeout")]:
                p = run / agent / "ts" / f"{task}__x"
                p.mkdir(parents=True)
                (p / "result.json").write_text(json.dumps({
                    "task_name": task, "verifier_result": {"rewards": {"reward": reward}} if reward is not None else None,
                    "exception_info": {"exception_type": exc} if exc else None, "agent_result": {"cost_usd": 0.5}}))
            s = bench.summarize(run, [])
            by = {a["name"]: a for a in s["agents"]}
            self.assertEqual((by["a"]["solved"], by["a"]["trials"], by["a"]["errors"]), (1, 2, 0))
            self.assertEqual((by["b"]["solved"], by["b"]["errors"]), (0, 1))
            text = bench.table(s)
            self.assertIn("50%", text)
            self.assertIn("error", text)


class Combine(unittest.TestCase):
    def test_dropping_a_command_keeps_the_rest_unedited(self):
        prompt = "\x1b[1;36m~/demo\x1b[0m \x1b[1;35m$\x1b[0m "
        events = [[0.3, "o", prompt]]
        t = 0.3
        for cmd, out in [("taskmine gateway", "gw up\r\n"), ("taskmine verify", "12 tasks\r\n")]:
            for ch in cmd:
                t += 0.03
                events.append([round(t, 3), "o", ch])
            events += [[t + 0.3, "o", "\r\n"], [t + 1.0, "o", out], [t + 1.5, "o", prompt]]
            t += 1.5
        with tempfile.TemporaryDirectory() as d:
            src, out = Path(d) / "a.cast", Path(d) / "out.cast"
            src.write_text(json.dumps({"version": 2, "width": 80, "height": 24}) + "\n"
                           + "\n".join(json.dumps(e) for e in events) + "\n")
            here = Path(__file__).resolve().parent.parent / "demo" / "combine.py"
            subprocess.run([sys.executable, str(here), str(out), f"{src}:-gateway"], check=True, capture_output=True)
            text = "".join(json.loads(l)[2] for l in out.read_text().splitlines()[1:])
            self.assertNotIn("gw up", text)
            self.assertIn("12 tasks", text)
            self.assertIn("taskmine verify", text)


if __name__ == "__main__":
    unittest.main()
