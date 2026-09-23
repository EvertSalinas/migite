#!/usr/bin/env python3
"""tests/test_migite_claude.py — unittest coverage for migite_claude, the shared
`claude --print` wrapper + usage ledger. Uses tests/fake-claude on PATH so no
real model call is made. Run: python3 -m unittest tests/test_migite_claude.py"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import migite_claude  # noqa: E402

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
        os.environ.pop(migite_claude.LEDGER_ENV, None)
        os.environ.pop("FAKE_CLAUDE_ARGV", None)
        self.ledger = str(Path(self.tmp.name) / "usage.jsonl")

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        self.tmp.cleanup()

    def mode(self, m):
        os.environ["FAKE_CLAUDE_MODE"] = m


class CallClaudeTest(_FakeClaude):
    def test_success_returns_text_and_usage(self):
        self.mode("envelope")
        r = migite_claude.call_claude("hello world", "claude-sonnet-5", tool="t", label="l", ledger=self.ledger)
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
        migite_claude.call_claude("a", "m", tool="migite-plan", label="explore:models", ledger=self.ledger)
        migite_claude.call_claude("b", "m", tool="migite-plan", label="synthesize_plan", ledger=self.ledger)
        recs = migite_claude.read_ledger(self.ledger)
        self.assertEqual(len(recs), 2)
        self.assertEqual([r["label"] for r in recs], ["explore:models", "synthesize_plan"])
        self.assertEqual(recs[0]["tool"], "migite-plan")

    def test_ledger_env_var_is_honoured(self):
        self.mode("envelope")
        os.environ[migite_claude.LEDGER_ENV] = self.ledger
        migite_claude.call_claude("a", "m")
        self.assertEqual(len(migite_claude.read_ledger(self.ledger)), 1)

    def test_no_ledger_is_a_noop(self):
        self.mode("envelope")
        migite_claude.call_claude("a", "m")  # must not raise, must not write anywhere

    def test_structured_output_is_parsed(self):
        self.mode("structured")
        r = migite_claude.call_claude("x", "m", schema={"type": "object"})
        self.assertEqual(r.structured["verdict"], "NEEDS FIXES")
        self.assertEqual(len(r.structured["findings"]), 2)
        self.assertIn("## Verdict: NEEDS FIXES", r.structured["document"])

    def test_schema_is_passed_on_the_command_line(self):
        self.mode("structured")
        argv_file = str(Path(self.tmp.name) / "argv")
        os.environ["FAKE_CLAUDE_ARGV"] = argv_file
        migite_claude.call_claude("x", "m", schema={"type": "object", "required": ["verdict"]})
        argv = Path(argv_file).read_text()
        self.assertIn("--json-schema", argv)
        self.assertIn('"required": ["verdict"]', argv)
        self.assertIn("--output-format json", argv)

    def test_plain_text_stdout_falls_back_gracefully(self):
        self.mode("text")
        r = migite_claude.call_claude("hi", "m", ledger=self.ledger)
        self.assertTrue(r.text.startswith("plain text result for: hi"))
        self.assertEqual(r.usage.input_tokens, 0)
        self.assertEqual(r.raw, {})
        self.assertEqual(len(migite_claude.read_ledger(self.ledger)), 1)  # still recorded, zero usage

    def test_is_error_envelope_raises(self):
        self.mode("is_error")
        with self.assertRaises(migite_claude.ClaudeError) as cm:
            migite_claude.call_claude("x", "m", ledger=self.ledger)
        self.assertIn("Something went wrong", str(cm.exception))
        self.assertFalse(migite_claude.read_ledger(self.ledger)[0]["ok"])

    def test_nonzero_exit_raises(self):
        self.mode("exit1")
        with self.assertRaises(migite_claude.ClaudeError):
            migite_claude.call_claude("x", "m")

    def test_timeout_raises_and_records(self):
        self.mode("timeout")
        with self.assertRaises(migite_claude.ClaudeError) as cm:
            migite_claude.call_claude("x", "m", timeout=1, ledger=self.ledger)
        self.assertIn("timed out", str(cm.exception))
        self.assertFalse(migite_claude.read_ledger(self.ledger)[0]["ok"])

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
        r = migite_claude.call_claude("x", "m")
        self.assertTrue(r.text.startswith("echo:"))


class HelpersTest(unittest.TestCase):
    def test_severity_counts(self):
        self.assertEqual(migite_claude.severity_counts("🔴 a 🔴 b 🟡 c 🟢 d 🟢 e 🟢 f"),
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
        s = migite_claude.summarize(recs)
        self.assertEqual(s["total"]["calls"], 3)
        self.assertEqual(s["total"]["failed"], 1)
        self.assertAlmostEqual(s["total"]["cost_usd"], 0.51)
        self.assertEqual(s["total"]["input_tokens"], 600)
        self.assertEqual(s["by_model"]["sonnet"]["calls"], 2)
        self.assertEqual(s["by_tool"]["migite"]["calls"], 1)
        table = migite_claude.format_summary(s)
        self.assertIn("sonnet", table)
        self.assertIn("$0.51", table)
        self.assertIn("1 call(s) failed", table)
        self.assertIn("cache-creation tokens: 1,000", table)

    def test_format_summary_empty(self):
        self.assertIn("No headless model calls", migite_claude.format_summary(migite_claude.summarize([])))

    def test_get_field(self):
        obj = {"a": {"b": [10, {"c": "x"}]}, "flag": False}
        self.assertEqual(migite_claude.get_field(obj, "a.b.1.c"), "x")
        self.assertEqual(migite_claude.get_field(obj, "a.b.0"), 10)
        self.assertIsNone(migite_claude.get_field(obj, "a.z"))
        self.assertIsNone(migite_claude.get_field(obj, "a.b.9"))

    def test_parse_envelope_rejects_non_envelopes(self):
        self.assertIsNone(migite_claude.parse_envelope("not json"))
        self.assertIsNone(migite_claude.parse_envelope('{"foo": 1}'))
        self.assertIsNone(migite_claude.parse_envelope("[1,2]"))
        self.assertEqual(migite_claude.parse_envelope('{"result": "ok"}')["result"], "ok")


class CliTest(unittest.TestCase):
    """The subcommands bash uses: extract, summary, field."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ledger = str(Path(self.tmp.name) / "usage.jsonl")

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *args, stdin=""):
        return subprocess.run([sys.executable, str(ROOT / "migite_claude.py"), *args],
                              input=stdin, capture_output=True, text=True)

    def test_extract_prints_result_and_records_usage(self):
        env = json.dumps({"type": "result", "result": "the answer", "is_error": False, "duration_ms": 12,
                          "total_cost_usd": 0.5, "usage": {"input_tokens": 3, "output_tokens": 4},
                          "modelUsage": {"claude-sonnet-5-20260101": {}}})
        r = self.run_cli("extract", "--tool", "migite", "--label", "knowledge", "--ledger", self.ledger, stdin=env)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "the answer")
        rec = migite_claude.read_ledger(self.ledger)[0]
        self.assertEqual(rec["label"], "knowledge")
        self.assertEqual(rec["model"], "claude-sonnet-5-20260101")  # taken from modelUsage when not given
        self.assertAlmostEqual(rec["cost_usd"], 0.5)

    def test_extract_passes_plain_text_through(self):
        r = self.run_cli("extract", "--label", "plain", "--ledger", self.ledger, stdin="just text\n")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "just text\n")
        recs = migite_claude.read_ledger(self.ledger)   # still counted, with zero usage
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["label"], "plain")
        self.assertEqual(recs[0]["cost_usd"], 0.0)
        self.assertTrue(recs[0]["ok"])

    def test_extract_propagates_failure_exit_code(self):
        r = self.run_cli("extract", "--exit-code", "3", "--ledger", self.ledger, stdin="error text")
        self.assertEqual(r.returncode, 3)
        env = json.dumps({"result": "bad", "is_error": True})
        r = self.run_cli("extract", "--ledger", self.ledger, stdin=env)
        self.assertEqual(r.returncode, 1)
        self.assertFalse(migite_claude.read_ledger(self.ledger)[0]["ok"])

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


if __name__ == "__main__":
    unittest.main()
