#!/usr/bin/env python3
"""tests/test_review_amendments.py - migite-review sees a task's amendments.

Without them, the reviewer graded amended code against the original plan and
reported the difference as plan drift. Every specialist and the verdict
synthesis now get the amendments, newest first, so a length cut drops the
oldest ones. The agent is replaced by a fake; skipped where langgraph isn't
installed (the tool imports it at module load).
Run: python3 -m unittest tests/test_review_amendments.py"""

import importlib.util
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

HAS_LANGGRAPH = importlib.util.find_spec("langgraph") is not None

AMEND_01 = "# Amendment 01\n## Feedback\nReduce ENQUEUE_DELAY to 15 seconds."
AMEND_02 = "# Amendment 02\n## Feedback\nFall back to a hardcoded go-live date."


def load_tool():
    # A fresh copy per test, so replacing its call_agent never leaks between tests.
    spec = importlib.util.spec_from_file_location("review_under_test", ROOT / "migite" / "tools" / "review.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@unittest.skipUnless(HAS_LANGGRAPH, "langgraph not installed")
class AmendmentsBlockTest(unittest.TestCase):
    def setUp(self):
        self.tool = load_tool()

    def test_no_amendments_adds_nothing(self):
        self.assertEqual(self.tool.amendments_block([], 4000), "")
        self.assertEqual(self.tool.amendments_block(["", "  \n"], 4000), "")

    def test_amendments_are_listed_newest_first_and_win_over_the_plan(self):
        block = self.tool.amendments_block([AMEND_01, AMEND_02], 4000)
        self.assertIn("the amendment wins", block)
        self.assertLess(block.index("Amendment 02"), block.index("Amendment 01"))

    def test_a_length_cut_keeps_the_newest_amendment(self):
        block = self.tool.amendments_block([AMEND_01, AMEND_02], len(AMEND_02))
        self.assertIn(AMEND_02, block)
        self.assertNotIn("Amendment 01", block)


@unittest.skipUnless(HAS_LANGGRAPH, "langgraph not installed")
class ReviewPromptsIncludeAmendmentsTest(unittest.TestCase):
    def setUp(self):
        self.tool = load_tool()
        self.prompts = []

        def call_agent(prompt, role, label="", schema=None):
            self.prompts.append(prompt)
            return SimpleNamespace(text="## Verdict: READY TO COMMIT", structured=None)
        self.tool.call_agent = call_agent

    def dimension_state(self, amendments):
        return {"dimension": "correctness", "description": "matches the plan", "plan": "# Plan\nENQUEUE_DELAY is 1 minute.",
                "implementation": "", "rubocop_log": "", "rspec_log": "", "git_diff": "", "testing_plan": "",
                "amendments": amendments}

    def test_each_specialist_is_given_the_amendments(self):
        self.tool.review_dimension(self.dimension_state([AMEND_01]))
        self.assertIn("Reduce ENQUEUE_DELAY to 15 seconds.", self.prompts[0])

    def test_a_task_without_amendments_gets_no_amendments_section(self):
        self.tool.review_dimension(self.dimension_state([]))
        self.assertNotIn("## Amendments", self.prompts[0])

    def test_a_reviewer_stopped_by_its_budget_is_a_warning_not_a_blocker(self):
        def over_budget(prompt, role, label="", schema=None):
            raise self.tool.gateway.AgentError("claude failed (exit 0, 90.0s): stopped at the --max-budget-usd cap ($2.01 spent)")
        self.tool.call_agent = over_budget
        finding = self.tool.review_dimension(self.dimension_state([]))["findings"][0]
        self.assertIn("🟡 **Warning**", finding)
        self.assertNotIn("🔴", finding)

    def test_the_reviewer_is_told_its_tools_are_read_only(self):
        self.tool.review_dimension(self.dimension_state([]))
        self.assertIn("read-only tools (Read, Grep, Glob)", self.prompts[0])

    def test_correctness_criteria_do_not_count_superseded_plan_text_as_a_finding(self):
        criteria = dict(self.tool.REVIEW_DIMENSIONS)["correctness"]
        self.assertIn("amendment supersedes the plan", criteria)

    def test_the_verdict_synthesis_is_given_the_amendments(self):
        state = {"review_cmd": "format", "plan": "# Plan", "findings": ["### correctness\nok"],
                 "rubocop_log": "", "rspec_log": "", "amendments": [AMEND_02]}
        with mock.patch.object(self.tool.gateway, "supports", return_value=False):
            self.tool.synthesize_verdict(state)
        self.assertIn("Fall back to a hardcoded go-live date.", self.prompts[0])


if __name__ == "__main__":
    unittest.main()
