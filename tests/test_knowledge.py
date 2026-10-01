#!/usr/bin/env python3
"""tests/test_knowledge.py - migite.knowledge puts the newest knowledge.md
entries into prompts, up to a byte budget.

knowledge.md grows by an entry every run and used to go into prompts whole;
the explorers' 800-character excerpt was the file header and the oldest
lessons. Run: python3 -m unittest tests/test_knowledge.py"""

import sys
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


if __name__ == "__main__":
    unittest.main()
