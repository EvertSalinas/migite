"""migite.keywords - the words of a task worth matching against other text.

migite-plan's explorers rank the files of an area by these words, and
migite.knowledge ranks knowledge.md entries by them. Words of four or more
letters, lowercased, minus common English and Rails filler. No langgraph, so
bash-side callers (python -m migite.knowledge) can import it too.
"""

import re

STOP_WORDS = {
    "this", "that", "with", "from", "have", "been", "will", "when", "where", "what",
    "which", "their", "there", "they", "also", "into", "more", "some", "than", "then",
    "type", "name", "each", "such", "well", "just", "only", "should", "would", "could",
    "these", "those", "about", "after", "before", "other", "first", "class", "return",
    "method", "endpoint", "rails", "ruby", "model", "controller", "service",
}


def extract_keywords(text: str) -> set[str]:
    words = re.findall(r"\b[A-Za-z][a-zA-Z]{3,}\b", text)
    return {w.lower() for w in words} - STOP_WORDS
