#!/usr/bin/env python3
"""tests/test_pr_review.py - every finding in a PR review comes with a fix:
the reviewer and synthesis prompts ask for one, a draft that leaves a finding without
one gets a single retry, and a failed reviewer still yields an actionable finding.
The agent is replaced by a fake; skipped where langgraph isn't installed (the tool
imports it at module load). Run: python3 -m unittest tests/test_pr_review.py"""

import importlib.util
import sys
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

    def test_a_failed_reviewer_still_gives_an_actionable_finding(self):
        def call_agent(prompt, role, label=""):
            raise RuntimeError("agent timed out")
        self.tool.call_agent = call_agent
        block = self.tool.review_dimension(self.dim_state())["findings"][0]
        self.assertIn("agent timed out", block)
        self.assertIn("**Fix:**", block)
        self.assertIn("migite doctor", block)


if __name__ == "__main__":
    unittest.main()
