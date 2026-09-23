#!/usr/bin/env python3
"""tests/test_migite_call.py - unittest coverage for migite_call, the shared
headless call path + usage ledger. Uses tests/fake-claude on PATH so no
real model call is made. Run: python3 -m unittest tests/test_migite_call.py"""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import agents  # noqa: E402
import migite_call  # noqa: E402

FAKE_DIR = str(ROOT / "tests")


class _FakeClaude(unittest.TestCase):
    """Puts a `claude` shim first on PATH that execs tests/fake-claude, so no
    test can ever reach the real CLI (and spend real money)."""

    def setUp(self):
        self._env = dict(os.environ)
        self.tmp = tempfile.TemporaryDirectory()
        os.chmod(ROOT / "tests" / "fake-claude", 0o755)
        shim_dir = Path(self.tmp.name) / "bin"
        shim_dir.mkdir()
        shim = shim_dir / "claude"
        shim.write_text(f'#!/usr/bin/env bash\nexec "{FAKE_DIR}/fake-claude" "$@"\n')
        shim.chmod(0o755)
        os.environ["PATH"] = str(shim_dir) + os.pathsep + os.environ.get("PATH", "")
        os.environ.pop(migite_call.LEDGER_ENV, None)
        os.environ.pop("FAKE_CLAUDE_ARGV", None)
        self.ledger = str(Path(self.tmp.name) / "usage.jsonl")

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        migite_call.reset()
        self.tmp.cleanup()

    def mode(self, m):
        os.environ["FAKE_CLAUDE_MODE"] = m


