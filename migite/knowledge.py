#!/usr/bin/env python3
"""knowledge - the part of a repo's knowledge.md worth putting in a prompt.

knowledge.md grows by an entry every run (lib/phases/deliver.sh) and was
injected whole into the plan, implement, fix and amend prompts: 24 KB and
counting for an active repo. Entries are dated sections, appended oldest
first, so the old `knowledge[:800]` excerpt gave the planner's explorers the
file header plus the oldest lessons. `recent` returns the newest entries
first, up to a byte budget, and says how many older ones it left out.
`relevant` fills the same budget with the entries that share the most words
with the task first (knowledge.select: relevant), still printed newest first.

  python -m migite.knowledge recent --file knowledge.md --max-bytes 8000
  python -m migite.knowledge relevant --file knowledge.md --max-bytes 8000 \\
         [--keywords "text or words"] [--keywords-file intake.md] [--keywords-file jira-context.md]
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Iterable
from pathlib import Path

from migite.keywords import extract_keywords

# An entry starts at a "## 2026-04-14 — bb-3013" heading (### in some older entries).
ENTRY_RE = re.compile(r"^#{2,3} \d{4}-\d{2}-\d{2}\b", re.MULTILINE)

# What an intake template or a fetched ticket puts there, not the person who wrote
# the task: front matter, <!-- --> hints, links, headings, the Type line the template
# pre-fills, **Label:** markers (their values stay) and labels left empty. A --jira
# run's intake is often the bare template, which then gives no keywords at all.
TASK_SCAFFOLDING = (
    re.compile(r"\A---\n.*?\n---[ \t]*(?:\n|\Z)", re.DOTALL),
    re.compile(r"<!--.*?-->", re.DOTALL),
    re.compile(r"https?://\S+"),
    re.compile(r"^#.*$", re.MULTILINE),
    re.compile(r"^\*\*Type:\*\*.*$", re.MULTILINE),
    re.compile(r"\*\*[^*\n]+?:\*\*"),
    re.compile(r"^[ \t]*[A-Za-z][\w ()/-]*:[ \t]*$", re.MULTILINE),
)
# Every entry links its task's review: [[dev-log/<org>/<repo>/<task>/00-build/review]].
WIKILINK_RE = re.compile(r"\[\[[^\]]*\]\]")


def entries(text: str) -> list[str]:
    """The dated entries in file order (oldest first), each with its heading."""
    starts = [m.start() for m in ENTRY_RE.finditer(text)]
    return [text[a:b].strip() for a, b in zip(starts, starts[1:] + [len(text)]) if text[a:b].strip()]


def recent(text: str, max_bytes: int, source: str = "knowledge.md") -> str:
    """The newest entries first, as many as fit in max_bytes (at least the newest
    one, cut to fit). Empty when the file has no entries yet."""
    found = entries(text)
    if not found:
        return ""
    picked: list[str] = []
    used = 0
    for entry in reversed(found):
        size = len(entry.encode()) + 2
        if picked and used + size > max_bytes:
            break
        picked.append(entry if picked or size <= max_bytes else entry.encode()[:max_bytes].decode(errors="ignore"))
        used += size
    out = "\n\n".join(picked)
    left_out = len(found) - len(picked)
    if left_out:
        out += f"\n\n({left_out} older entr{'y' if left_out == 1 else 'ies'} not shown; all of them are in {source})"
    return out


def task_keywords(text: str) -> set[str]:
    """The words of an intake or a ticket that its writer put there (TASK_SCAFFOLDING
    removed), as migite.keywords extracts them. One document per call: front matter
    is only recognised at the start."""
    for pattern in TASK_SCAFFOLDING:
        text = pattern.sub("", text)
    return extract_keywords(text)


def entry_score(entry: str, keywords: set[str]) -> int:
    """How many of the keywords the entry's lessons use. Its dated heading and its
    wikilinks don't count: they name the task and the repo, not the lesson."""
    body = entry.split("\n", 1)[1] if "\n" in entry else ""
    return len(extract_keywords(WIKILINK_RE.sub("", body)) & keywords)


def relevant(text: str, keywords: Iterable[str], max_bytes: int, source: str = "knowledge.md") -> str:
    """The entries that share the most keywords with the task, as many as fit in
    max_bytes (at least the best one, cut to fit), printed newest first. Ties go to
    the newer entry, and an entry too big for the room left is skipped so a smaller
    one can use it. With no keywords, or none that any entry shares, this is recent()."""
    found = entries(text)
    wanted = {k.lower() for k in keywords}
    scores = [entry_score(entry, wanted) for entry in found] if wanted else []
    if not any(scores):
        return recent(text, max_bytes, source)
    picked: dict[int, str] = {}
    used = 0
    for i in sorted(range(len(found)), key=lambda i: (-scores[i], -i)):
        size = len(found[i].encode()) + 2
        if picked and used + size > max_bytes:
            continue
        picked[i] = found[i] if picked or size <= max_bytes else found[i].encode()[:max_bytes].decode(errors="ignore")
        used += size
    out = "\n\n".join(picked[i] for i in sorted(picked, reverse=True))
    left_out = len(found) - len(picked)
    if left_out:
        out += f"\n\n({left_out} other entr{'y' if left_out == 1 else 'ies'} not shown; all of them are in {source})"
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="knowledge: the part of knowledge.md for a prompt")
    sub = ap.add_subparsers(dest="command", required=True)
    r = sub.add_parser("recent", help="newest entries first, up to a byte budget")
    r.add_argument("--file", required=True)
    r.add_argument("--max-bytes", type=int, default=8000)
    rel = sub.add_parser("relevant", help="entries sharing the most words with the task, up to a byte budget")
    rel.add_argument("--file", required=True)
    rel.add_argument("--max-bytes", type=int, default=8000)
    rel.add_argument("--keywords", action="append", default=[],
                     help="the task: a list of words or free text (repeatable)")
    rel.add_argument("--keywords-file", action="append", default=[],
                     help="a file holding the task, e.g. intake.md or jira-context.md; missing files are skipped (repeatable)")
    args = ap.parse_args()
    path = Path(args.file)
    text = path.read_text(errors="replace") if path.is_file() else ""
    if args.command == "recent":
        sys.stdout.write(recent(text, args.max_bytes, source=str(path)))
        return
    task = list(args.keywords) + [Path(f).read_text(errors="replace") for f in args.keywords_file if Path(f).is_file()]
    keywords = set().union(*(task_keywords(t) for t in task))
    sys.stdout.write(relevant(text, keywords, args.max_bytes, source=str(path)))


if __name__ == "__main__":
    main()
