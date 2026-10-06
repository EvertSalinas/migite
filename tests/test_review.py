#!/usr/bin/env python3
"""tests/test_review.py - migite-review's frontend reviewer: it runs only when
migite passes changed views or JavaScript, its system-spec rule depends on
whether the repo has spec/system, and it sees the frontend lint log and the
browser check report. The agent is replaced by a fake; skipped where langgraph
isn't installed (the tool imports it at module load).
Run: python3 -m unittest tests/test_review.py"""

import importlib.util
import sys
import tempfile
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


@unittest.skipUnless(HAS_LANGGRAPH, "langgraph not installed")
class TestingPlanToggleTest(unittest.TestCase):
    """review.dimensions.testing_plan: off drops the testing-plan reviewer and tells the verdict why."""

    def setUp(self):
        self.review = load_tool()
        self.prompts = []

        def fake_call_agent(prompt, role, label="", schema=None):
            self.prompts.append(prompt)
            return types.SimpleNamespace(text="# Review: t\n\n## Verdict: READY TO COMMIT\n\nclean", structured=None)
        self.review.call_agent = fake_call_agent

    def dims(self, **overrides):
        return [d for d, _ in self.review.active_dimensions(state(**overrides))]

    def synth_prompt(self, **overrides):
        self.review.synthesize_verdict({**state(), "review_cmd": "format", "amendments": [],
                                        "findings": ["### correctness\n✅ No issues in this dimension."], **overrides})
        return self.prompts[0]

    def test_on_is_the_default_when_the_state_says_nothing(self):
        self.assertIn("testing_plan", self.dims())
        self.assertIn("testing_plan", self.dims(testing_plan_enabled=True))

    def test_off_leaves_three_reviewers(self):
        self.assertEqual(self.dims(testing_plan_enabled=False), ["correctness", "security", "test_coverage"])

    def test_off_still_adds_the_frontend_reviewer_for_a_frontend_diff(self):
        dims = self.dims(testing_plan_enabled=False, frontend_files=["app/views/items/index.html.erb"])
        self.assertEqual(dims, ["correctness", "security", "test_coverage", "frontend"])

    def test_off_sends_no_testing_plan_reviewer_even_on_a_full_review(self):
        sends = self.review.route_to_reviewers({**state(testing_plan_enabled=False), "amendments": [], "rerun": []})
        self.assertEqual([s.arg["dimension"] for s in sends], ["correctness", "security", "test_coverage"])

    def test_the_verdict_is_told_the_reviewer_did_not_run_only_when_it_is_off(self):
        off = self.synth_prompt(testing_plan_enabled=False)
        self.assertIn("review.dimensions.testing_plan is off", off)
        self.assertIn("N/A", off)
        self.prompts.clear()
        self.assertNotIn("review.dimensions.testing_plan", self.synth_prompt(testing_plan_enabled=True))
        self.prompts.clear()
        self.assertNotIn("review.dimensions.testing_plan", self.synth_prompt())


CRITICAL = """- 🔴 **Critical** · `app/models/item.rb:2` · Unscoped lookup
  - **Problem:** finds items across accounts.
  - **Evidence:** `app/models/item.rb:2` `Item.find(id)`
  - **Fix:** scope it to the account."""


@unittest.skipUnless(HAS_LANGGRAPH, "langgraph not installed")
class VerificationStepTest(unittest.TestCase):
    def setUp(self):
        self.review = load_tool()
        self.calls = []
        self.tmp = tempfile.TemporaryDirectory()
        (Path(self.tmp.name) / "app" / "models").mkdir(parents=True)
        (Path(self.tmp.name) / "app" / "models" / "item.rb").write_text("class Item\n  Item.find(id)\nend\n")

    def tearDown(self):
        self.tmp.cleanup()

    def fake(self, text="", structured=None):
        def fake_call_agent(prompt, role, label="", schema=None):
            self.calls.append((role, prompt, label))
            return types.SimpleNamespace(text=text, structured=structured)
        self.review.call_agent = fake_call_agent

    def test_code_reviewers_must_quote_evidence_but_document_reviewers_need_not(self):
        self.fake("✅ No issues in this dimension.")
        for dim in ("security", "testing_plan"):
            self.review.review_dimension({**state(), "dimension": dim, "description": "d", "amendments": []})
        security, testing_plan = self.calls[0][1], self.calls[1][1]
        self.assertIn("**Evidence:**", security)
        self.assertIn("constant lookup through the enclosing modules", security)
        self.assertNotIn("**Evidence:**", testing_plan)

    def test_the_graph_verifies_between_the_reviewers_and_the_synthesis(self):
        edges = {(e.source, e.target) for e in self.review.build_graph().get_graph().edges}
        self.assertIn(("review_dimension", "verify_findings"), edges)
        self.assertIn(("verify_findings", "synthesize_verdict"), edges)

    def test_a_refuted_critical_is_removed_and_the_rest_of_the_review_is_unchanged(self):
        self.fake("VERDICT: REFUTED\nEVIDENCE: `app/models/item.rb:2` `Item.find(id)`\nREASON: scoped by a default scope.")
        out = self.review.verify_findings({**state(), "repo_root": self.tmp.name,
                                           "findings": [f"### security\n{CRITICAL}", "### test_coverage\n✅ No issues in this dimension."]})
        self.assertEqual([c[0] for c in self.calls], ["refute"])
        self.assertIn("plan", self.calls[0][1].lower())                       # the refuter is given the plan for intent
        self.assertNotIn("🔴", out["verified"][0])
        self.assertEqual(out["verified"][1], "### test_coverage\n✅ No issues in this dimension.")
        self.assertEqual(out["refuted"][0]["reason"], "scoped by a default scope.")

    def test_the_review_appends_the_refuted_findings_and_records_them_in_review_json(self):
        refuted = [{"dimension": "security", "severity": "critical", "title": "Unscoped lookup", "location": "a.rb:2",
                    "reason": "default scope", "evidence": ""}]
        structured = {"verdict": "READY TO COMMIT", "reason": "clean", "findings": [],
                      "document": "# Review: t\n\n## Verdict: READY TO COMMIT\n\nclean"}
        self.fake(structured=structured)
        out = self.review.synthesize_verdict({**state(), "review_cmd": "format", "amendments": [], "refuted": refuted,
                                              "findings": ["### security\n" + CRITICAL],
                                              "verified": ["### security\n✅ No issues remain"]})
        self.assertNotIn("Unscoped lookup", self.calls[0][1])                 # the synthesis saw the verified findings
        self.assertIn("## Refuted by verification", out["verdict"])
        self.assertTrue(out["verdict"].startswith("# Review: t"))             # the verdict line is still first


if __name__ == "__main__":
    unittest.main()