class CallClaudeTest(_FakeClaude):
    def test_success_returns_text_and_usage(self):
        self.mode("envelope")
        r = migite_call.call_agent("hello world", "knowledge", model="claude-sonnet-5", tool="t", label="l", ledger=self.ledger)
        self.assertTrue(r.text.startswith("echo:hello world"))
        self.assertIsNone(r.structured)
        self.assertEqual(r.usage.input_tokens, 10)
        self.assertEqual(r.usage.output_tokens, 39)
        self.assertEqual(r.usage.cache_creation_input_tokens, 23624)
        self.assertAlmostEqual(r.usage.cost_usd, 0.0475)
        self.assertEqual(r.usage.model, "claude-sonnet-5")
        self.assertTrue(r.usage.ok)

    def test_ledger_gets_one_line_per_call(self):
        self.mode("envelope")
        migite_call.call_agent("a", "knowledge", model="m", tool="migite-plan", label="explore:models", ledger=self.ledger)
        migite_call.call_agent("b", "knowledge", model="m", tool="migite-plan", label="synthesize_plan", ledger=self.ledger)
        recs = migite_call.read_ledger(self.ledger)
        self.assertEqual(len(recs), 2)
        self.assertEqual([r["label"] for r in recs], ["explore:models", "synthesize_plan"])
        self.assertEqual(recs[0]["tool"], "migite-plan")

    def test_ledger_env_var_is_honoured(self):
        self.mode("envelope")
        os.environ[migite_call.LEDGER_ENV] = self.ledger
        migite_call.call_agent("a", "knowledge", model="m")
        self.assertEqual(len(migite_call.read_ledger(self.ledger)), 1)

    def test_no_ledger_is_a_noop(self):
        self.mode("envelope")
        migite_call.call_agent("a", "knowledge", model="m")  # must not raise, must not write anywhere

    def test_structured_output_is_parsed(self):
        self.mode("structured")
        r = migite_call.call_agent("x", "knowledge", model="m", schema={"type": "object"})
        self.assertEqual(r.structured["verdict"], "NEEDS FIXES")
        self.assertEqual(len(r.structured["findings"]), 2)
        self.assertIn("## Verdict: NEEDS FIXES", r.structured["document"])

    def test_schema_is_passed_on_the_command_line(self):
        self.mode("structured")
        argv_file = str(Path(self.tmp.name) / "argv")
        os.environ["FAKE_CLAUDE_ARGV"] = argv_file
        migite_call.call_agent("x", "knowledge", model="m", schema={"type": "object", "required": ["verdict"]})
        argv = Path(argv_file).read_text()
        self.assertIn("--json-schema", argv)
        self.assertIn('"required": ["verdict"]', argv)
        self.assertIn("--output-format json", argv)

    def test_effort_flag_is_passed_and_skipped_for_haiku(self):
        self.mode("envelope")
        argv_file = str(Path(self.tmp.name) / "argv")
        os.environ["FAKE_CLAUDE_ARGV"] = argv_file
        migite_call.call_agent("x", "knowledge", model="claude-opus-5-5", effort="xhigh")
        self.assertIn("--effort xhigh", Path(argv_file).read_text())
        migite_call.call_agent("x", "knowledge", model="claude-haiku-4-5-20251001", effort="xhigh")
        self.assertNotIn("--effort", Path(argv_file).read_text())   # Haiku rejects the flag
        migite_call.call_agent("x", "knowledge", model="claude-sonnet-5", effort="none")
        self.assertNotIn("--effort", Path(argv_file).read_text())
        migite_call.call_agent("x", "knowledge", model="claude-sonnet-5")
        self.assertNotIn("--effort", Path(argv_file).read_text())

    def test_role_effort_table_from_configure(self):
        self.mode("envelope")
        argv_file = str(Path(self.tmp.name) / "argv")
        os.environ["FAKE_CLAUDE_ARGV"] = argv_file
        old = dict(migite_call.ROLE_EFFORT)
        try:
            migite_call.ROLE_EFFORT = {"critic": "max", "knowledge": None}
            migite_call.call_agent("x", "critic", model="claude-opus-5-5")
            self.assertIn("--effort max", Path(argv_file).read_text())
            migite_call.call_agent("x", "knowledge", model="claude-sonnet-5")
            self.assertNotIn("--effort", Path(argv_file).read_text())
            migite_call.call_agent("x", "critic", model="claude-opus-5-5", effort="low")   # explicit beats table
            self.assertIn("--effort low", Path(argv_file).read_text())
        finally:
            migite_call.ROLE_EFFORT = old

    def test_plain_text_stdout_falls_back_gracefully(self):
        self.mode("text")
        r = migite_call.call_agent("hi", "knowledge", model="m", ledger=self.ledger)
        self.assertTrue(r.text.startswith("plain text result for: hi"))
        self.assertEqual(r.usage.input_tokens, 0)
        self.assertEqual(r.raw, {})
        self.assertEqual(len(migite_call.read_ledger(self.ledger)), 1)  # still recorded, zero usage

    def test_is_error_envelope_raises(self):
        self.mode("is_error")
        with self.assertRaises(migite_call.AgentError) as cm:
            migite_call.call_agent("x", "knowledge", model="m", ledger=self.ledger)
        self.assertIn("Something went wrong", str(cm.exception))
        self.assertFalse(migite_call.read_ledger(self.ledger)[0]["ok"])

    def test_nonzero_exit_raises(self):
        self.mode("exit1")
        with self.assertRaises(migite_call.AgentError):
            migite_call.call_agent("x", "knowledge", model="m")

    def test_timeout_raises_and_records(self):
        self.mode("timeout")
        with self.assertRaises(migite_call.AgentError) as cm:
            migite_call.call_agent("x", "knowledge", model="m", timeout=1, ledger=self.ledger)
        self.assertIn("timed out", str(cm.exception))
        self.assertFalse(migite_call.read_ledger(self.ledger)[0]["ok"])

    def test_claudecode_is_stripped_from_env(self):
        self.mode("envelope")
        os.environ["CLAUDECODE"] = "1"
        # A stricter shim that refuses to run if CLAUDECODE leaked through.
        probe_dir = Path(self.tmp.name) / "probe"
        probe_dir.mkdir()
        probe = probe_dir / "claude"
        probe.write_text('#!/usr/bin/env bash\n[[ -n "${CLAUDECODE:-}" ]] && { echo nested >&2; exit 7; }\nexec "%s/fake-claude" "$@"\n' % FAKE_DIR)
        probe.chmod(0o755)
        os.environ["PATH"] = str(probe_dir) + os.pathsep + os.environ["PATH"]
        r = migite_call.call_agent("x", "knowledge", model="m")
        self.assertTrue(r.text.startswith("echo:"))



