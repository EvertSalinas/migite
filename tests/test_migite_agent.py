#!/usr/bin/env python3
"""tests/test_migite_agent.py — agent backends: argv building, permission mapping,
output parsing, capability degradation, and the end-to-end call through
migite_claude with the fake CLIs on PATH. Run: python3 -m unittest tests/test_migite_agent.py"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import migite_agent    # noqa: E402
import migite_claude   # noqa: E402
import migite_config   # noqa: E402

FAKES = ROOT / "tests"


class ArgvTest(unittest.TestCase):
    def test_claude_headless(self):
        b = migite_agent.ClaudeBackend()
        argv, stdin = b.headless_argv(model="claude-opus-5-5", effort="xhigh", schema={"type": "object"},
                                      permission_mode="acceptEdits", allowed_tools=["a", "b"], prompt="P")
        self.assertEqual(argv[:4], ["claude", "--print", "--output-format", "json"])
        self.assertIn("--effort", argv); self.assertIn("--json-schema", argv)
        self.assertEqual(argv[argv.index("--permission-mode") + 1], "acceptEdits")
        self.assertEqual(argv[argv.index("--allowedTools") + 1], "a b")
        self.assertEqual(stdin, "P")                       # prompt on stdin
        argv, _ = b.headless_argv(model="claude-haiku-4-5-20251001", effort="xhigh", schema=None,
                                  permission_mode=None, allowed_tools=None, prompt="P")
        self.assertNotIn("--effort", argv)                 # never for haiku
        self.assertNotIn("--permission-mode", argv)

    def test_cursor_headless_maps_permissions_and_takes_prompt_as_arg(self):
        b = migite_agent.CursorBackend()
        argv, stdin = b.headless_argv(model=None, effort="xhigh", schema={"x": 1},
                                      permission_mode="bypassPermissions", allowed_tools=["t"], prompt="P")
        self.assertEqual(argv[:4], ["cursor-agent", "-p", "--output-format", "json"])
        self.assertIn("--force", argv); self.assertIn("--trust", argv)
        self.assertNotIn("--model", argv)                  # no default model for cursor
        self.assertNotIn("--effort", argv); self.assertNotIn("--json-schema", argv); self.assertNotIn("--allowedTools", argv)
        self.assertEqual(argv[-1], "P"); self.assertIsNone(stdin)
        argv, _ = b.headless_argv(model="sonnet-4", effort=None, schema=None, permission_mode="plan", allowed_tools=None, prompt="P")
        self.assertIn("--mode", argv); self.assertEqual(argv[argv.index("--mode") + 1], "plan")
        self.assertEqual(argv[argv.index("--model") + 1], "sonnet-4")
        argv, _ = b.headless_argv(model=None, effort=None, schema=None, permission_mode="none", allowed_tools=None, prompt="P")
        self.assertNotIn("--force", argv)

    def test_opencode_headless(self):
        b = migite_agent.OpenCodeBackend()
        argv, stdin = b.headless_argv(model="anthropic/claude-sonnet-4-5", effort=None, schema=None,
                                      permission_mode="acceptEdits", allowed_tools=None, prompt="P")
        self.assertEqual(argv[:4], ["opencode", "run", "--format", "json"])
        self.assertIn("--auto", argv)
        self.assertEqual(argv[argv.index("--model") + 1], "anthropic/claude-sonnet-4-5")
        self.assertEqual(argv[-1], "P"); self.assertIsNone(stdin)
        argv, _ = b.headless_argv(model=None, effort=None, schema=None, permission_mode="default", allowed_tools=None, prompt="P")
        self.assertNotIn("--auto", argv); self.assertNotIn("--model", argv)

    def test_interactive_argv(self):
        self.assertEqual(migite_agent.ClaudeBackend().interactive_argv(prompt="P", permission_mode="bypassPermissions", model=None),
                         ["claude", "--permission-mode", "bypassPermissions", "--", "P"])
        self.assertEqual(migite_agent.ClaudeBackend().interactive_argv(prompt="P", permission_mode="none", model=None),
                         ["claude", "--", "P"])
        self.assertEqual(migite_agent.CursorBackend().interactive_argv(prompt="P", permission_mode="bypassPermissions", model=None),
                         ["cursor-agent", "--force", "P"])
        self.assertEqual(migite_agent.CursorBackend().interactive_argv(prompt="P", permission_mode="plan", model="gpt-5"),
                         ["cursor-agent", "--mode", "plan", "--model", "gpt-5", "P"])
        self.assertEqual(migite_agent.OpenCodeBackend().interactive_argv(prompt="P", permission_mode="acceptEdits", model=None),
                         ["opencode", "--prompt", "P", "--auto"])

    def test_binary_override_and_capabilities(self):
        b = migite_agent.CursorBackend(binary="/opt/bin/agent")
        argv, _ = b.headless_argv(model=None, effort=None, schema=None, permission_mode=None, allowed_tools=None, prompt="P")
        self.assertEqual(argv[0], "/opt/bin/agent")
        caps = migite_agent.OpenCodeBackend().capabilities()
        self.assertFalse(caps["structured_output"]); self.assertTrue(caps["usage"]); self.assertEqual(caps["prompt_via"], "arg")
        self.assertTrue(migite_agent.ClaudeBackend().capabilities()["tool_allowlist"])


class ParseTest(unittest.TestCase):
    def test_cursor_result_object(self):
        out = '{"type":"result","subtype":"success","is_error":false,"duration_ms":842,"result":"hello","session_id":"s"}\n'
        p = migite_agent.CursorBackend().parse(out, 0, requested_model="")
        self.assertTrue(p.ok); self.assertEqual(p.text, "hello"); self.assertEqual(p.duration_ms, 842)
        self.assertEqual(p.cost_usd, 0.0)                   # cursor reports no usage
        p = migite_agent.CursorBackend().parse('{"type":"result","is_error":true,"result":"Not logged in"}', 0, requested_model="")
        self.assertFalse(p.ok); self.assertIn("Not logged in", p.error)
        p = migite_agent.CursorBackend().parse("noise\n" + out, 0, requested_model="")
        self.assertEqual(p.text, "hello")                   # last JSON object wins
        p = migite_agent.CursorBackend().parse("just text", 0, requested_model="m")
        self.assertFalse(p.is_envelope); self.assertEqual(p.text, "just text"); self.assertTrue(p.ok)

    def test_opencode_jsonl(self):
        lines = [
            {"type": "step_start", "part": {"type": "step-start"}},
            {"type": "text", "part": {"type": "text", "text": "first"}},
            {"type": "step_finish", "part": {"reason": "tool-calls", "cost": 0.01, "tokens": {"input": 100, "output": 10, "reasoning": 5, "cache": {"read": 50, "write": 7}}}},
            {"type": "text", "part": {"type": "text", "text": "second"}},
            {"type": "step_finish", "part": {"reason": "stop", "cost": 0.02, "tokens": {"input": 200, "output": 20, "reasoning": 0, "cache": {"read": 0, "write": 0}}}},
        ]
        p = migite_agent.OpenCodeBackend().parse("\n".join(json.dumps(l) for l in lines), 0, requested_model="anthropic/x")
        self.assertTrue(p.ok); self.assertEqual(p.text, "first\nsecond")
        self.assertAlmostEqual(p.cost_usd, 0.03); self.assertEqual(p.input_tokens, 300)
        self.assertEqual(p.output_tokens, 35); self.assertEqual(p.cache_read_input_tokens, 50); self.assertEqual(p.cache_creation_input_tokens, 7)
        err = json.dumps({"type": "error", "error": {"name": "ProviderAuthError", "data": {"message": "No API key"}}})
        p = migite_agent.OpenCodeBackend().parse(err, 0, requested_model="")
        self.assertFalse(p.ok); self.assertIn("No API key", p.error)
        p = migite_agent.OpenCodeBackend().parse("plain", 0, requested_model="")
        self.assertFalse(p.is_envelope); self.assertEqual(p.text, "plain")

    def test_claude_envelope_unchanged(self):
        env = json.dumps({"type": "result", "result": "ok", "is_error": False, "duration_ms": 5, "total_cost_usd": 0.5,
                          "usage": {"input_tokens": 3, "output_tokens": 4, "cache_read_input_tokens": 1, "cache_creation_input_tokens": 2},
                          "modelUsage": {"claude-opus-5-5": {}}, "structured_output": {"a": 1}})
        p = migite_agent.ClaudeBackend().parse(env, 0, requested_model="")
        self.assertEqual((p.text, p.cost_usd, p.model, p.structured), ("ok", 0.5, "claude-opus-5-5", {"a": 1}))


class EndToEndTest(unittest.TestCase):
    """call_claude through each backend with the fake CLIs first on PATH."""

    def setUp(self):
        self._env = dict(os.environ)
        self.tmp = tempfile.TemporaryDirectory()
        bin_dir = Path(self.tmp.name) / "bin"; bin_dir.mkdir()
        for name, fake in (("claude", "fake-claude"), ("cursor-agent", "fake-cursor-agent"), ("opencode", "fake-opencode")):
            (bin_dir / name).write_text(f'#!/usr/bin/env bash\nexec "{FAKES}/{fake}" "$@"\n'); (bin_dir / name).chmod(0o755)
        for f in ("fake-claude", "fake-cursor-agent", "fake-opencode"):
            os.chmod(FAKES / f, 0o755)
        os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ["PATH"]
        os.environ.pop("MIGITE_USAGE_LEDGER", None); os.environ.pop("MIGITE_AGENT", None)
        # An empty HOME/XDG so the developer's real ~/.config/migite never leaks in.
        home = Path(self.tmp.name) / "home"; (home / ".config").mkdir(parents=True)
        os.environ["HOME"] = str(home); os.environ["XDG_CONFIG_HOME"] = str(home / ".config")
        os.environ.pop("MIGITE_CONFIG", None)
        self.ledger = str(Path(self.tmp.name) / "usage.jsonl")
        self.argv_log = str(Path(self.tmp.name) / "argv.log")
        os.environ["FAKE_AGENT_ARGV"] = self.argv_log
        migite_config._CACHE.clear()

    def tearDown(self):
        os.environ.clear(); os.environ.update(self._env)
        migite_claude.BACKEND = migite_agent.ClaudeBackend(); migite_claude.ROLE_EFFORT = {}
        migite_config._CACHE.clear(); self.tmp.cleanup()

    def cfg_for(self, backend):
        env = {k: v for k, v in os.environ.items() if k not in migite_config.ENV_OVERRIDES}
        env["MIGITE_AGENT"] = backend
        return migite_config.load(None, env=env, use_cache=False)

    def test_cursor_end_to_end(self):
        migite_claude.configure_from(self.cfg_for("cursor"))
        os.environ["FAKE_AGENT_MODE"] = "ok"
        r = migite_claude.call_claude("hello cursor", "", label="t", tool="x", ledger=self.ledger, schema={"type": "object"}, effort="xhigh")
        self.assertTrue(r.text.startswith("cursor:hello cursor"))
        self.assertIsNone(r.structured)                      # degraded: no structured output
        argv = Path(self.argv_log).read_text()
        self.assertIn("--trust", argv); self.assertNotIn("--effort", argv); self.assertNotIn("--json-schema", argv); self.assertNotIn("--model", argv)
        rec = migite_claude.read_ledger(self.ledger)[0]
        self.assertTrue(rec["ok"]); self.assertEqual(rec["cost_usd"], 0.0); self.assertEqual(rec["duration_ms"], 842)
        os.environ["FAKE_AGENT_MODE"] = "error"
        with self.assertRaises(migite_claude.ClaudeError) as cm:
            migite_claude.call_claude("x", "", ledger=self.ledger)
        self.assertIn("Not logged in", str(cm.exception))

    def test_opencode_end_to_end_with_usage(self):
        migite_claude.configure_from(self.cfg_for("opencode"))
        os.environ["FAKE_AGENT_MODE"] = "ok"
        r = migite_claude.call_claude("hello opencode", "anthropic/claude-sonnet-4-5", label="t", tool="x", ledger=self.ledger,
                                      permission_mode="bypassPermissions")
        self.assertTrue(r.text.startswith("opencode:hello opencode")); self.assertIn("second part", r.text)
        self.assertAlmostEqual(r.usage.cost_usd, 0.02); self.assertEqual(r.usage.input_tokens, 1500)
        self.assertEqual(r.usage.cache_read_input_tokens, 6000)
        argv = Path(self.argv_log).read_text()
        self.assertIn("run --format json --auto --model anthropic/claude-sonnet-4-5", argv)
        os.environ["FAKE_AGENT_MODE"] = "error"
        with self.assertRaises(migite_claude.ClaudeError):
            migite_claude.call_claude("x", "", ledger=self.ledger)

    def test_large_prompt_becomes_a_pointer_file_for_arg_backends(self):
        migite_claude.configure_from(self.cfg_for("cursor"))
        os.environ["FAKE_AGENT_MODE"] = "ok"
        big = "x" * (migite_claude.ARG_PROMPT_MAX + 10)
        r = migite_claude.call_claude(big, "", ledger=self.ledger)
        argv = Path(self.argv_log).read_text()
        self.assertIn("Your task brief is in the file", argv)
        self.assertLess(len(argv), 2000)
        self.assertTrue(r.text.startswith("cursor:Your task brief"))

    def test_supports_reflects_backend(self):
        migite_claude.configure_from(self.cfg_for("claude"))
        self.assertTrue(migite_claude.supports("structured_output")); self.assertTrue(migite_claude.supports("tool_allowlist"))
        migite_claude.configure_from(self.cfg_for("opencode"))
        self.assertFalse(migite_claude.supports("structured_output")); self.assertTrue(migite_claude.supports("usage"))
        migite_claude.configure_from(self.cfg_for("cursor"))
        self.assertFalse(migite_claude.supports("usage"))

    def test_model_defaults_per_backend(self):
        self.assertEqual(self.cfg_for("claude").model("critic"), "claude-opus-5-5")
        self.assertEqual(self.cfg_for("cursor").model("critic"), "")          # no --model until pinned
        self.assertEqual(self.cfg_for("opencode").model("explore"), "")
        pinned = self.cfg_for("cursor")
        pinned._data["models"]["strong"] = "gpt-5"
        self.assertEqual(pinned.model("critic"), "gpt-5")
        self.assertEqual(migite_config.default_model("critic"), "claude-opus-5-5")
        self.assertEqual(migite_config.default_model("critic", "cursor"), "")

    def test_unknown_backend_rejected(self):
        with self.assertRaises(migite_config.ConfigError):
            self.cfg_for("copilot")


if __name__ == "__main__":
    unittest.main()
