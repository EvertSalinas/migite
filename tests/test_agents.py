#!/usr/bin/env python3
"""tests/test_agents.py - the agents package: each adapter's translation of a
request into a command line, its output parser, and the end-to-end call through
the gateway with the fake CLIs on PATH. Run: python3 -m unittest tests/test_agents.py"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import agents          # noqa: E402
import migite_call     # noqa: E402
import migite_config   # noqa: E402

FAKES = ROOT / "tests"


def ask(**kw):
    kw.setdefault("prompt", "P")
    return agents.AskRequest(**kw)


class VocabularyTest(unittest.TestCase):
    def test_permission_words_and_claude_aliases_normalize(self):
        self.assertEqual(agents.normalize_permission("auto"), "auto")
        self.assertEqual(agents.normalize_permission("bypassPermissions"), "auto")
        self.assertEqual(agents.normalize_permission("acceptEdits"), "edits")
        self.assertEqual(agents.normalize_permission("default"), "ask")
        self.assertEqual(agents.normalize_permission(None), "none")
        self.assertEqual(agents.normalize_permission(""), "none")
        with self.assertRaises(ValueError):
            agents.normalize_permission("sometimes")

    def test_launch_shell_quotes_and_prefixes_env(self):
        launch = agents.Launch(argv=["cli", "a b"], env_unset=("X",), env_set={"K": "v"})
        self.assertEqual(launch.shell(), "env -u X K=v cli 'a b'")
        self.assertEqual(agents.Launch(argv=["cli", "p"]).shell(), "cli p")
        env = launch.environ({"X": "1", "Y": "2"})
        self.assertNotIn("X", env); self.assertEqual(env["Y"], "2"); self.assertEqual(env["K"], "v")

    def test_registry(self):
        self.assertEqual(agents.names(), ("claude", "cursor", "opencode"))
        self.assertEqual(agents.get().name, "claude")
        self.assertEqual(agents.get("cursor", binary="/opt/agent").binary, "/opt/agent")
        with self.assertRaises(ValueError):
            agents.get("copilot")

    def test_supports_reads_the_info(self):
        claude, cursor, opencode = agents.get("claude"), agents.get("cursor"), agents.get("opencode")
        self.assertTrue(claude.supports("structured_output")); self.assertTrue(claude.supports("scope:jira.read"))
        self.assertFalse(cursor.supports("usage")); self.assertFalse(cursor.supports("scope:jira.read"))
        self.assertTrue(opencode.supports("usage")); self.assertFalse(opencode.supports("effort"))
        self.assertFalse(claude.supports("scope:not-a-scope"))


class ClaudeTest(unittest.TestCase):
    def setUp(self):
        self.agent = agents.get("claude")

    def test_headless_flags(self):
        launch = self.agent.ask_launch(ask(model="claude-opus-5-5", effort="xhigh", schema={"type": "object"},
                                           permission="edits", scopes=("jira.read",)))
        argv = launch.argv
        self.assertEqual(argv[:4], ["claude", "--print", "--output-format", "json"])
        self.assertEqual(argv[argv.index("--effort") + 1], "xhigh")
        self.assertIn("--json-schema", argv)
        self.assertEqual(argv[argv.index("--permission-mode") + 1], "acceptEdits")
        self.assertIn("mcp__claude_ai_Atlassian__getJiraIssue", argv[argv.index("--allowedTools") + 1])
        self.assertEqual(launch.stdin, "P")                       # prompt on stdin
        self.assertEqual(launch.env_unset, ("CLAUDECODE",))

    def test_no_effort_for_haiku_and_no_flag_for_none(self):
        argv = self.agent.ask_launch(ask(model="claude-haiku-4-5-20251001", effort="xhigh")).argv
        self.assertNotIn("--effort", argv)                        # Haiku rejects the flag
        self.assertNotIn("--permission-mode", argv)               # permission none = no flag

    def test_permission_words(self):
        for word, flag in (("auto", "bypassPermissions"), ("edits", "acceptEdits"), ("plan", "plan"), ("ask", "default")):
            argv = self.agent.ask_launch(ask(permission=word)).argv
            self.assertEqual(argv[argv.index("--permission-mode") + 1], flag, word)

    def test_session(self):
        launch = self.agent.session_launch(agents.SessionRequest(prompt="P", permission="auto"))
        self.assertEqual(launch.argv, ["claude", "--permission-mode", "bypassPermissions", "--", "P"])
        self.assertEqual(launch.shell(), "env -u CLAUDECODE claude --permission-mode bypassPermissions -- P")
        self.assertEqual(self.agent.session_launch(agents.SessionRequest(prompt="P")).argv, ["claude", "--", "P"])

    def test_parse_envelope(self):
        env = json.dumps({"type": "result", "result": "ok", "is_error": False, "duration_ms": 5, "total_cost_usd": 0.5,
                          "usage": {"input_tokens": 3, "output_tokens": 4, "cache_read_input_tokens": 1,
                                    "cache_creation_input_tokens": 2},
                          "modelUsage": {"claude-opus-5-5": {}}, "structured_output": {"a": 1}})
        r = self.agent.parse(env, 0, ask())
        self.assertEqual((r.text, r.cost_usd, r.model, r.structured), ("ok", 0.5, "claude-opus-5-5", {"a": 1}))
        self.assertEqual((r.input_tokens, r.output_tokens, r.cache_read_input_tokens, r.cache_creation_input_tokens), (3, 4, 1, 2))
        bad = self.agent.parse(json.dumps({"result": "Something went wrong", "is_error": True}), 0, ask())
        self.assertFalse(bad.ok); self.assertIn("Something went wrong", bad.error)
        plain = self.agent.parse("plain", 0, ask(model="m"))
        self.assertFalse(plain.is_envelope); self.assertEqual((plain.text, plain.model), ("plain", "m"))


class CursorTest(unittest.TestCase):
    def setUp(self):
        self.agent = agents.get("cursor")

    def test_headless_takes_prompt_as_argument_and_maps_permissions(self):
        launch = self.agent.ask_launch(ask(permission="auto", effort="xhigh", schema={"x": 1}))
        argv = launch.argv
        self.assertEqual(argv[:4], ["cursor-agent", "-p", "--output-format", "json"])
        self.assertIn("--force", argv); self.assertIn("--trust", argv)
        self.assertNotIn("--model", argv)                          # no default model for cursor
        self.assertNotIn("--effort", argv); self.assertNotIn("--json-schema", argv)
        self.assertEqual(argv[-1], "P"); self.assertIsNone(launch.stdin)
        argv = self.agent.ask_launch(ask(model="sonnet-4", permission="plan")).argv
        self.assertEqual(argv[argv.index("--mode") + 1], "plan"); self.assertEqual(argv[argv.index("--model") + 1], "sonnet-4")
        self.assertNotIn("--force", self.agent.ask_launch(ask(permission="none")).argv)

    def test_session(self):
        self.assertEqual(self.agent.session_launch(agents.SessionRequest(prompt="P", permission="auto")).argv,
                         ["cursor-agent", "--force", "P"])
        self.assertEqual(self.agent.session_launch(agents.SessionRequest(prompt="P", permission="plan", model="gpt-5")).argv,
                         ["cursor-agent", "--mode", "plan", "--model", "gpt-5", "P"])

    def test_parse_result_object(self):
        out = '{"type":"result","subtype":"success","is_error":false,"duration_ms":842,"result":"hello","session_id":"s"}\n'
        r = self.agent.parse(out, 0, ask())
        self.assertTrue(r.ok); self.assertEqual(r.text, "hello"); self.assertEqual(r.duration_ms, 842)
        self.assertEqual(r.cost_usd, 0.0)                           # cursor reports no usage
        r = self.agent.parse('{"type":"result","is_error":true,"result":"Not logged in"}', 0, ask())
        self.assertFalse(r.ok); self.assertIn("Not logged in", r.error)
        self.assertEqual(self.agent.parse("noise\n" + out, 0, ask()).text, "hello")   # last JSON object wins
        r = self.agent.parse("just text", 0, ask(model="m"))
        self.assertFalse(r.is_envelope); self.assertEqual(r.text, "just text"); self.assertTrue(r.ok)


class OpenCodeTest(unittest.TestCase):
    def setUp(self):
        self.agent = agents.get("opencode")

    def test_headless(self):
        launch = self.agent.ask_launch(ask(model="anthropic/claude-sonnet-4-5", permission="edits"))
        argv = launch.argv
        self.assertEqual(argv[:4], ["opencode", "run", "--format", "json"])
        self.assertIn("--auto", argv)
        self.assertEqual(argv[argv.index("--model") + 1], "anthropic/claude-sonnet-4-5")
        self.assertEqual(argv[-1], "P"); self.assertIsNone(launch.stdin)
        argv = self.agent.ask_launch(ask(permission="ask")).argv
        self.assertNotIn("--auto", argv); self.assertNotIn("--model", argv)

    def test_session(self):
        self.assertEqual(self.agent.session_launch(agents.SessionRequest(prompt="P", permission="edits")).argv,
                         ["opencode", "--prompt", "P", "--auto"])

    def test_parse_jsonl_events(self):
        lines = [
            {"type": "step_start", "part": {"type": "step-start"}},
            {"type": "text", "part": {"type": "text", "text": "first"}},
            {"type": "step_finish", "part": {"reason": "tool-calls", "cost": 0.01,
                                             "tokens": {"input": 100, "output": 10, "reasoning": 5, "cache": {"read": 50, "write": 7}}}},
            {"type": "text", "part": {"type": "text", "text": "second"}},
            {"type": "step_finish", "part": {"reason": "stop", "cost": 0.02,
                                             "tokens": {"input": 200, "output": 20, "reasoning": 0, "cache": {"read": 0, "write": 0}}}},
        ]
        r = self.agent.parse("\n".join(json.dumps(line) for line in lines), 0, ask(model="anthropic/x"))
        self.assertTrue(r.ok); self.assertEqual(r.text, "first\nsecond")
        self.assertAlmostEqual(r.cost_usd, 0.03); self.assertEqual(r.input_tokens, 300)
        self.assertEqual(r.output_tokens, 35); self.assertEqual(r.cache_read_input_tokens, 50)
        self.assertEqual(r.cache_creation_input_tokens, 7)
        err = json.dumps({"type": "error", "error": {"name": "ProviderAuthError", "data": {"message": "No API key"}}})
        r = self.agent.parse(err, 0, ask())
        self.assertFalse(r.ok); self.assertIn("No API key", r.error)
        r = self.agent.parse("plain", 0, ask())
        self.assertFalse(r.is_envelope); self.assertEqual(r.text, "plain")


class EndToEndTest(unittest.TestCase):
    """call_agent through each agent with the fake CLIs first on PATH."""

    def setUp(self):
        self._env = dict(os.environ)
        self.tmp = tempfile.TemporaryDirectory()
        bin_dir = Path(self.tmp.name) / "bin"; bin_dir.mkdir()
        for name, fake in (("claude", "fake-claude"), ("cursor-agent", "fake-cursor-agent"), ("opencode", "fake-opencode")):
            (bin_dir / name).write_text(f'#!/usr/bin/env bash\nexec "{FAKES}/{fake}" "$@"\n'); (bin_dir / name).chmod(0o755)
        for f in ("fake-claude", "fake-cursor-agent", "fake-opencode"):
            os.chmod(FAKES / f, 0o755)
        os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ["PATH"]
        for var in ("MIGITE_USAGE_LEDGER", "MIGITE_AGENT", "MIGITE_CONFIG", "MIGITE_PERMISSION_MODE"):
            os.environ.pop(var, None)
        # An empty HOME/XDG so the developer's real ~/.config/migite never leaks in.
        home = Path(self.tmp.name) / "home"; (home / ".config").mkdir(parents=True)
        os.environ["HOME"] = str(home); os.environ["XDG_CONFIG_HOME"] = str(home / ".config")
        self.ledger = str(Path(self.tmp.name) / "usage.jsonl")
        self.argv_log = str(Path(self.tmp.name) / "argv.log")
        os.environ["FAKE_AGENT_ARGV"] = self.argv_log
        migite_config._CACHE.clear()

    def tearDown(self):
        os.environ.clear(); os.environ.update(self._env)
        migite_call.reset()
        migite_config._CACHE.clear(); self.tmp.cleanup()

    def cfg_for(self, backend, **env_extra):
        env = {k: v for k, v in os.environ.items() if k not in migite_config.ENV_OVERRIDES}
        env["MIGITE_AGENT"] = backend
        env.update(env_extra)
        return migite_config.load(None, env=env, use_cache=False)

    def test_cursor_degrades_schema_and_effort(self):
        migite_call.configure_from(self.cfg_for("cursor"))
        os.environ["FAKE_AGENT_MODE"] = "ok"
        r = migite_call.call_agent("hello cursor", "verdict", label="t", tool="x", ledger=self.ledger,
                                   schema={"type": "object"}, effort="xhigh")
        self.assertTrue(r.text.startswith("cursor:hello cursor"))
        self.assertIsNone(r.structured)                                 # degraded: no structured output
        argv = Path(self.argv_log).read_text()
        self.assertIn("--trust", argv); self.assertNotIn("--effort", argv)
        self.assertNotIn("--json-schema", argv); self.assertNotIn("--model", argv)
        rec = migite_call.read_ledger(self.ledger)[0]
        self.assertTrue(rec["ok"]); self.assertEqual(rec["cost_usd"], 0.0); self.assertEqual(rec["duration_ms"], 842)
        os.environ["FAKE_AGENT_MODE"] = "error"
        with self.assertRaises(migite_call.AgentError) as cm:
            migite_call.call_agent("x", "verdict", ledger=self.ledger)
        self.assertIn("Not logged in", str(cm.exception))

    def test_opencode_usage_and_pinned_model(self):
        cfg = self.cfg_for("opencode")
        cfg._data["models"]["standard"] = "anthropic/claude-sonnet-4-5"
        migite_call.configure_from(cfg)
        os.environ["FAKE_AGENT_MODE"] = "ok"
        r = migite_call.call_agent("hello opencode", "knowledge", label="t", tool="x", ledger=self.ledger,
                                   permission="bypassPermissions")
        self.assertTrue(r.text.startswith("opencode:hello opencode")); self.assertIn("second part", r.text)
        self.assertAlmostEqual(r.usage.cost_usd, 0.02); self.assertEqual(r.usage.input_tokens, 1500)
        self.assertEqual(r.usage.cache_read_input_tokens, 6000)
        self.assertIn("run --format json --auto --model anthropic/claude-sonnet-4-5", Path(self.argv_log).read_text())
        os.environ["FAKE_AGENT_MODE"] = "error"
        with self.assertRaises(migite_call.AgentError):
            migite_call.call_agent("x", "knowledge", ledger=self.ledger)

    def test_headless_permission_comes_from_the_config(self):
        migite_call.configure_from(self.cfg_for("cursor", MIGITE_PERMISSION_MODE="acceptEdits"))
        os.environ["FAKE_AGENT_MODE"] = "ok"
        migite_call.call_agent("x", "knowledge", ledger=self.ledger)
        self.assertIn("--force", Path(self.argv_log).read_text())      # edits → --force on cursor

    def test_scope_refused_before_the_cli_starts(self):
        migite_call.configure_from(self.cfg_for("cursor"))
        with self.assertRaises(migite_call.ScopeUnsupported):
            migite_call.call_agent("x", "jira", scopes=("jira.read",), ledger=self.ledger)
        self.assertFalse(Path(self.argv_log).exists())
        self.assertEqual(migite_call.read_ledger(self.ledger), [])

    def test_large_prompt_becomes_a_pointer_file_for_arg_agents(self):
        migite_call.configure_from(self.cfg_for("cursor"))
        os.environ["FAKE_AGENT_MODE"] = "ok"
        big = "x" * (migite_call.INLINE_MAX + 10)
        r = migite_call.call_agent(big, "knowledge", ledger=self.ledger)
        argv = Path(self.argv_log).read_text()
        self.assertIn("Your task brief is in the file", argv)
        self.assertLess(len(argv), 2000)
        self.assertTrue(r.text.startswith("cursor:Your task brief"))
        pointed = argv.split("Your task brief is in the file ", 1)[1].split(" ", 1)[0]
        self.assertFalse(Path(pointed).exists())                        # the temp file is cleaned up

    def test_supports_and_model_for_follow_the_agent(self):
        migite_call.configure_from(self.cfg_for("claude"))
        self.assertTrue(migite_call.supports("structured_output")); self.assertTrue(migite_call.supports("scope:jira.read"))
        self.assertEqual(migite_call.model_for("critic"), "claude-opus-5-5")
        migite_call.configure_from(self.cfg_for("opencode"))
        self.assertFalse(migite_call.supports("structured_output")); self.assertTrue(migite_call.supports("usage"))
        self.assertEqual(migite_call.model_for("critic"), "")           # no model until pinned
        migite_call.configure_from(self.cfg_for("cursor"))
        self.assertFalse(migite_call.supports("usage"))

    def test_model_defaults_per_agent(self):
        self.assertEqual(self.cfg_for("claude").model("critic"), "claude-opus-5-5")
        self.assertEqual(self.cfg_for("cursor").model("critic"), "")
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
