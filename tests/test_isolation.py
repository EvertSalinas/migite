#!/usr/bin/env python3
"""tests/test_isolation.py - headless calls run isolated.

Each headless role gets only the tools config.ROLE_TOOLS names (none for a
text-only call, Read/Grep/Glob for the reviewers) and none of the CLI's MCP
servers, plugins, hooks or skills. Without that, every call paid ~24k tokens of
CLI context and "no tools" prompts browsed the repo turn after turn. The user's
and the repo's CLAUDE.md are passed explicitly, so their rules still apply.
permissions.headless_tools: default restores the old behaviour. Uses
tests/fake-claude on PATH; no real model call.
Run: python3 -m unittest tests/test_isolation.py"""

import io
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from migite import agents   # noqa: E402
from migite import config   # noqa: E402
from migite import gateway  # noqa: E402

ISOLATION_FLAGS = ("--tools", "--strict-mcp-config", "--safe-mode", "--no-session-persistence",
                   "--exclude-dynamic-system-prompt-sections")


def ask(**kw):
    kw.setdefault("prompt", "P")
    return agents.AskRequest(**kw)


class ClaudeArgvTest(unittest.TestCase):
    def setUp(self):
        self.agent = agents.get("claude")
        patcher = mock.patch.object(type(self.agent), "instructions", return_value="RULES")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_text_only_call_gets_no_tools_and_none_of_the_cli_context(self):
        argv = self.agent.ask_launch(ask(isolated=True)).argv
        for flag in ISOLATION_FLAGS:
            self.assertIn(flag, argv)
        self.assertEqual(argv[argv.index("--tools") + 1], "")
        self.assertEqual(argv[argv.index("--append-system-prompt") + 1], "RULES")
        self.assertNotIn("--max-budget-usd", argv)

    def test_a_reviewer_gets_read_only_tools_and_a_budget(self):
        argv = self.agent.ask_launch(ask(isolated=True, tools=("Read", "Grep", "Glob"), max_budget_usd=2.0)).argv
        self.assertEqual(argv[argv.index("--tools") + 1], "Read,Grep,Glob")
        self.assertEqual(argv[argv.index("--max-budget-usd") + 1], "2")

    def test_a_call_that_is_not_isolated_keeps_todays_argv(self):
        argv = self.agent.ask_launch(ask(model="m")).argv
        self.assertEqual(argv, ["claude", "--print", "--output-format", "json", "--model", "m"])

    def test_no_instructions_means_no_append_flag(self):
        with mock.patch.object(type(self.agent), "instructions", return_value=""):
            self.assertNotIn("--append-system-prompt", self.agent.ask_launch(ask(isolated=True)).argv)

    def test_a_budget_stop_is_reported_as_one(self):
        env = '{"type": "result", "subtype": "error_max_budget_usd", "is_error": true, "total_cost_usd": 2.013, "num_turns": 9}'
        r = self.agent.parse(env, 0, ask())
        self.assertFalse(r.ok)
        self.assertEqual(r.error, "stopped at the --max-budget-usd cap ($2.01 spent)")


class InstructionsTest(unittest.TestCase):
    def test_user_file_then_repo_root_down_to_the_working_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            home, repo = Path(tmp) / "home", Path(tmp) / "repo"
            (home / ".claude").mkdir(parents=True)
            (home / ".claude" / "CLAUDE.md").write_text("USER RULES")
            sub = repo / "app" / "models"
            sub.mkdir(parents=True)
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            (repo / "CLAUDE.md").write_text("REPO RULES")
            (repo / "app" / "CLAUDE.md").write_text("APP RULES")
            (Path(tmp) / "CLAUDE.md").write_text("OUTSIDE THE REPO")
            with mock.patch.dict(os.environ, {"HOME": str(home)}):
                text = agents.get("claude").instructions(str(sub))
        self.assertLess(text.index("USER RULES"), text.index("REPO RULES"))
        self.assertLess(text.index("REPO RULES"), text.index("APP RULES"))
        self.assertNotIn("OUTSIDE THE REPO", text)

    def test_nothing_to_pass_is_an_empty_string(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"HOME": tmp}):
            self.assertEqual(agents.get("claude").instructions(tmp), "")


