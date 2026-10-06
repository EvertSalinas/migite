#!/usr/bin/env python3
"""tests/test_keywords.py - migite.keywords, the task words migite-plan's
explorers rank files by and migite.knowledge ranks knowledge.md entries by.
Moved out of migite/tools/plan.py unchanged; these pin what both rely on.
Run: python3 -m unittest tests/test_keywords.py"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from migite.keywords import extract_keywords  # noqa: E402


class ExtractKeywordsTest(unittest.TestCase):
    def test_words_of_four_or_more_letters_lowercased_without_repeats(self):
        self.assertEqual(extract_keywords("Retry the SMS delivery, retry it twice"),
                         {"retry", "delivery", "twice"})

    def test_filler_and_rails_vocabulary_are_not_keywords(self):
        self.assertEqual(extract_keywords("This controller method should return the model"), set())

    def test_snake_case_identifiers_and_words_with_digits_are_not_split_into_keywords(self):
        self.assertEqual(extract_keywords("chat_thread_members oauth2 bb-3752 threads"), {"threads"})


if __name__ == "__main__":
    unittest.main()
