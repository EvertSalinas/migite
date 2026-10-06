#!/usr/bin/env python3
"""tests/test_testing_plan.py - migite.testing_plan writes testing-plan.md. One prompt serves
migite-plan (Phase 1, from the finished plan) and Phase 3 (plan.testing_plan_when: review, from the
plan and the diff); the CLI writes its output only when the call gave a non-empty reply. The agent is
replaced by a fake; no network, and no langgraph is needed (bash runs this module on its own).
Run: python3 -m unittest tests/test_testing_plan.py"""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from migite import config  # noqa: E402
from migite import testing_plan  # noqa: E402

PLAN = "# Plan\n## Approach\nRename Item#name.\n"
DIFF = "app/item.rb | 2 +-\n\n-def name; \"a\"\n+def name; \"renamed\"\n\nFiles changed (tracked and new):\napp/brand.rb\n"
BROWSER = "without a full page reload"


class BuildPromptTest(unittest.TestCase):
    def test_from_the_plan_alone_it_is_the_phase_1_prompt(self):
        prompt = testing_plan.build_prompt(PLAN)
        self.assertIn(f"## Implementation plan\n{PLAN}\n\n## Instructions\n", prompt)
        self.assertNotIn("What was built", prompt)
        for heading in ("# Testing Plan", "### Prerequisites", "### Verification steps", "### Teardown"):
            self.assertIn(heading, prompt)

    def test_the_diff_goes_between_the_plan_and_the_instructions_and_wins_over_the_plan(self):
        prompt = testing_plan.build_prompt(PLAN, diff=DIFF)
        self.assertLess(prompt.index("## Implementation plan"), prompt.index("## What was built"))
        self.assertLess(prompt.index("## What was built"), prompt.index("## Instructions"))
        self.assertIn('+def name; "renamed"', prompt)
        self.assertIn("app/brand.rb", prompt)
        self.assertIn("the diff is right", prompt)

    def test_browser_steps_only_for_a_frontend_change(self):
        self.assertNotIn(BROWSER, testing_plan.build_prompt(PLAN))
        self.assertIn(BROWSER, testing_plan.build_prompt(PLAN, frontend=True))
        self.assertIn(BROWSER, testing_plan.build_prompt(PLAN, frontend=True, diff=DIFF))

    def test_role_and_label_are_what_the_ledger_reads(self):
        self.assertEqual((testing_plan.ROLE, testing_plan.LABEL, testing_plan.TOOL),
                         ("testing_plan", "generate_testing_plan", "migite-plan"))
        self.assertEqual(config.ROLE_TIERS[testing_plan.ROLE], "standard")


class CliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        (self.tmp / "plan.md").write_text(PLAN)
        (self.tmp / "change.txt").write_text(DIFF)
        # No config file or user config is read: the gateway is replaced, so the config is never used.
        for target in (mock.patch.object(testing_plan.config, "load", return_value=mock.Mock()),
                       mock.patch.object(testing_plan.gateway, "configure_from")):
            target.start()
            self.addCleanup(target.stop)

    def run_cli(self, *extra, text="# Testing Plan\n1. check it\n", raises=None, existing=None):
        out = self.tmp / "testing-plan.md"
        if existing is not None:
            out.write_text(existing)
        fake = mock.Mock(side_effect=raises) if raises else mock.Mock(return_value=SimpleNamespace(text=text))
        with mock.patch.object(testing_plan.gateway, "call_agent", fake), mock.patch("builtins.print"):
            rc = testing_plan.main(["--plan", str(self.tmp / "plan.md"), "--out", str(out), *extra])
        return rc, out, fake

    def test_exit_0_writes_the_reply(self):
        rc, out, _ = self.run_cli()
        self.assertEqual(rc, 0)
        self.assertEqual(out.read_text(), "# Testing Plan\n1. check it\n")

    def test_it_asks_for_the_testing_plan_role_under_the_planner_s_ledger_label(self):
        _, _, fake = self.run_cli()
        self.assertEqual(fake.call_args.args[1], "testing_plan")
        self.assertEqual(fake.call_args.kwargs["label"], "generate_testing_plan")
        self.assertEqual(fake.call_args.kwargs["tool"], "migite-plan")

    def test_the_diff_file_and_the_frontend_flag_reach_the_prompt(self):
        _, _, fake = self.run_cli("--diff", str(self.tmp / "change.txt"), "--frontend")
        prompt = fake.call_args.args[0]
        self.assertIn("app/brand.rb", prompt)
        self.assertIn(BROWSER, prompt)

    def test_a_missing_diff_file_is_the_plan_only_prompt(self):
        _, _, fake = self.run_cli("--diff", str(self.tmp / "nope.txt"))
        self.assertNotIn("What was built", fake.call_args.args[0])

    def test_exit_2_on_an_empty_reply_and_the_existing_file_survives(self):
        for text in ("", "  \n"):
            rc, out, _ = self.run_cli(text=text, existing="the last good testing plan")
            self.assertEqual(rc, 2)
            self.assertEqual(out.read_text(), "the last good testing plan")

    def test_exit_1_when_the_call_fails_and_the_existing_file_survives(self):
        rc, out, _ = self.run_cli(raises=testing_plan.gateway.AgentError("timeout"), existing="the last good testing plan")
        self.assertEqual(rc, 1)
        self.assertEqual(out.read_text(), "the last good testing plan")

    def test_exit_1_on_a_config_error_before_any_call(self):
        with mock.patch.object(testing_plan.config, "load", side_effect=config.ConfigError("bad")):
            rc, out, fake = self.run_cli()
        self.assertEqual(rc, 1)
        self.assertFalse(out.exists())
        fake.assert_not_called()


if __name__ == "__main__":
    unittest.main()
