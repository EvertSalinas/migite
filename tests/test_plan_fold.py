#!/usr/bin/env python3
"""tests/test_plan_fold.py - migite.plan_fold keeps plan.md current without
letting the model rewrite it.

The model proposes exact find/replace edits; an edit lands only when its text
appears exactly once in the plan body, and the revision history (the record of
which runs the plan reflects) is never editable by it. Every successful fold
adds one revision line, even with no edits, so later prompts can leave that
run's amendment out. The agent is replaced by a fake; no network.
Run: python3 -m unittest tests/test_plan_fold.py"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from migite import plan_fold  # noqa: E402

PLAN = """---
created: 2026-09-01
---
# BB-1: Record message reads

## Approach
ENQUEUE_DELAY is 1 minute, so the message rows are usually stored first.

## Open questions
### 1. Where should the go-live date live?
Undecided.
"""


class EditsTest(unittest.TestCase):
    def test_an_edit_whose_text_appears_once_is_applied(self):
        new, applied, rejected = plan_fold.apply_edits(PLAN, [{"find": "ENQUEUE_DELAY is 1 minute", "replace": "ENQUEUE_DELAY is 15 seconds"}])
        self.assertIn("ENQUEUE_DELAY is 15 seconds, so", new)
        self.assertEqual((len(applied), rejected), (1, []))

    def test_text_that_is_missing_or_ambiguous_is_rejected_and_the_plan_kept(self):
        plan = PLAN + "\nUndecided.\n"
        new, applied, rejected = plan_fold.apply_edits(plan, [
            {"find": "ENQUEUE_DELAY is 2 minutes", "replace": "x"},
            {"find": "Undecided.", "replace": "Settled."},
            {"find": "", "replace": "x"},
            {"find": "Approach", "replace": "Approach"},
            "not an edit",
        ])
        self.assertEqual(new, plan)
        self.assertEqual(applied, [])
        self.assertEqual([r["reason"] for r in rejected],
                         ["text not found in the document", "text appears 2 times in the document",
                          "malformed edit", "no change", "malformed edit"])

    def test_the_revision_history_can_never_be_edited(self):
        plan = plan_fold.add_revision(PLAN, "- 2026-09-02 `01-amend-x`: Delay is 15 seconds.")
        new, applied, rejected = plan_fold.apply_edits(plan, [{"find": "Delay is 15 seconds.", "replace": "gone"}])
        self.assertEqual(new, plan)
        self.assertEqual(rejected[0]["reason"], "text not found in the document")

    def test_edits_apply_in_order_against_the_updated_text(self):
        new, applied, _ = plan_fold.apply_edits(PLAN, [
            {"find": "1 minute", "replace": "15 seconds"},
            {"find": "15 seconds, so", "replace": "15 seconds (amendment 01), so"},
        ])
        self.assertIn("15 seconds (amendment 01), so", new)
        self.assertEqual(len(applied), 2)


class HistoryTest(unittest.TestCase):
    def test_the_first_revision_adds_the_section_at_the_end(self):
        plan = plan_fold.add_revision(PLAN, "- 2026-09-02 `01-amend-x`: A.")
        self.assertTrue(plan.endswith("\n## Revision history\n\n- 2026-09-02 `01-amend-x`: A.\n"))

    def test_later_revisions_append_under_the_same_heading(self):
        plan = plan_fold.add_revision(plan_fold.add_revision(PLAN, "- d `00-build`: A."), "- d `01-amend-x`: B.")
        self.assertEqual(plan.count("## Revision history"), 1)
        self.assertTrue(plan.endswith("- d `00-build`: A.\n- d `01-amend-x`: B.\n"))

    def test_folded_runs_reads_run_slugs_from_the_history_only(self):
        plan = plan_fold.add_revision(PLAN.replace("Undecided.", "See `02-amend-not-history`."), "- d `00-build`: uses `ENQUEUE_DELAY`.")
        plan = plan_fold.add_revision(plan, "- d `01-amend-reduce-delay`: B.")
        self.assertEqual(plan_fold.folded_runs(plan), ["00-build", "01-amend-reduce-delay"])
        self.assertEqual(plan_fold.folded_runs(PLAN), [])


class ReplyTest(unittest.TestCase):
    def test_structured_output_is_used_when_present(self):
        reply = plan_fold.parse_reply("ignored", {"revision": "R", "edits": [{"find": "a", "replace": "b"}]})
        self.assertEqual(reply, {"revision": "R", "edits": [{"find": "a", "replace": "b"}]})

    def test_json_in_text_is_found_even_inside_a_code_fence(self):
        reply = plan_fold.parse_reply('```json\n{"revision": "R", "edits": []}\n```')
        self.assertEqual(reply, {"revision": "R", "edits": []})

    def test_a_reply_without_usable_json_is_none(self):
        self.assertIsNone(plan_fold.parse_reply("I updated the plan for you."))
        self.assertIsNone(plan_fold.parse_reply('{"revision": "R", "edits": "all of it"}'))


class FoldTest(unittest.TestCase):
    def test_applied_edits_get_the_models_revision_line(self):
        new, report = plan_fold.fold(PLAN, {"revision": "Delay is 15 seconds.", "edits": [{"find": "1 minute", "replace": "15 seconds"}]},
                                     date="2026-09-30", run_slug="01-amend-reduce-delay")
        self.assertTrue(new.endswith("- 2026-09-30 `01-amend-reduce-delay`: Delay is 15 seconds.\n"))
        self.assertTrue(report["changed_body"])

    def test_no_applied_edit_still_records_the_run_but_claims_no_change(self):
        new, report = plan_fold.fold(PLAN, {"revision": "Delay is 15 seconds.", "edits": [{"find": "absent", "replace": "x"}]},
                                     date="2026-09-30", run_slug="01-amend-x")
        self.assertTrue(new.endswith("- 2026-09-30 `01-amend-x`: No plan changes.\n"))
        self.assertEqual(new.split("## Revision history")[0].rstrip("\n"), PLAN.rstrip("\n"))
        self.assertFalse(report["changed_body"])
        self.assertEqual(len(report["rejected"]), 1)

    def test_the_prompt_omits_the_revision_history_and_carries_the_amendment(self):
        plan = plan_fold.add_revision(PLAN, "- d `00-build`: SECRET-HISTORY.")
        prompt = plan_fold.build_prompt(plan, "01-amend-x", amendment="# Amendment 01\nReduce the delay.",
                                        fixes=[("fix-r1.md", "renamed the job")])
        self.assertNotIn("SECRET-HISTORY", prompt)
        self.assertIn("Reduce the delay.", prompt)
        self.assertIn("### fix-r1.md\nrenamed the job", prompt)


class CliTest(unittest.TestCase):
    def run_fold(self, reply_text, *, raises=None):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "plan.md").write_text(PLAN)
        args = SimpleNamespace(plan=str(tmp / "plan.md"), run_slug="01-amend-x", date="2026-09-30",
                               out=str(tmp / "new.md"), report=str(tmp / "report.json"),
                               amendment="", implementation="", fix=[], review="")
        fake = mock.Mock(side_effect=raises) if raises else mock.Mock(return_value=SimpleNamespace(text=reply_text, structured=None))
        with mock.patch.object(plan_fold.gateway, "call_agent", fake), \
             mock.patch.object(plan_fold.gateway, "supports", return_value=False):
            rc = plan_fold._cmd_fold(args)
        return rc, tmp, fake

    def test_a_good_reply_writes_the_new_plan_and_a_report_but_never_touches_plan_md(self):
        rc, tmp, fake = self.run_fold('{"revision": "Delay is 15s.", "edits": [{"find": "1 minute", "replace": "15 seconds"}]}')
        self.assertEqual(rc, 0)
        self.assertIn("15 seconds", (tmp / "new.md").read_text())
        self.assertEqual((tmp / "plan.md").read_text(), PLAN)
        self.assertEqual(json.loads((tmp / "report.json").read_text())["revision"], "- 2026-09-30 `01-amend-x`: Delay is 15s.")
        self.assertEqual(fake.call_args.args[1], "plan_fold")

    def test_an_unusable_reply_exits_1_and_writes_nothing(self):
        rc, tmp, _ = self.run_fold("Sure, here is the updated plan: ...")
        self.assertEqual(rc, 1)
        self.assertFalse((tmp / "new.md").exists())

    def test_an_agent_failure_exits_1(self):
        rc, tmp, _ = self.run_fold("", raises=plan_fold.gateway.AgentError("boom"))
        self.assertEqual(rc, 1)
        self.assertFalse((tmp / "new.md").exists())


if __name__ == "__main__":
    unittest.main()
