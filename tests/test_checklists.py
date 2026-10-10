#!/usr/bin/env python3
"""tests/test_checklists.py - migite/checklists.py: the per-stack checklists migite-review,
migite-pr-review and migite-audit read from prompts/checklists/<stack>.md, and prompts.dir
overrides that replace only the sections they contain. No langgraph needed.
Run: python3 -m unittest tests/test_checklists.py"""

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from migite import checklists, verify  # noqa: E402

RAILS_WORDS = ("Rails", "RSpec", "rspec", "Rubocop", "rubocop", "Pundit", "current_user", "ActiveRecord",
               "turbo", "Turbo", "app/models", "has_many", "find_by")


class ShippedChecklistTest(unittest.TestCase):
    def test_rails_has_every_dimension_and_its_seven_audit_areas(self):
        c = checklists.load("rails", ROOT)
        self.assertEqual(c.expertise, "Rails")
        self.assertEqual([d for d, _ in c.review_dimensions()], list(checklists.REVIEW_REQUIRED))
        self.assertIn("frontend", c.review)
        self.assertEqual([d for d, _ in c.pr_review_dimensions()], list(checklists.PR_REVIEW_DIMENSIONS))
        self.assertEqual([a.name for a in c.audit], ["models", "controllers", "services", "serializers",
                                                     "jobs", "migrations", "schema_indexes"])
        self.assertEqual(c.audit[2].globs, ["app/services/**/*.rb", "app/interactors/**/*.rb", "app/commands/**/*.rb"])
        self.assertIsNone(c.refute)  # rails keeps verify.HOW_TO_WORK, the block the evals calibrate

    def test_generic_is_complete_and_says_nothing_rails(self):
        c = checklists.load("generic", ROOT)
        self.assertEqual(c.expertise, "software")
        self.assertNotIn("frontend", c.review)  # the Hotwire reviewer is rails-only
        text = "\n".join([*c.review.values(), *c.pr_review.values(), *(a.checks for a in c.audit), c.refute or ""])
        for word in RAILS_WORDS:
            self.assertNotIn(word, text)
        self.assertIn("How to work:", c.refute or "")

    def test_a_stack_with_no_file_of_its_own_gets_generic(self):
        c = checklists.load("node", ROOT)
        self.assertEqual(c.review, checklists.load("generic", ROOT).review)
        self.assertEqual(c.files, [str(ROOT / "prompts" / "checklists" / "generic.md")])


class OverrideTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        (self.dir / "checklists").mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, stack: str, text: str):
        (self.dir / "checklists" / f"{stack}.md").write_text(text)

    def test_an_override_replaces_only_its_sections(self):
        self.write("rails", "## review: security\nOnly this changes.\n\n## audit: models\nFiles: lib/**/*.rb\n- x\n")
        c, shipped = checklists.load("rails", ROOT, self.dir), checklists.load("rails", ROOT)
        self.assertEqual(c.review["security"], "Only this changes.")
        self.assertEqual(c.review["correctness"], shipped.review["correctness"])
        self.assertEqual(c.pr_review, shipped.pr_review)
        models = next(a for a in c.audit if a.name == "models")
        self.assertEqual((models.globs, models.checks), (["lib/**/*.rb"], "- x"))
        self.assertEqual(len(c.audit), 7)  # same area replaced in place, none added
        self.assertEqual(c.expertise, "Rails")
        self.assertEqual(c.files[-1], str(self.dir / "checklists" / "rails.md"))

    def test_a_profile_gets_its_own_checks_on_top_of_generic(self):
        self.write("node", "Expertise: Node.js\n\n## pr_review: security\n- Every route checks the session\n\n"
                           "## audit: handlers\nFiles: src/routes/**/*.ts\n- Unvalidated req.body\n\n## refute\nTrace it.\n")
        c = checklists.load("node", ROOT, self.dir)
        self.assertEqual(c.expertise, "Node.js")
        self.assertEqual(c.pr_review["security"], "- Every route checks the session")
        self.assertEqual(c.review, checklists.load("generic", ROOT).review)
        self.assertEqual(c.audit[-1].name, "handlers")
        self.assertEqual(c.refute, "Trace it.")

    def test_no_override_dir_or_file_is_the_shipped_checklist(self):
        self.assertEqual(checklists.load("rails", ROOT, self.dir).files, checklists.load("rails", ROOT).files)
        self.assertEqual(checklists.load("rails", ROOT, None).files, checklists.load("rails", ROOT).files)


class FormatErrorTest(unittest.TestCase):
    def parse_error(self, text: str) -> str:
        with self.assertRaises(checklists.ChecklistError) as cm:
            checklists.parse(text, "x.md")
        return str(cm.exception)

    def test_dimensions_are_fixed_because_each_is_a_model_role(self):
        msg = self.parse_error("## review: style\ntext\n")
        self.assertIn("x.md:1", msg)
        self.assertIn("model role", msg)
        self.assertIn("pr_review", self.parse_error("## pr_review: frontend\ntext\n"))

    def test_unknown_or_unnamed_sections_and_repeats_are_errors(self):
        self.assertIn("unknown section", self.parse_error("## notes: x\n"))
        self.assertIn("needs a name", self.parse_error("## audit\n"))
        self.assertIn("takes no name", self.parse_error("## refute: x\n"))
        self.assertIn("twice", self.parse_error("## review: security\na\n## review: security\nb\n"))

    def test_an_audit_area_needs_its_files_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "checklists").mkdir()
            (Path(tmp) / "checklists" / "rails.md").write_text("## audit: models\n- no files line\n")
            with self.assertRaises(checklists.ChecklistError) as cm:
                checklists.load("rails", ROOT, tmp)
            self.assertIn("Files:", str(cm.exception))

    def test_text_before_the_first_section_is_ignored_except_expertise(self):
        expertise, sections = checklists.parse("# Title\n<!-- a comment -->\nExpertise: Go\n\n## refute\nx\n", "x.md")
        self.assertEqual((expertise, sections), ("Go", {("refute", ""): "x"}))


class HelpersTest(unittest.TestCase):
    def test_log_labels_name_rails_tools_only_on_rails(self):
        self.assertEqual(checklists.log_labels("rails"), ("Rubocop", "RSpec"))
        self.assertEqual(checklists.log_labels("node"), ("Lint", "Test"))

    def test_rails_keeps_its_ruby_fence_other_stacks_get_the_files_language(self):
        self.assertEqual(checklists.fence("rails", "app/x.js"), "ruby")
        self.assertEqual(checklists.fence("node", "src/x.ts"), "typescript")
        self.assertEqual(checklists.fence("generic", "README"), "")

    def test_the_refuter_takes_the_checklists_expertise_and_how_to_work(self):
        finding = verify.parse_findings("- 🔴 **Critical** · `src/a.ts:3` · leak\n  - **Problem:** p\n")[0]
        rails = verify.refute_prompt(finding, "ok", "")
        self.assertIn("skeptical senior Rails engineer", rails)
        self.assertIn(verify.HOW_TO_WORK, rails)
        node = verify.refute_prompt(finding, "ok", "", expertise="Node.js", how_to_work="How to work: trace it.")
        self.assertIn("skeptical senior Node.js engineer", node)
        self.assertIn("How to work: trace it.", node)
        self.assertNotIn("Ruby and Rails", node)


if __name__ == "__main__":
    unittest.main()
