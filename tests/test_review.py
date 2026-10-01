#!/usr/bin/env python3
"""tests/test_review.py - migite-review's frontend reviewer: it runs only when
migite passes changed views or JavaScript, its system-spec rule depends on
whether the repo has spec/system, and it sees the frontend lint log and the
browser check report. The agent is replaced by a fake; skipped where langgraph
isn't installed (the tool imports it at module load).
Run: python3 -m unittest tests/test_review.py"""

import importlib.util
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

HAS_LANGGRAPH = importlib.util.find_spec("langgraph") is not None


def load_tool():
    # A fresh copy per test, so replacing its call_agent never leaks between tests.
    spec = importlib.util.spec_from_file_location("review_under_test", ROOT / "migite" / "tools" / "review.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def state(**overrides):
    s = {"plan": "# Plan", "implementation": "notes", "rubocop_log": "", "rspec_log": "",
         "git_diff": "", "testing_plan": "", "frontend_files": [], "frontend_lint_log": "",
         "system_specs": False, "browser_check": ""}
    s.update(overrides)
    return s


BROWSER_REPORT = "# Browser check\nResult: FAIL - the modal never opened\nURL: http://localhost:3000\n"


@unittest.skipUnless(HAS_LANGGRAPH, "langgraph not installed")
class FrontendReviewerTest(unittest.TestCase):
    def setUp(self):
        self.review = load_tool()
        self.calls = []

        def fake_call_agent(prompt, role, label="", schema=None):
            self.calls.append((role, prompt))
            return types.SimpleNamespace(text="✅ No issues in this dimension.", structured=None)
        self.review.call_agent = fake_call_agent

    def dims(self, **overrides):
        return [d for d, _ in self.review.active_dimensions(state(**overrides))]

    def test_backend_only_diff_keeps_four_reviewers(self):
        self.assertEqual(self.dims(), ["correctness", "security", "test_coverage", "testing_plan"])

    def test_diff_touching_views_or_js_adds_the_frontend_reviewer(self):
        self.assertEqual(self.dims(frontend_files=["app/views/items/index.html.erb"])[-1], "frontend")
        sends = self.review.route_to_reviewers(state(frontend_files=["app/javascript/controllers/modal_controller.js"]))
        self.assertEqual(len(sends), 5)

    def test_system_spec_rule_follows_whether_the_repo_has_spec_system(self):
        self.assertIn("🟡 Warning", self.review.frontend_criteria(True))
        self.assertIn("do not demand one", self.review.frontend_criteria(False))

    def test_frontend_reviewer_sees_the_files_lint_log_and_browser_report(self):
        self.review.review_dimension({
            **state(frontend_files=["app/views/items/_form.html.erb"],
                    frontend_lint_log="== erb_lint ==\n1 error(s) were found in ERB files",
                    browser_check=BROWSER_REPORT),
            "dimension": "frontend", "description": self.review.frontend_criteria(False),
        })
        role, prompt = self.calls[0]
        self.assertEqual(role, "review_frontend")
        self.assertIn("- app/views/items/_form.html.erb", prompt)
        self.assertIn("1 error(s) were found in ERB files", prompt)
        self.assertIn("the modal never opened", prompt)

    def test_verdict_synthesis_gets_the_browser_result_only_for_frontend_diffs(self):
        self.assertEqual(self.review.synth_frontend_block(state(browser_check=BROWSER_REPORT)), "")
        block = self.review.synth_frontend_block(state(frontend_files=["a.js"], browser_check=BROWSER_REPORT))
        self.assertIn("Result: FAIL - the modal never opened", block)


if __name__ == "__main__":
    unittest.main()
