#!/usr/bin/env python3
"""tests/test_doc_edits.py - migite.doc_edits changes a document with exact
find/replace edits instead of a full rewrite.

An edit lands only when its text appears exactly once; a reply with no usable
edit returns None so the caller falls back to its full rewrite; an empty edit
list means nothing needed to change. Also covers migite.tools.plan's
refine_plan using edits first (skipped where langgraph isn't installed). The
agent is replaced by a fake; no network.
Run: python3 -m unittest tests/test_doc_edits.py"""

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from migite import doc_edits  # noqa: E402

HAS_LANGGRAPH = importlib.util.find_spec("langgraph") is not None

DOC = """# Testing Plan

### Verification steps
1. Wait 1 minute, then check the receipts.
2. grep the log for `RecordMessageReadsJob`.
"""


def reply(text: str):
    return mock.patch.object(doc_edits.gateway, "call_agent", return_value=SimpleNamespace(text=text, structured=None))


class UpdateTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(doc_edits.gateway, "supports", return_value=False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_update(self):
        return doc_edits.update(DOC, name="testing-plan.md", task="Amendment 01 changed the delay.",
                                context=[("Amendment", "Reduce the delay to 15 seconds.")], role="testing_plan", label="t")

    def test_applied_edits_return_the_edited_document(self):
        with reply('{"revision": "15 seconds.", "edits": [{"find": "Wait 1 minute", "replace": "Wait 15 seconds"}]}'):
            new, report = self.run_update()
        self.assertIn("Wait 15 seconds, then", new)
        self.assertEqual(len(report["applied"]), 1)

    def test_an_empty_edit_list_means_nothing_to_change(self):
        with reply('{"revision": "No changes.", "edits": []}'):
            new, _ = self.run_update()
        self.assertEqual(new, DOC)

    def test_an_unusable_reply_asks_the_caller_to_fall_back(self):
        with reply("Here is the full updated testing plan: ..."):
            new, report = self.run_update()
        self.assertIsNone(new)
        self.assertIn("no usable JSON", report["reason"])

    def test_edits_that_all_miss_ask_the_caller_to_fall_back(self):
        with reply('{"revision": "x", "edits": [{"find": "Wait 2 minutes", "replace": "y"}]}'):
            new, report = self.run_update()
        self.assertIsNone(new)
        self.assertEqual(report["rejected"][0]["reason"], "text not found in the document")

    def test_the_prompt_carries_the_task_context_and_the_whole_document(self):
        with reply('{"revision": "", "edits": []}') as fake:
            self.run_update()
        prompt = fake.call_args.args[0]
        self.assertIn("Amendment 01 changed the delay.", prompt)
        self.assertIn("## Amendment\nReduce the delay to 15 seconds.", prompt)
        self.assertIn(DOC, prompt)
        self.assertEqual(fake.call_args.args[1], "testing_plan")


class CliTest(unittest.TestCase):
    def run_cli(self, text=None, raises=None):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "doc.md").write_text(DOC)
        (tmp / "amendment.md").write_text("Reduce the delay.")
        args = SimpleNamespace(doc=str(tmp / "doc.md"), out=str(tmp / "out.md"), report=str(tmp / "report.json"),
                               role="testing_plan", label="t", name="testing-plan.md", task="Do it.",
                               context=[f"Amendment={tmp / 'amendment.md'}"])
        fake = mock.Mock(side_effect=raises) if raises else mock.Mock(return_value=SimpleNamespace(text=text, structured=None))
        with mock.patch.object(doc_edits.gateway, "call_agent", fake), \
             mock.patch.object(doc_edits.gateway, "supports", return_value=False):
            rc = doc_edits._cmd_update(args)
        return rc, tmp, fake

    def test_exit_0_writes_the_updated_document_and_never_touches_the_original(self):
        rc, tmp, fake = self.run_cli('{"revision": "r", "edits": [{"find": "1 minute", "replace": "15 seconds"}]}')
        self.assertEqual(rc, 0)
        self.assertIn("15 seconds", (tmp / "out.md").read_text())
        self.assertEqual((tmp / "doc.md").read_text(), DOC)
        self.assertIn("Reduce the delay.", fake.call_args.args[0])       # --context file content reaches the prompt

    def test_exit_2_when_there_is_nothing_usable(self):
        rc, tmp, _ = self.run_cli("not json")
        self.assertEqual(rc, 2)
        self.assertFalse((tmp / "out.md").exists())
        self.assertIn("reason", json.loads((tmp / "report.json").read_text()))

    def test_exit_1_when_the_call_fails(self):
        rc, tmp, _ = self.run_cli(raises=doc_edits.gateway.AgentError("boom"))
        self.assertEqual(rc, 1)
        self.assertFalse((tmp / "out.md").exists())


@unittest.skipUnless(HAS_LANGGRAPH, "langgraph not installed")
class RefinePlanTest(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location("plan_under_test", ROOT / "migite" / "tools" / "plan.py")
        self.tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.tool)
        # A realistic plan: the full-rewrite fallback's guards reject anything shorter
        # than 3 headings / 400 characters as a stub.
        self.body = "\n## Summary\nStore read receipts.\n\n## Scope\n- app/models/chat/message_read.rb\n\n## Risks\n" + ("- none\n" * 60)
        self.state = {"plan_draft": "# Plan\n## Approach\nUse a 1 minute delay.\n" + self.body,
                      "critic_findings": "🟡 the delay is too long"}

    def test_the_critic_findings_are_applied_as_edits_without_a_full_rewrite(self):
        full_rewrite = mock.Mock()
        self.tool.call_agent = full_rewrite
        edited = "# Plan\n## Approach\nUse a 15 second delay.\n"
        with mock.patch.object(self.tool.doc_edits, "update", return_value=(edited, {"applied": [{}], "rejected": []})):
            out = self.tool.refine_plan(self.state)
        self.assertEqual(out, {"plan_final": edited, "refine_status": "applied_as_edits"})
        full_rewrite.assert_not_called()

    def test_no_usable_edits_falls_back_to_the_full_rewrite(self):
        rewritten = "# Plan\n## Approach\nUse a 15 second delay, rewritten.\n" + self.body
        self.tool.call_agent = mock.Mock(return_value=rewritten)
        with mock.patch.object(self.tool.doc_edits, "update", return_value=(None, {"reason": "no usable JSON"})):
            out = self.tool.refine_plan(self.state)
        self.assertEqual(out, {"plan_final": rewritten, "refine_status": "applied"})

    def test_a_failed_edit_call_also_falls_back(self):
        rewritten = "# Plan\n## Approach\nRewritten.\n" + self.body
        self.tool.call_agent = mock.Mock(return_value=rewritten)
        with mock.patch.object(self.tool.doc_edits, "update", side_effect=self.tool.gateway.AgentError("timeout")):
            out = self.tool.refine_plan(self.state)
        self.assertEqual(out["plan_final"], rewritten)


if __name__ == "__main__":
    unittest.main()