class GatewayTest(_FakeClaude):
    """Role resolution, permission words, scopes, the prompt pointer, sessions, reset, health check."""

    def setUp(self):
        super().setUp()
        os.environ["FAKE_CLAUDE_ARGV"] = str(Path(self.tmp.name) / "argv")
        self.mode("envelope")

    def argv(self):
        return Path(os.environ["FAKE_CLAUDE_ARGV"]).read_text()

    def test_the_role_picks_the_model(self):
        migite_call.call_agent("x", "critic")
        self.assertIn("--model claude-opus-5-5", self.argv())
        migite_call.call_agent("x", "explore")
        self.assertIn("--model claude-haiku-4-5-20251001", self.argv())
        migite_call.call_agent("x", "critic", model="pinned")      # an explicit model wins
        self.assertIn("--model pinned", self.argv())

    def test_model_for_without_a_config_uses_the_agent_defaults(self):
        self.assertEqual(migite_call.model_for("heal"), "claude-sonnet-5")
        migite_call.AGENT = agents.get("cursor")
        self.assertEqual(migite_call.model_for("heal"), "")

    def test_permission_words_and_aliases_reach_the_cli(self):
        migite_call.call_agent("x", "knowledge", permission="auto")
        self.assertIn("--permission-mode bypassPermissions", self.argv())
        migite_call.call_agent("x", "knowledge", permission="acceptEdits")
        self.assertIn("--permission-mode acceptEdits", self.argv())
        migite_call.call_agent("x", "knowledge")                   # default: permissions.headless = none
        self.assertNotIn("--permission-mode", self.argv())
        with self.assertRaises(ValueError):
            migite_call.call_agent("x", "knowledge", permission="sometimes")

    def test_scopes_reach_claude_as_tools(self):
        migite_call.call_agent("x", "jira", scopes=["jira.read"])
        self.assertIn("mcp__claude_ai_Atlassian__getJiraIssue", self.argv())

    def test_fit_prompt(self):
        self.assertEqual(migite_call.fit_prompt("short"), ("short", None))
        big = "y" * (migite_call.INLINE_MAX + 1)
        text, tmp = migite_call.fit_prompt(big)
        try:
            self.assertIn(f"Your task brief is in the file {tmp}", text)
            self.assertEqual(Path(tmp).read_text(), big)
        finally:
            os.unlink(tmp)
        text, tmp = migite_call.fit_prompt(big, prompt_file="/logs/prompt.txt")
        self.assertIsNone(tmp)
        self.assertIn("/logs/prompt.txt", text)

    def test_session_launch_defaults_and_pointer(self):
        self.assertEqual(migite_call.session_launch("do it").argv,
                         ["claude", "--permission-mode", "bypassPermissions", "--", "do it"])
        self.assertEqual(migite_call.session_launch("do it", permission="plan").argv[1:3], ["--permission-mode", "plan"])
        big = "z" * (migite_call.INLINE_MAX + 1)
        launch = migite_call.session_launch(big, prompt_file="/logs/p.txt")
        self.assertIn("/logs/p.txt", launch.argv[-1])
        self.assertLess(len(launch.argv[-1]), 1000)

    def test_reset_restores_the_built_in_state(self):
        migite_call.AGENT = agents.get("opencode")
        migite_call.ROLE_EFFORT = {"critic": "max"}
        migite_call.DEFAULT_PERMISSION = "auto"
        migite_call.reset()
        self.assertEqual((migite_call.AGENT.name, migite_call.ROLE_EFFORT, migite_call.DEFAULT_PERMISSION, migite_call.CONFIG),
                         ("claude", {}, "none", None))

    def test_check_agent_reports_path_and_missing_cli(self):
        status = migite_call.check_agent()
        self.assertEqual(status["name"], "claude")
        self.assertTrue(status["path"].endswith("/claude"))
        migite_call.AGENT = agents.get("cursor", binary="no-such-agent-cli")
        self.assertEqual(migite_call.check_agent()["path"], "")

