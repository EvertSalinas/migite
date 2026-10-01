#!/usr/bin/env python3
"""tests/test_review_rerun.py - a re-review after a fix round runs only what it
needs to: correctness always, every dimension whose last output had findings
(or failed), and the testing-plan dimension when the testing plan changed.
Clean dimensions carry their result into the verdict, marked as not re-run.
Every re-review used to run all four reviewers and the verdict again.
Skipped where langgraph isn't installed (the tool imports it at module load).
Run: python3 -m unittest tests/test_review_rerun.py"""

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

HAS_LANGGRAPH = importlib.util.find_spec("langgraph") is not None
CLEAN = "✅ No issues in this dimension."


def load_tool():
    spec = importlib.util.spec_from_file_location("review_rerun_under_test", ROOT / "migite" / "tools" / "review.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@unittest.skipUnless(HAS_LANGGRAPH, "langgraph not installed")
class DimsToRerunTest(unittest.TestCase):
    def setUp(self):
        self.tool = load_tool()
        self.tp = "# Testing Plan\n1. check it"

    def previous(self, **bodies):
        dims = {"correctness": CLEAN, "security": CLEAN, "test_coverage": CLEAN, "testing_plan": CLEAN}
        dims.update(bodies)
        return {"dimensions": dims, "testing_plan_sha": self.tool._sha(self.tp)}

    def test_all_clean_reruns_only_correctness(self):
        self.assertEqual(self.tool.dims_to_rerun(self.previous(), self.tp), ["correctness"])

    def test_a_dimension_with_any_finding_is_rerun(self):
        prev = self.previous(security="- 🟡 **Warning** — unscoped query", test_coverage="- 🟢 **Note** — tiny")
        self.assertEqual(self.tool.dims_to_rerun(prev, self.tp), ["correctness", "security", "test_coverage"])

    def test_a_failed_reviewer_is_rerun(self):
        prev = self.previous(security="reviewer failed: timeout")
        self.assertIn("security", self.tool.dims_to_rerun(prev, self.tp))

    def test_a_changed_testing_plan_reruns_its_dimension(self):
        self.assertIn("testing_plan", self.tool.dims_to_rerun(self.previous(), self.tp + "\n2. new step"))

    def test_a_dimension_missing_from_the_last_review_is_rerun(self):
        prev = self.previous()
        del prev["dimensions"]["test_coverage"]
        self.assertIn("test_coverage", self.tool.dims_to_rerun(prev, self.tp))

    def test_a_clean_frontend_dimension_is_carried_over(self):
        names = ["correctness", "security", "test_coverage", "testing_plan", "frontend"]
        prev = self.previous(frontend=CLEAN)
        rerun = self.tool.dims_to_rerun(prev, self.tp, names)
        self.assertEqual(rerun, ["correctness"])
        carried = self.tool.carried_findings(prev, rerun, names)
        self.assertEqual(len(carried), 4)
        self.assertTrue(any(block.startswith("### frontend") for block in carried))

    def test_a_frontend_finding_reruns_the_frontend_reviewer(self):
        names = ["correctness", "security", "test_coverage", "testing_plan", "frontend"]
        prev = self.previous(frontend="- 🟡 **Warning** — missing turbo_frame target")
        self.assertIn("frontend", self.tool.dims_to_rerun(prev, self.tp, names))

    def test_a_frontend_dimension_the_last_review_never_ran_is_rerun(self):
        names = ["correctness", "security", "test_coverage", "testing_plan", "frontend"]
        self.assertIn("frontend", self.tool.dims_to_rerun(self.previous(), self.tp, names))

    def test_a_frontend_dimension_no_longer_in_the_diff_is_not_carried(self):
        names = ["correctness", "security", "test_coverage", "testing_plan"]
        prev = self.previous(frontend=CLEAN)
        rerun = self.tool.dims_to_rerun(prev, self.tp, names)
        self.assertNotIn("frontend", rerun)
        self.assertFalse(any(b.startswith("### frontend") for b in self.tool.carried_findings(prev, rerun, names)))


@unittest.skipUnless(HAS_LANGGRAPH, "langgraph not installed")
class FanOutAndRecordTest(unittest.TestCase):
    def setUp(self):
        self.tool = load_tool()

    def state(self, **kw):
        base = {"plan": "", "implementation": "", "rubocop_log": "", "rspec_log": "", "git_diff": "",
                "testing_plan": "tp", "amendments": [], "rerun": ["correctness", "security"]}
        base.update(kw)
        return base

    def test_only_the_rerun_dimensions_are_sent(self):
        sends = self.tool.route_to_reviewers(self.state())
        self.assertEqual([s.arg["dimension"] for s in sends], ["correctness", "security"])

    def test_no_rerun_list_means_a_full_review(self):
        sends = self.tool.route_to_reviewers(self.state(rerun=[]))
        self.assertEqual(len(sends), len(self.tool.REVIEW_DIMENSIONS))

    def test_clean_dimensions_carry_over_marked_once(self):
        prev = {"dimensions": {"correctness": "- 🔴 x", "security": CLEAN,
                               "test_coverage": f"{self.tool.CARRIED_NOTE}\n{CLEAN}"}}
        blocks = self.tool.carried_findings(prev, ["correctness"])
        self.assertEqual([b.splitlines()[0] for b in blocks], ["### security", "### test_coverage"])
        self.assertTrue(all(b.count(self.tool.CARRIED_NOTE) == 1 for b in blocks))

    def test_write_review_records_each_dimension_for_the_next_rerun(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = {"review_output": f"{tmp}/review.md", "sentinel": f"{tmp}/.done", "base_branch": "main",
                     "verdict": "# Review\n## Verdict: READY TO COMMIT\n", "review_meta": {}, "testing_plan": "tp",
                     "findings": ["### correctness\n- 🟡 **Warning** — x", f"### security\n{self.tool.CARRIED_NOTE}\n{CLEAN}"]}
            self.tool.write_review(state)
            dims = json.loads(Path(tmp, "review-dimensions.json").read_text())
        self.assertEqual(dims["dimensions"]["correctness"], "- 🟡 **Warning** — x")
        self.assertEqual(dims["testing_plan_sha"], self.tool._sha("tp"))
        # A carried-over clean dimension stays clean, so it is carried again next time.
        self.assertFalse(self.tool.has_findings(dims["dimensions"]["security"]))


if __name__ == "__main__":
    unittest.main()