class PolicyTest(unittest.TestCase):
    def tearDown(self):
        gateway.reset()

    def test_roles_map_to_their_tools(self):
        self.assertEqual(gateway.tools_policy("review_correctness"), "read")
        self.assertEqual(gateway.tools_policy("pr_review_security"), "read")
        self.assertEqual(gateway.tools_policy("refute"), "read")
        self.assertEqual(gateway.tools_policy("think"), "none")
        self.assertEqual(gateway.tools_policy("summary"), "none")
        self.assertEqual(gateway.tools_policy("heal"), "default")

    def test_an_automata_session_keeps_the_interactive_sessions_toolset(self):
        # --automata runs run_phase's sessions headless on this role: isolated, the
        # implement session would have no tools and could not edit a file
        self.assertEqual(gateway.tools_policy("session"), "default")

    def test_every_review_dimension_gets_read_only_tools(self):
        reviewers = [r for r in config.ROLE_TIERS if r.startswith(("review_", "pr_review_"))]
        self.assertIn("review_frontend", reviewers)
        for role in reviewers:
            self.assertEqual(gateway.tools_policy(role), "read", role)

    def test_a_scoped_call_keeps_the_cli_context_its_mcp_tools_live_in(self):
        self.assertEqual(gateway.tools_policy("jira", ("jira.read",)), "default")

    def test_headless_tools_default_restores_the_old_behaviour_for_every_role(self):
        gateway.HEADLESS_TOOLS = "default"
        self.assertEqual(gateway.tools_policy("think"), "default")
        self.assertEqual(gateway.tools_policy("review_correctness"), "default")

    def test_every_role_in_the_table_is_a_real_role_with_a_known_policy(self):
        self.assertTrue(set(config.ROLE_TOOLS) <= set(config.ROLE_TIERS))
        self.assertTrue(set(config.ROLE_TOOLS.values()) <= {"none", "read", "default"})


class GatewayTest(unittest.TestCase):
    """call_agent end to end with the fake claude; the argv it was run with is recorded."""

    def setUp(self):
        self._env = dict(os.environ)
        self.tmp = tempfile.TemporaryDirectory()
        shim_dir = Path(self.tmp.name) / "bin"
        shim_dir.mkdir()
        (shim_dir / "claude").write_text(f'#!/usr/bin/env bash\nexec "{ROOT}/tests/fake-claude" "$@"\n')
        (shim_dir / "claude").chmod(0o755)
        os.environ["PATH"] = str(shim_dir) + os.pathsep + os.environ.get("PATH", "")
        os.environ["FAKE_CLAUDE_MODE"] = "envelope"
        self.argv_file = Path(self.tmp.name) / "argv"
        os.environ["FAKE_CLAUDE_ARGV"] = str(self.argv_file)
        os.environ.pop(gateway.LEDGER_ENV, None)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        gateway.reset()
        self.tmp.cleanup()

    def argv(self) -> str:
        return self.argv_file.read_text()

    def test_a_text_only_role_runs_isolated(self):
        gateway.call_agent("x", "summary", label="s")
        self.assertIn("--safe-mode", self.argv())
        self.assertNotIn("--max-budget-usd", self.argv())

    def test_a_reviewer_runs_read_only_under_the_configured_budget(self):
        gateway.REVIEW_CALL_MAX_USD = 0.75
        gateway.call_agent("x", "review_security", label="r")
        self.assertIn("--tools Read,Grep,Glob", self.argv())
        self.assertIn("--max-budget-usd 0.75", self.argv())

    def test_heal_keeps_the_full_toolset(self):
        gateway.call_agent("x", "heal", label="h", permission="auto")
        self.assertNotIn("--safe-mode", self.argv())
        self.assertNotIn("--tools", self.argv())

    def test_an_agent_that_cannot_isolate_says_so_once_and_runs_as_before(self):
        gateway.AGENT = agents.get("cursor", binary=str(ROOT / "tests" / "fake-cursor-agent"))
        err = io.StringIO()
        with redirect_stderr(err):
            for _ in range(2):
                try:
                    gateway.call_agent("x", "summary", label="s")
                except gateway.AgentError:
                    pass
        self.assertEqual(err.getvalue().count("can't restrict a headless call"), 1)


class ConfigTest(unittest.TestCase):
    def test_the_defaults_isolate_and_cap_reviewers(self):
        self.assertEqual(config.DEFAULTS["permissions"]["headless_tools"], "isolated")
        self.assertEqual(config.DEFAULTS["budget"]["review_call_max_usd"], 2.0)

    def test_an_unknown_headless_tools_value_is_refused(self):
        with self.assertRaises(config.ConfigError):
            config._coerce("permissions.headless_tools", "everything", "test")


if __name__ == "__main__":
    unittest.main()
