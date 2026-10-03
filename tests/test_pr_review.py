#!/usr/bin/env python3
"""tests/test_pr_review.py - every finding in a PR review comes with a fix:
the reviewer and synthesis prompts ask for one, a draft that leaves a finding without
one gets a single retry, and a failed reviewer still yields an actionable finding.
The agent is replaced by a fake; skipped where langgraph isn't installed (the tool
imports it at module load). Run: python3 -m unittest tests/test_pr_review.py"""

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

HAS_LANGGRAPH = importlib.util.find_spec("langgraph") is not None


def load_tool():
    # A fresh copy per test, so replacing its call_agent never leaks between tests.
    spec = importlib.util.spec_from_file_location("pr_review_under_test", ROOT / "migite" / "tools" / "pr_review.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


COMPLETE = """# PR Review: feat/x
## Summary
Adds soft delete.

## Findings
### Critical
#### 🔴 1. Unscoped destroy · `app/controllers/items_controller.rb:12`
**Problem:** any user can delete any item.
**Fix:** scope the lookup to the current user:
```ruby
@item = current_user.items.find(params[:id])
```
**Alternative:** a Pundit policy, if the app already uses Pundit elsewhere.

### Notes
#### 🟢 2. Magic number · `app/models/item.rb:4`
**Problem:** 30 is unexplained.
**Fix:** extract `RETENTION_DAYS = 30`.

## Verdict
NEEDS CHANGES
"""

MISSING_ONE = COMPLETE.replace("**Fix:** extract `RETENTION_DAYS = 30`.\n", "")


@unittest.skipUnless(HAS_LANGGRAPH, "langgraph not installed")
class FindingsWithoutFixTest(unittest.TestCase):
    def setUp(self):
        self.tool = load_tool()

    def test_complete_review_has_none_missing(self):
        self.assertEqual(self.tool.findings_without_fix(COMPLETE), [])

    def test_a_finding_without_a_fix_is_named(self):
        missing = self.tool.findings_without_fix(MISSING_ONE)
        self.assertEqual(len(missing), 1)
        self.assertIn("Magic number", missing[0])

    def test_bullet_style_fix_counts_and_other_sections_are_ignored(self):
        doc = ("## Summary\n#### 🔴 not a finding outside Findings\n"
               "## Findings\n#### 🟡 1. Slow query · `a.rb:1`\n- **Fix:** add an index\n## Verdict\nAPPROVED\n")
        self.assertEqual(self.tool.findings_without_fix(doc), [])
        self.assertEqual(self.tool.findings_without_fix("# PR Review\n## Verdict\nAPPROVED\n"), [])


@unittest.skipUnless(HAS_LANGGRAPH, "langgraph not installed")
class SynthesisRetryTest(unittest.TestCase):
    def setUp(self):
        self.tool = load_tool()
        self.calls = []

    def state(self):
        return {"branch": "feat/x", "base": "main", "jira": "", "commits": "abc fix",
                "findings": ["### security\n- 🔴 **Critical** · `a.rb:1` · x"], "changed_files": ["a.rb"]}

    def fake(self, *replies):
        replies = list(replies)

        def call_agent(prompt, role, label=""):
            self.calls.append((prompt, role, label))
            return replies.pop(0)
        self.tool.call_agent = call_agent

    def test_the_synthesis_prompt_requires_a_fix_per_finding(self):
        self.fake(COMPLETE)
        self.tool.synthesize_verdict(self.state())
        prompt = self.calls[0][0]
        self.assertIn("Every finding keeps its Fix", prompt)
        self.assertIn("**Alternative:**", prompt)
        self.assertEqual(len(self.calls), 1)                      # complete draft: no retry

    def test_a_draft_missing_a_fix_is_retried_once_and_the_better_draft_kept(self):
        self.fake(MISSING_ONE, COMPLETE)
        out = self.tool.synthesize_verdict(self.state())
        self.assertEqual(out["verdict"], COMPLETE)
        self.assertEqual([c[2] for c in self.calls], ["synthesize_verdict", "synthesize_verdict:retry"])
        self.assertIn("Magic number", self.calls[1][0])            # the retry names what was missing

    def test_a_worse_retry_is_discarded(self):
        worse = MISSING_ONE.replace("**Fix:** scope the lookup to the current user:\n", "")
        self.fake(MISSING_ONE, worse)
        out = self.tool.synthesize_verdict(self.state())
        self.assertEqual(out["verdict"], MISSING_ONE)
        self.assertEqual(len(self.calls), 2)                      # one retry, never a loop


@unittest.skipUnless(HAS_LANGGRAPH, "langgraph not installed")
class ReviewerPromptTest(unittest.TestCase):
    def setUp(self):
        self.tool = load_tool()
        self.prompts = []

    def dim_state(self):
        return {"dimension": "security", "checks": "- auth", "diff": "+x", "commits": "c", "file_contents": "",
                "rubocop_log": "", "rspec_log": "", "branch": "feat/x"}

    def test_each_reviewer_is_asked_for_a_fix_and_alternatives(self):
        def call_agent(prompt, role, label=""):
            self.prompts.append(prompt)
            return "✅ No issues in security."
        self.tool.call_agent = call_agent
        self.tool.review_dimension(self.dim_state())
        prompt = self.prompts[0]
        for expected in ("**Problem:**", "**Fix:**", "**Alternative:**", "The Fix line is required on every finding"):
            self.assertIn(expected, prompt)

    def test_each_reviewer_must_quote_evidence_and_is_told_it_has_tools(self):
        def call_agent(prompt, role, label=""):
            self.prompts.append(prompt)
            return "✅ No issues in security."
        self.tool.call_agent = call_agent
        self.tool.review_dimension(self.dim_state())
        prompt = self.prompts[0]
        for expected in ("**Evidence:**", "required on every Critical and Warning", "Read, Grep, Glob",
                         "constant lookup through the enclosing modules", "not a Critical"):
            self.assertIn(expected, prompt)
        self.assertNotIn("Only build on code you can see above", prompt)

    def test_a_failed_reviewer_still_gives_an_actionable_finding(self):
        def call_agent(prompt, role, label=""):
            raise RuntimeError("agent timed out")
        self.tool.call_agent = call_agent
        block = self.tool.review_dimension(self.dim_state())["findings"][0]
        self.assertIn("agent timed out", block)
        self.assertIn("**Fix:**", block)
        self.assertIn("migite doctor", block)


WRONG_CRITICAL = """- 🔴 **Critical** · `app/controllers/insights_controller.rb:5` · Wrong base controller
  - **Problem:** inherits from the bare ApplicationController.
  - **Evidence:** `app/controllers/insights_controller.rb:5` `class InsightsController < ApplicationController`
  - **Fix:** inherit from API::V1::Chat::ApplicationController."""

CONTROLLER = ("module API\n  module V1\n    module Chat\n      module Messages\n"
              "        class InsightsController < ApplicationController\n")


@unittest.skipUnless(HAS_LANGGRAPH, "langgraph not installed")
class VerificationStepTest(unittest.TestCase):
    def setUp(self):
        self.tool = load_tool()
        self.calls = []
        self.tmp = tempfile.TemporaryDirectory()
        path = Path(self.tmp.name) / "app" / "controllers"
        path.mkdir(parents=True)
        (path / "insights_controller.rb").write_text(CONTROLLER)

    def tearDown(self):
        self.tmp.cleanup()

    def fake(self, reply):
        def call_agent(prompt, role, label=""):
            self.calls.append((prompt, role, label))
            return reply
        self.tool.call_agent = call_agent

    def state(self, **extra):
        return {"branch": "feat/x", "base": "main", "repo_root": self.tmp.name, "diff": "", "jira": "",
                "commits": "abc", "changed_files": ["a.rb"],
                "findings": [f"### correctness\n{WRONG_CRITICAL}"], **extra}

    def test_the_graph_verifies_between_the_reviewers_and_the_synthesis(self):
        edges = {(e.source, e.target) for e in self.tool.build_graph().get_graph().edges}
        self.assertIn(("review_dimension", "verify_findings"), edges)
        self.assertIn(("verify_findings", "synthesize_verdict"), edges)

    def test_a_refuted_critical_never_reaches_the_synthesis(self):
        self.fake("VERDICT: REFUTED\nEVIDENCE: `x.rb:1` `module Chat`\nREASON: resolves to the Chat base.")
        out = self.tool.verify_findings(self.state())
        self.assertEqual([c[1] for c in self.calls], ["refute"])
        self.assertNotIn("🔴", out["verified"][0])
        self.assertEqual(out["refuted"][0]["title"], "Wrong base controller")

        self.calls.clear()
        self.fake("### unused")
        self.tool.synthesize_verdict(self.state(**out))
        self.assertNotIn("Wrong base controller", self.calls[0][0])

    def test_the_synthesis_appends_what_was_refuted_to_the_review(self):
        self.fake(COMPLETE)
        refuted = [{"dimension": "correctness", "severity": "critical", "title": "Wrong base controller",
                    "location": "a.rb:5", "reason": "resolves lexically", "evidence": ""}]
        out = self.tool.synthesize_verdict(self.state(verified=["### correctness\n✅ No issues remain"], refuted=refuted))
        self.assertTrue(out["verdict"].startswith(COMPLETE.rstrip()))
        self.assertIn("## Refuted by verification", out["verdict"])
        self.assertIn("Wrong base controller", out["verdict"])

    def test_the_synthesis_prompt_carries_verification_through_and_defines_the_verdict(self):
        self.fake(COMPLETE)
        self.tool.synthesize_verdict(self.state())
        prompt = self.calls[0][0]
        self.assertIn("Evidence, Verified and Verification lines", prompt)
        self.assertIn("never promote it", prompt)
        self.assertIn("NEEDS CHANGES only when a Critical finding remains", prompt)


if __name__ == "__main__":
    unittest.main()