class HelpersTest(unittest.TestCase):
    def test_severity_counts(self):
        self.assertEqual(migite_call.severity_counts("🔴 a 🔴 b 🟡 c 🟢 d 🟢 e 🟢 f"),
                         {"critical": 2, "warning": 1, "note": 3})

    def test_summarize_and_format(self):
        recs = [
            {"tool": "migite-plan", "label": "explore", "model": "haiku", "input_tokens": 100, "output_tokens": 10,
             "cache_read_input_tokens": 0, "cache_creation_input_tokens": 1000, "cost_usd": 0.01, "duration_ms": 1000, "ok": True},
            {"tool": "migite-plan", "label": "synth", "model": "sonnet", "input_tokens": 200, "output_tokens": 20,
             "cache_read_input_tokens": 50, "cache_creation_input_tokens": 0, "cost_usd": 0.20, "duration_ms": 2000, "ok": True},
            {"tool": "migite", "label": "knowledge", "model": "sonnet", "input_tokens": 300, "output_tokens": 30,
             "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0, "cost_usd": 0.30, "duration_ms": 3000, "ok": False},
        ]
        s = migite_call.summarize(recs)
        self.assertEqual(s["total"]["calls"], 3)
        self.assertEqual(s["total"]["failed"], 1)
        self.assertAlmostEqual(s["total"]["cost_usd"], 0.51)
        self.assertEqual(s["total"]["input_tokens"], 600)
        self.assertEqual(s["by_model"]["sonnet"]["calls"], 2)
        self.assertEqual(s["by_tool"]["migite"]["calls"], 1)
        table = migite_call.format_summary(s)
        self.assertIn("sonnet", table)
        self.assertIn("$0.51", table)
        self.assertIn("1 call(s) failed", table)
        self.assertIn("cache-creation tokens: 1,000", table)

    def test_format_summary_empty(self):
        self.assertIn("No headless model calls", migite_call.format_summary(migite_call.summarize([])))

    def test_get_field(self):
        obj = {"a": {"b": [10, {"c": "x"}]}, "flag": False}
        self.assertEqual(migite_call.get_field(obj, "a.b.1.c"), "x")
        self.assertEqual(migite_call.get_field(obj, "a.b.0"), 10)
        self.assertIsNone(migite_call.get_field(obj, "a.z"))
        self.assertIsNone(migite_call.get_field(obj, "a.b.9"))


class CliTest(unittest.TestCase):
    """The subcommands bash uses from this module: summary, field."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ledger = str(Path(self.tmp.name) / "usage.jsonl")

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *args, stdin=""):
        return subprocess.run([sys.executable, str(ROOT / "migite_call.py"), *args],
                              input=stdin, capture_output=True, text=True)

    def test_summary_writes_json_and_prints_table(self):
        Path(self.ledger).write_text(json.dumps({"tool": "migite", "label": "x", "model": "m", "input_tokens": 1,
                                                 "output_tokens": 2, "cost_usd": 1.25, "duration_ms": 10, "ok": True}) + "\n")
        out = str(Path(self.tmp.name) / "usage.json")
        r = self.run_cli("summary", "--ledger", self.ledger, "--json", out)
        self.assertEqual(r.returncode, 0)
        self.assertIn("$1.25", r.stdout)
        data = json.loads(Path(out).read_text())
        self.assertEqual(data["total"]["calls"], 1)
        self.assertIn("note", data)

    def test_field(self):
        f = Path(self.tmp.name) / "review.json"
        f.write_text(json.dumps({"verdict": "needs_fixes", "counts": {"critical": 2}, "clean": True, "findings": [{"a": 1}]}))
        self.assertEqual(self.run_cli("field", str(f), "verdict").stdout.strip(), "needs_fixes")
        self.assertEqual(self.run_cli("field", str(f), "counts.critical").stdout.strip(), "2")
        self.assertEqual(self.run_cli("field", str(f), "clean").stdout.strip(), "true")
        self.assertEqual(json.loads(self.run_cli("field", str(f), "findings").stdout), [{"a": 1}])
        self.assertEqual(self.run_cli("field", str(f), "missing").returncode, 1)
        self.assertEqual(self.run_cli("field", "/nonexistent.json", "x").returncode, 1)



class MissingCliTest(unittest.TestCase):
    """A CLI that is not installed surfaces as AgentError, never a raw OSError."""

    TOOLS = ("migite-plan", "migite-review", "migite-audit.py", "migite-blueprint.py",
             "migite-explore.py", "migite-pr-review.py")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ledger = str(Path(self.tmp.name) / "usage.jsonl")
        self._agent = migite_call.AGENT

    def tearDown(self):
        migite_call.AGENT = self._agent
        self.tmp.cleanup()

    def use_binary(self, binary):
        migite_call.AGENT = agents.get("cursor", binary=binary)

    def test_a_missing_binary_raises_agent_error_and_is_recorded(self):
        self.use_binary("no-such-agent-cli")
        with self.assertRaises(migite_call.AgentError) as cm:
            migite_call.call_agent("hi", "knowledge", model="", label="probe", ledger=self.ledger)
        self.assertIn("no-such-agent-cli", str(cm.exception))
        self.assertIn("agent.command", str(cm.exception))
        rec = migite_call.read_ledger(self.ledger)[0]
        self.assertFalse(rec["ok"])
        self.assertEqual(rec["label"], "probe")

    def test_require_cli_refuses_a_missing_binary(self):
        self.use_binary("no-such-agent-cli")
        with self.assertRaises(migite_call.AgentError) as cm:
            migite_call.require_cli()
        self.assertIn("not found on PATH", str(cm.exception))

    def test_require_cli_accepts_an_installed_binary(self):
        self.use_binary(sys.executable)
        migite_call.require_cli()   # must not raise

    def test_every_tool_checks_for_its_cli_after_loading_config(self):
        for tool in self.TOOLS:
            src = (ROOT / tool).read_text()
            configured = src.index("migite_call.configure_from(cfg)")
            self.assertIn("migite_call.require_cli()", src[configured:], f"{tool} never calls require_cli")

    @unittest.skipUnless(importlib.util.find_spec("langgraph"), "langgraph not installed")
    def test_a_tool_stops_with_one_clear_error_when_the_cli_is_missing(self):
        cfg = Path(self.tmp.name) / "cfg.json"
        cfg.write_text(json.dumps({"agent": {"backend": "cursor", "command": "no-such-agent-cli"}}))
        env = {**os.environ, "MIGITE_CONFIG": str(cfg), "MIGITE_AGENT": "cursor"}
        r = subprocess.run([sys.executable, str(ROOT / "migite-audit.py"), "--repo-root", self.tmp.name],
                           capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(r.returncode, 1)
        self.assertIn("no-such-agent-cli", r.stderr)
        self.assertNotIn("Traceback", r.stderr)

class OldNameShimTest(unittest.TestCase):
    """migite_claude.py keeps old imports, old names, and old symlinks working."""

    def test_old_import_is_the_same_module(self):
        import migite_claude
        self.assertIs(migite_claude, migite_call)

    def test_old_names_alias_the_new_ones(self):
        import migite_claude
        self.assertIs(migite_claude.call_claude, migite_call.call_agent)
        self.assertIs(migite_claude.ClaudeError, migite_call.AgentError)

    def test_setting_a_global_through_the_old_name_reaches_the_real_module(self):
        import migite_claude
        old = dict(migite_call.ROLE_EFFORT)
        try:
            migite_claude.ROLE_EFFORT = {"critic": "max"}
            self.assertEqual(migite_call.ROLE_EFFORT, {"critic": "max"})
        finally:
            migite_call.ROLE_EFFORT = old

    def test_old_script_path_runs_the_same_cli(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "x.json"
            f.write_text(json.dumps({"verdict": "READY"}))
            r = subprocess.run([sys.executable, str(ROOT / "migite_claude.py"), "field", str(f), "verdict"],
                               capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.strip(), "READY")

if __name__ == "__main__":
    unittest.main()
