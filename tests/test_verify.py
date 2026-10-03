#!/usr/bin/env python3
"""tests/test_verify.py - findings are checked before they reach the verdict: an evidence
gate (the quoted line must exist where the finding says), and a refuter agent that tries to
disprove every Critical. The agent is a fake; needs no langgraph.
Run: python3 -m unittest tests/test_verify.py"""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from migite import verify  # noqa: E402

CONTROLLER = """module API
  module V1
    module Chat
      module Messages
        class InsightsController < ApplicationController
          def index
            authorize message
          end
        end
      end
    end
  end
end
"""

CRITICAL = """- 🔴 **Critical** · `app/controllers/insights_controller.rb:5` · Wrong base controller
  - **Problem:** inherits from the bare ApplicationController, so authorize raises NoMethodError.
  - **Evidence:** `app/controllers/insights_controller.rb:5` `class InsightsController < ApplicationController`
  - **Fix:** inherit from API::V1::Chat::ApplicationController."""

WARNING_NO_EVIDENCE = """- 🟡 **Warning** · `app/models/item.rb:4` · Unscoped lookup
  - **Problem:** finds across accounts.
  - **Fix:** scope it."""

NOTE = """- 🟢 **Note** · `app/models/item.rb:9` · Magic number
  - **Problem:** 30 is unexplained.
  - **Fix:** extract a constant."""


def reader(files):
    return lambda path: files.get(path)


def finding(text):
    return verify.parse_findings(text)[0]


class ParseTest(unittest.TestCase):
    def test_a_block_is_split_into_findings_with_severity_location_and_fields(self):
        found = verify.parse_findings(f"{CRITICAL}\n{NOTE}")
        self.assertEqual([f.severity for f in found], ["critical", "note"])
        self.assertEqual(found[0].title, "Wrong base controller")
        self.assertEqual(verify.location_of(found[0]), ("app/controllers/insights_controller.rb", 5))
        self.assertIn("bare ApplicationController", found[0].problem)
        self.assertEqual(found[1].evidence, "")

    def test_the_review_prompts_dash_style_header_parses_too(self):
        f = finding("🟡 **Warning** \u2014 `a.rb:3` \u2014 thing\n**Problem:** x")
        self.assertEqual(f.severity, "warning")
        self.assertEqual(f.problem, "x")

    def test_a_synthetic_failure_finding_is_recognised(self):
        self.assertTrue(finding("- 🔴 **Critical** · (none) · the security reviewer failed").synthetic)
        self.assertTrue(finding("🟡 **Warning**: the security reviewer stopped at its budget cap").synthetic)
        self.assertFalse(finding(CRITICAL).synthetic)


class EvidenceTest(unittest.TestCase):
    files = {"app/controllers/insights_controller.rb": CONTROLLER}

    def test_a_quote_found_at_the_cited_line_is_ok(self):
        self.assertEqual(verify.check_evidence(finding(CRITICAL), reader(self.files))[0], "ok")

    def test_whitespace_differences_do_not_matter(self):
        text = CRITICAL.replace("class InsightsController < ApplicationController",
                                "class   InsightsController  <  ApplicationController")
        self.assertEqual(verify.check_evidence(finding(text), reader(self.files))[0], "ok")

    def test_a_quote_that_is_not_in_the_file_is_a_mismatch(self):
        text = CRITICAL.replace("class InsightsController < ApplicationController", "class Other < Base")
        status, detail = verify.check_evidence(finding(text), reader(self.files))
        self.assertEqual(status, "mismatch")
        self.assertIn("class Other < Base", detail)

    def test_a_quote_far_from_the_cited_line_is_a_mismatch(self):
        far = CONTROLLER + "\n" * 40 + "class Elsewhere < Base\n"
        text = CRITICAL.replace("class InsightsController < ApplicationController", "class Elsewhere < Base")
        status, _ = verify.check_evidence(finding(text), reader({"app/controllers/insights_controller.rb": far}))
        self.assertEqual(status, "mismatch")

    def test_no_evidence_line_is_missing(self):
        self.assertEqual(verify.check_evidence(finding(WARNING_NO_EVIDENCE), reader({}))[0], "missing")

    def test_a_quote_from_the_diff_counts_when_the_file_is_gone(self):
        text = CRITICAL.replace("class InsightsController < ApplicationController", "removed_call(x)")
        status, _ = verify.check_evidence(finding(text), reader({}), extra="-  removed_call(x)\n")
        self.assertEqual(status, "ok")

    def test_which_findings_get_refuted(self):
        self.assertTrue(verify.needs_refutation(finding(CRITICAL), "ok"))
        self.assertFalse(verify.needs_refutation(finding(WARNING_NO_EVIDENCE), "ok"))
        self.assertTrue(verify.needs_refutation(finding(WARNING_NO_EVIDENCE), "missing"))
        self.assertFalse(verify.needs_refutation(finding(NOTE), "missing"))


class ReaderTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "a.rb").write_text("on disk\n")
        (self.root.parent / "outside.txt").write_text("secret")

    def tearDown(self):
        (self.root.parent / "outside.txt").unlink(missing_ok=True)
        self.tmp.cleanup()

    def test_reads_the_working_tree_and_stays_inside_it(self):
        read = verify.make_reader(str(self.root))
        self.assertEqual(read("a.rb"), "on disk\n")
        self.assertIsNone(read("missing.rb"))
        self.assertIsNone(read("../outside.txt"))
        self.assertIsNone(read("/etc/hosts"))

    def test_prefers_the_content_at_a_ref(self):
        run = lambda *a: subprocess.run(["git", *a], cwd=self.root, check=True, capture_output=True)
        run("init", "-q", "-b", "main")
        run("-c", "user.name=t", "-c", "user.email=t@t", "add", ".")
        run("-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", "commit", "-qm", "x")
        (self.root / "a.rb").write_text("changed in the working tree\n")
        self.assertEqual(verify.make_reader(str(self.root), ref="main")("a.rb"), "on disk\n")
        self.assertEqual(verify.make_reader(str(self.root), ref="nope")("a.rb"), "changed in the working tree\n")


class VerdictParseTest(unittest.TestCase):
    def test_parses_the_required_format(self):
        reply = "VERDICT: refuted\nEVIDENCE: `a.rb:1` `class A < B`\nREASON: B is resolved lexically.\nIt is the Chat one."
        self.assertEqual(verify.parse_verdict(reply),
                         ("REFUTED", "`a.rb:1` `class A < B`", "B is resolved lexically. It is the Chat one."))

    def test_rejects_anything_else(self):
        self.assertIsNone(verify.parse_verdict("I think it is fine."))
        self.assertIsNone(verify.parse_verdict(""))

    def test_the_refuter_prompt_hides_the_fix_and_asks_to_disprove(self):
        prompt = verify.refute_prompt(finding(CRITICAL), "ok", "")
        self.assertIn("DISPROVE", prompt)
        self.assertIn("Wrong base controller", prompt)
        self.assertIn("bare ApplicationController", prompt)
        self.assertNotIn("inherit from API::V1::Chat::ApplicationController", prompt)   # the reviewer's Fix
        self.assertIn("constant", prompt)


