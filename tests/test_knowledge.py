#!/usr/bin/env python3
"""tests/test_knowledge.py - migite.knowledge puts knowledge.md entries into
prompts, up to a byte budget: the newest first (recent), or the ones that share
the most words with the task first (relevant), still printed newest first.

knowledge.md grows by an entry every run and used to go into prompts whole;
the explorers' 800-character excerpt was the file header and the oldest
lessons. Run: python3 -m unittest tests/test_knowledge.py"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from migite import knowledge  # noqa: E402

FILE = """---
created: 2026-05-19
---

# Knowledge — communications-backend

> One file per repo.

## 2026-03-31 — BB-2539
- oldest lesson

### 2026-04-22 -- BB-2940
- middle lesson, older heading style

## 2026-09-29 — bb-3693
- newest lesson
"""


class RecentTest(unittest.TestCase):
    def test_entries_are_found_under_both_heading_styles_without_the_preamble(self):
        found = knowledge.entries(FILE)
        self.assertEqual(len(found), 3)
        self.assertTrue(found[0].startswith("## 2026-03-31"))
        self.assertNotIn("One file per repo", "".join(found))

    def test_the_newest_entry_comes_first(self):
        out = knowledge.recent(FILE, 8000)
        self.assertLess(out.index("newest lesson"), out.index("middle lesson"))
        self.assertLess(out.index("middle lesson"), out.index("oldest lesson"))
        self.assertNotIn("not shown", out)

    def test_the_budget_drops_the_oldest_entries_and_says_where_they_are(self):
        out = knowledge.recent(FILE, 60, source="kb.md")
        self.assertIn("newest lesson", out)
        self.assertNotIn("oldest lesson", out)
        self.assertIn("older entries not shown; all of them are in kb.md", out)

    def test_an_oversized_newest_entry_is_cut_to_fit_rather_than_dropped(self):
        big = "## 2026-09-30 — x\n" + "- " + "a" * 500 + "\n"
        out = knowledge.recent(big, 100)
        self.assertTrue(out.startswith("## 2026-09-30"))
        self.assertLessEqual(len(out.encode()), 100)

    def test_a_file_without_entries_gives_nothing(self):
        self.assertEqual(knowledge.recent("# Knowledge\n\n> header only\n", 8000), "")


def entry(day: int, slug: str, lesson: str) -> str:
    return f"## 2026-{day // 28 + 1:02d}-{day % 28 + 1:02d} — {slug}\n- {lesson} [[dev-log/Apptegy/repo/{slug}/00-build/review]]\n\n"


# One old lesson about the task (Twilio SMS retries), then 40 newer ones about
# something else, about 13 KB: the newest 8 KB leaves the old lesson out.
OLD_MATCH = "Twilio delivery callbacks arrive twice when a retried send succeeds; dedupe on the message SID"
FILLER = ("Pagination cursors in the admin dashboard must be opaque strings, never raw ids, "
          "and the serializer owns their encoding so controllers stay thin")
GROWN = "# Knowledge — repo\n\n" + entry(0, "bb-1000", OLD_MATCH) + "".join(
    entry(i, f"bb-{1000 + i}", f"{FILLER} (lesson {i:02d})") for i in range(1, 41))
TASK = "**Title:** Retry failed Twilio SMS deliveries\n**Context:** a retried send can run its delivery callback twice\n"


class RelevantTest(unittest.TestCase):
    def setUp(self):
        self.keywords = knowledge.task_keywords(TASK)

    def test_an_old_entry_that_matches_the_task_is_kept_where_the_newest_8kb_would_drop_it(self):
        self.assertNotIn(OLD_MATCH, knowledge.recent(GROWN, 8000))
        out = knowledge.relevant(GROWN, self.keywords, 8000)
        self.assertIn(OLD_MATCH, out)
        self.assertIn("(lesson 40)", out)
        self.assertLessEqual(len(out.split("\n\n(")[0].encode()), 8000)

    def test_the_picked_entries_are_printed_newest_first_whatever_their_score(self):
        out = knowledge.relevant(GROWN, self.keywords, 8000)
        self.assertLess(out.index("(lesson 40)"), out.index("(lesson 39)"))
        self.assertLess(out.index("(lesson 30)"), out.index(OLD_MATCH))

    def test_ties_go_to_the_newer_entry(self):
        text = entry(1, "a", "twilio lesson one") + entry(2, "b", "twilio lesson two") + entry(3, "c", "unrelated")
        out = knowledge.relevant(text, {"twilio"}, 100)  # room for one whole entry
        self.assertIn("lesson two", out)
        self.assertNotIn("lesson one", out)

    def test_an_entry_too_big_for_the_room_left_is_skipped_for_a_smaller_one(self):
        text = (entry(1, "small", "twilio small lesson")
                + entry(2, "big", "twilio delivery " + "padding " * 40)
                + entry(3, "best", "twilio delivery callbacks"))
        out = knowledge.relevant(text, {"twilio", "delivery", "callbacks"}, 300)
        self.assertIn("callbacks", out)
        self.assertNotIn("padding", out)
        self.assertIn("small lesson", out)

    def test_the_footer_counts_the_other_entries_and_names_the_file(self):
        out = knowledge.relevant(GROWN, self.keywords, 8000, source="kb.md")
        shown = sum(1 for e in knowledge.entries(GROWN) if e in out)
        self.assertIn(f"({41 - shown} other entries not shown; all of them are in kb.md)", out)

    def test_a_match_only_in_the_heading_or_a_wikilink_does_not_count(self):
        text = (entry(1, "twilio-retries", "an old lesson about something else")
                + "## 2026-01-03 — x\n- see [[dev-log/twilio/review]]\n\n"
                + entry(4, "newest", "the newest lesson"))
        self.assertEqual(knowledge.entry_score(knowledge.entries(text)[0], {"twilio", "retries"}), 0)
        self.assertEqual(knowledge.entry_score(knowledge.entries(text)[1], {"twilio"}), 0)
        self.assertEqual(knowledge.relevant(text, {"twilio"}, 40), knowledge.recent(text, 40))

    def test_no_keywords_is_recent_byte_for_byte(self):
        for text in (FILE, GROWN):
            for budget in (40, 60, 800, 8000, 100_000):
                for none in (set(), [], ()):
                    self.assertEqual(knowledge.relevant(text, none, budget, source="kb.md"),
                                     knowledge.recent(text, budget, source="kb.md"))

    def test_keywords_no_entry_shares_are_recent_too(self):
        self.assertEqual(knowledge.relevant(GROWN, {"kubernetes"}, 8000), knowledge.recent(GROWN, 8000))

    def test_keywords_match_whatever_their_case(self):
        self.assertIn(OLD_MATCH, knowledge.relevant(GROWN, {"Twilio", "SID"}, 8000))

    def test_the_best_entry_is_cut_to_fit_rather_than_dropped(self):
        big = entry(1, "x", "twilio " + "a" * 500) + entry(2, "y", "newer and unrelated")
        out = knowledge.relevant(big, {"twilio"}, 100)
        self.assertIn("twilio", out)
        self.assertLessEqual(len(out.split("\n\n(")[0].encode()), 100)

    def test_a_file_without_entries_gives_nothing(self):
        self.assertEqual(knowledge.relevant("# Knowledge\n\n> header only\n", {"twilio"}, 8000), "")


INTAKE_TEMPLATES = ("bug", "config", "feature", "refactor", "spike")


class TaskKeywordsTest(unittest.TestCase):
    def test_every_bare_intake_template_gives_no_keywords(self):
        for name in INTAKE_TEMPLATES:
            text = (ROOT / "templates" / f"{name}.md").read_text()
            self.assertEqual(knowledge.task_keywords(text), set(), name)

    def test_a_filled_intake_keeps_what_was_written_and_drops_labels_hints_and_links(self):
        text = ("---\ncreated: 2026-10-06\n---\n\n# Task intake\n\n**Title:** Retry failed Twilio deliveries\n"
                "**Type:** feature\n**Jira:** https://apptegy.atlassian.net/browse/BB-1\n"
                "**Context:** <!-- why does this need to happen? -->\nCallbacks arrive twice.\nEndpoint:\n")
        self.assertEqual(knowledge.task_keywords(text),
                         {"retry", "failed", "twilio", "deliveries", "callbacks", "arrive", "twice"})

    def test_a_list_of_words_passes_through(self):
        self.assertEqual(knowledge.task_keywords("twilio sms callbacks"), {"twilio", "callbacks"})


def run_cli(*args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONPATH=str(ROOT))
    return subprocess.run([sys.executable, "-m", "migite.knowledge", *args],
                          capture_output=True, text=True, env=env)


class CliTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.kb = self.dir / "knowledge.md"
        self.kb.write_text(GROWN)

    def test_relevant_with_keywords_picks_the_old_match(self):
        out = run_cli("relevant", "--file", str(self.kb), "--keywords", "twilio callbacks")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn(OLD_MATCH, out.stdout)
        self.assertIn(f"other entries not shown; all of them are in {self.kb}", out.stdout)

    def test_relevant_reads_the_task_from_files_and_skips_missing_ones(self):
        intake = self.dir / "intake.md"
        intake.write_text(TASK)
        out = run_cli("relevant", "--file", str(self.kb), "--keywords-file", str(intake),
                      "--keywords-file", str(self.dir / "jira-context.md"))
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn(OLD_MATCH, out.stdout)

    def test_relevant_without_keywords_prints_what_recent_prints(self):
        rel = run_cli("relevant", "--file", str(self.kb), "--max-bytes", "2000")
        rec = run_cli("recent", "--file", str(self.kb), "--max-bytes", "2000")
        self.assertEqual(rel.returncode, 0, rel.stderr)
        self.assertEqual(rel.stdout, rec.stdout)
        self.assertNotIn(OLD_MATCH, rel.stdout)


if __name__ == "__main__":
    unittest.main()
