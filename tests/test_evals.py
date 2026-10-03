#!/usr/bin/env python3
"""tests/test_evals.py - the plumbing of evals/ (the promptfoo setup that calibrates the
refuter): reading findings out of past reports, grading a verdict against a label, and
turning labeled findings into tests. No model is called, and nothing needs promptfoo or node.
Run: python3 -m unittest tests/test_evals.py"""

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "evals"))

HAS_YAML = importlib.util.find_spec("yaml") is not None
if HAS_YAML:
    import common  # noqa: E402
    import extract_candidates  # noqa: E402
    import tests_from_golden  # noqa: E402

from migite import verify  # noqa: E402


def load(path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


HEADING_REPORT = """# PR Review: bb-1
## Findings
### Critical
#### 🔴 1. Wrong base controller breaks `authorize` · `app/controllers/a.rb:7`
**Problem:** inherits from the bare ApplicationController,
so authorize raises.
**Fix:** inherit from the Chat one.
### Notes
#### 🟢 2. Magic number · `app/models/b.rb:4`
**Problem:** unexplained.
**Fix:** a constant.
"""

BULLET_REPORT = """# PR Review: bb-2
### Warnings
- 🟡 `app/jobs/j.rb:66` \u2014 `backfill` interpolates a constant into raw SQL \u2014 build it with sanitize_sql_array.
- 🟡 `app/models/x.rb:44`, `app/models/y.rb:3` - the new branch is untested - add a spec.
- 🟢 Tooling \u2014 rubocop could not run.
"""


@unittest.skipUnless(HAS_YAML, "PyYAML not installed")
class ExtractTest(unittest.TestCase):
    def test_heading_format_gives_title_location_and_the_problem_without_the_fix(self):
        found = extract_candidates.parse_review(HEADING_REPORT)
        self.assertEqual(len(found), 1)                                    # the Note is not calibration material
        self.assertEqual(found[0]["severity"], "critical")
        self.assertEqual(found[0]["location"], "app/controllers/a.rb:7")
        self.assertIn("authorize", found[0]["title"])
        self.assertEqual(found[0]["problem"], "inherits from the bare ApplicationController, so authorize raises.")

    def test_one_line_bullets_split_the_problem_from_the_fix(self):
        found = extract_candidates.parse_review(BULLET_REPORT)
        self.assertEqual([f["location"] for f in found], ["app/jobs/j.rb:66", "app/models/x.rb:44"])
        self.assertEqual(found[0]["problem"], "`backfill` interpolates a constant into raw SQL")
        self.assertNotIn("sanitize_sql_array", found[0]["problem"])

    def test_a_report_with_no_findings_gives_none(self):
        self.assertEqual(extract_candidates.parse_review("# PR Review: x\n## Verdict\nAPPROVED\n"), [])

    def test_the_review_time_is_exact_when_the_file_name_has_one(self):
        when, exact = extract_candidates.review_time(Path("pr-review-x-20261001-151652.md"), "Date: 2026-10-01")
        self.assertEqual((when.hour, when.minute, exact), (15, 16, True))
        when, exact = extract_candidates.review_time(Path("pr-review-x-2026-10-01.md"), "Date: 2026-10-01")
        self.assertEqual((when.hour, exact), (0, False))


@unittest.skipUnless(HAS_YAML, "PyYAML not installed")
class GoldenTest(unittest.TestCase):
    def test_locations_split_into_path_and_line(self):
        self.assertEqual(common.split_location("app/a.rb:12"), ("app/a.rb", 12))
        self.assertEqual(common.split_location("app/a.rb:12-20"), ("app/a.rb", 12))
        self.assertEqual(common.split_location("app/a.rb"), ("app/a.rb", None))

    def test_a_golden_entry_becomes_a_finding_the_refuter_prompt_accepts(self):
        entry = {"severity": "critical", "location": "app/a.rb:7", "title": "Wrong base", "problem": "it is wrong"}
        finding = verify.parse_findings(common.finding_block(entry))[0]
        self.assertEqual((finding.severity, finding.title, finding.problem), ("critical", "Wrong base", "it is wrong"))
        self.assertEqual(verify.location_of(finding), ("app/a.rb", 7))
        self.assertIn("it is wrong", verify.refute_prompt(finding, "ok", ""))

    def test_only_labeled_findings_with_a_commit_become_tests(self):
        base = {"repo": "~/r", "severity": "critical", "location": "a.rb:1", "title": "t", "problem": "p"}
        entries = [{**base, "id": "a", "ref": "abc", "label": True}, {**base, "id": "b", "ref": "abc", "label": False},
                   {**base, "id": "c", "ref": "abc", "label": None}, {**base, "id": "d", "ref": None, "label": True}]
        with tempfile.TemporaryDirectory() as tmp:
            old, common.GOLDEN = common.GOLDEN, Path(tmp) / "findings.yaml"
            try:
                common.save_golden(entries)
                tests = tests_from_golden.generate_tests()
            finally:
                common.GOLDEN = old
        self.assertEqual([t["description"] for t in tests], ["a", "b"])
        self.assertEqual([t["vars"]["label"] for t in tests], ["true", "false"])
        self.assertTrue(Path(tests[0]["vars"]["repo"]).is_absolute())


@unittest.skipUnless(HAS_YAML, "PyYAML not installed")
class VariantTest(unittest.TestCase):
    def setUp(self):
        import variants
        self.variants = variants
        self.shipped = verify.HOW_TO_WORK

    def tearDown(self):
        verify.HOW_TO_WORK = self.shipped

    def finding(self):
        return verify.parse_findings("- 🔴 **Critical** · `a.rb:1` · t\n  - **Problem:** p")[0]

    def test_the_default_variant_is_the_prompt_migite_ships(self):
        self.variants.apply(None)
        self.assertIs(verify.HOW_TO_WORK, self.shipped)
        self.assertIn("a spec that covers it", verify.refute_prompt(self.finding(), "ok", ""))

    def test_the_focused_variant_swaps_only_the_how_to_work_block(self):
        self.variants.apply("focused")
        prompt = verify.refute_prompt(self.finding(), "ok", "")
        self.assertIn("Stop as soon as one line you have traced decides the verdict", prompt)
        self.assertNotIn("a spec that covers it", prompt)
        self.assertIn("VERDICT: CONFIRMED | REFUTED | UNVERIFIABLE", prompt)     # the rest of the prompt is untouched

    def test_an_unknown_variant_is_an_error(self):
        with self.assertRaises(SystemExit):
            self.variants.apply("nope")


class GradeTest(unittest.TestCase):
    grade = staticmethod(load(ROOT / "evals" / "assertions" / "verdict.py").grade)

    def test_a_true_finding_must_be_confirmed(self):
        self.assertTrue(self.grade(True, "CONFIRMED")["pass"])
        refuted = self.grade(True, "REFUTED")
        self.assertFalse(refuted["pass"])
        self.assertEqual(refuted["namedScores"]["false_refute"], 1.0)
        self.assertEqual(self.grade(True, "UNVERIFIABLE")["namedScores"]["missed"], 1.0)

    def test_a_wrong_finding_must_be_refuted_and_demoting_it_is_half_right(self):
        self.assertEqual(self.grade(False, "REFUTED")["score"], 1.0)
        demoted = self.grade(False, "UNVERIFIABLE")
        self.assertEqual((demoted["score"], demoted["pass"]), (0.5, True))
        confirmed = self.grade(False, "CONFIRMED")
        self.assertFalse(confirmed["pass"])
        self.assertEqual(confirmed["namedScores"]["false_confirm"], 1.0)

    def test_turns_are_reported_when_the_provider_gives_them(self):
        get_assert = load(ROOT / "evals" / "assertions" / "verdict.py").get_assert
        ctx = {"vars": {"label": "false"}, "providerResponse": {"metadata": {"turns": 14}}}
        self.assertEqual(get_assert("VERDICT: REFUTED", ctx)["namedScores"]["turns"], 14.0)
        self.assertNotIn("turns", get_assert("VERDICT: REFUTED", {"vars": {"label": "false"}})["namedScores"])

    def test_a_reply_in_the_wrong_format_fails_either_way(self):
        verdict_of = load(ROOT / "evals" / "assertions" / "verdict.py").verdict_of
        self.assertEqual(verdict_of("I think it is fine"), "UNPARSEABLE")
        self.assertEqual(verdict_of("VERDICT: refuted\nREASON: x"), "REFUTED")
        self.assertFalse(self.grade(True, "UNPARSEABLE")["pass"])
        self.assertFalse(self.grade(False, "UNPARSEABLE")["pass"])


if __name__ == "__main__":
    unittest.main()