class VerifyBlocksTest(unittest.TestCase):
    files = {"app/controllers/insights_controller.rb": CONTROLLER}

    def run_blocks(self, blocks, replies, **kw):
        self.asked = []

        def ask(_prompt, label):
            self.asked.append(label)
            reply = replies.pop(0) if isinstance(replies, list) else replies
            if isinstance(reply, Exception):
                raise reply
            return reply
        return verify.verify_blocks(blocks, ask, reader(self.files), log=lambda *_: None, **kw)

    def test_a_refuted_critical_is_removed_and_recorded(self):
        out, refuted = self.run_blocks(
            [f"### correctness\n{CRITICAL}"],
            ["VERDICT: REFUTED\nEVIDENCE: `x.rb:1` `module Chat`\nREASON: resolves to the Chat base."])
        self.assertNotIn("🔴", out[0])
        self.assertIn("No issues remain", out[0])
        self.assertEqual(refuted[0]["title"], "Wrong base controller")
        self.assertEqual(refuted[0]["reason"], "resolves to the Chat base.")
        self.assertEqual(refuted[0]["severity"], "critical")

    def test_a_confirmed_critical_stays_with_a_verified_line(self):
        out, refuted = self.run_blocks(
            [f"### security\n{CRITICAL}"], ["VERDICT: CONFIRMED\nEVIDENCE: `a.rb:2` `Item.find(id)`\nREASON: unscoped."])
        self.assertIn("🔴 **Critical**", out[0])
        self.assertIn("**Verified:** `a.rb:2` `Item.find(id)`", out[0])
        self.assertEqual(refuted, [])

    def test_an_unverifiable_critical_becomes_a_note(self):
        out, _ = self.run_blocks([f"### security\n{CRITICAL}"], ["VERDICT: UNVERIFIABLE\nREASON: depends on prod config."])
        self.assertNotIn("🔴", out[0])
        self.assertIn("🟢 **Note** · `app/controllers/insights_controller.rb:5` · Wrong base controller", out[0])
        self.assertIn("could not be confirmed, so this is a Note instead of a Critical: depends on prod config.", out[0])

    def test_a_refuter_that_fails_leaves_the_finding_and_says_so(self):
        out, refuted = self.run_blocks([f"### security\n{CRITICAL}"], [RuntimeError("timed out")])
        self.assertIn("🔴 **Critical**", out[0])
        self.assertIn("not checked (timed out)", out[0])
        self.assertEqual(refuted, [])
        out, _ = self.run_blocks([f"### security\n{CRITICAL}"], ["no idea"])
        self.assertIn("did not follow the required format", out[0])

    def test_only_what_needs_checking_is_sent_to_the_refuter(self):
        block = f"### correctness\n{CRITICAL}\n{WARNING_NO_EVIDENCE}\n{NOTE}"
        self.run_blocks([block], "VERDICT: CONFIRMED\nREASON: ok")
        self.assertEqual(sorted(self.asked), ["refute:correctness:1", "refute:correctness:2"])   # the Critical and the unevidenced Warning

    def test_other_findings_in_the_block_are_untouched(self):
        block = f"### correctness\n{CRITICAL}\n{NOTE}"
        out, _ = self.run_blocks([block], ["VERDICT: REFUTED\nREASON: wrong."])
        self.assertIn("Magic number", out[0])
        self.assertNotIn("No issues remain", out[0])

    def test_dimensions_without_code_evidence_are_skipped(self):
        out, _ = self.run_blocks([f"### testing_plan\n{CRITICAL}", f"### frontend\n{CRITICAL}"], "VERDICT: REFUTED\nREASON: x")
        self.assertEqual(self.asked, [])
        self.assertIn("🔴", out[0] + out[1])

    def test_failure_findings_made_by_migite_are_never_refuted(self):
        block = "### security\n- 🔴 **Critical** · (none) · the security reviewer failed\n  - **Problem:** timeout"
        out, _ = self.run_blocks([block], "VERDICT: REFUTED\nREASON: x")
        self.assertEqual(self.asked, [])
        self.assertEqual(out, [block])

    def test_a_block_with_nothing_to_check_comes_back_as_it_was(self):
        out, refuted = self.run_blocks(["### security\n✅ No issues in security."], "unused")
        self.assertEqual(out, ["### security\n✅ No issues in security."])
        self.assertEqual(refuted, [])

    def test_the_cap_checks_criticals_first_and_marks_the_rest(self):
        warnings = "\n".join(WARNING_NO_EVIDENCE.replace("Unscoped lookup", f"Warn {i}") for i in range(verify.MAX_REFUTATIONS))
        out, _ = self.run_blocks([f"### a\n{warnings}", f"### b\n{CRITICAL}"], "VERDICT: CONFIRMED\nREASON: ok")
        self.assertEqual(len(self.asked), verify.MAX_REFUTATIONS)
        self.assertIn("refute:b:1", self.asked)                                  # the Critical made the cut
        self.assertIn("over the limit of", out[0])                               # a Warning did not


class AppendixTest(unittest.TestCase):
    def test_lists_refuted_findings_without_severity_marks(self):
        doc = verify.append_refuted("# Review\n\n## Verdict\nAPPROVED\n", [
            {"dimension": "correctness", "severity": "critical", "title": "Wrong base", "location": "a.rb:5",
             "reason": "resolves lexically", "evidence": "`module Chat`"}])
        self.assertIn("## Refuted by verification", doc)
        self.assertIn("**Wrong base** (was Critical, `a.rb:5`, correctness): resolves lexically", doc)
        for mark in "🔴🟡🟢":
            self.assertNotIn(mark, doc)
        self.assertTrue(doc.startswith("# Review"))

    def test_nothing_refuted_leaves_the_document_alone(self):
        self.assertEqual(verify.append_refuted("# Review\n", []), "# Review\n")


if __name__ == "__main__":
    unittest.main()
