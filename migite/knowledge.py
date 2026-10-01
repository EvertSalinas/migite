#!/usr/bin/env python3
"""knowledge - the part of a repo's knowledge.md worth putting in a prompt.

knowledge.md grows by an entry every run (lib/phases/deliver.sh) and was
injected whole into the plan, implement, fix and amend prompts: 24 KB and
counting for an active repo. Entries are dated sections, appended oldest
first, so the old `knowledge[:800]` excerpt gave the planner's explorers the
file header plus the oldest lessons. `recent` returns the newest entries
first, up to a byte budget, and says how many older ones it left out.

  python -m migite.knowledge recent --file knowledge.md --max-bytes 8000
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# An entry starts at a "## 2026-04-14 — bb-3013" heading (### in some older entries).
ENTRY_RE = re.compile(r"^#{2,3} \d{4}-\d{2}-\d{2}\b", re.MULTILINE)


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


def main() -> None:
    ap = argparse.ArgumentParser(description="knowledge: the recent part of knowledge.md for a prompt")
    sub = ap.add_subparsers(dest="command", required=True)
    r = sub.add_parser("recent", help="newest entries first, up to a byte budget")
    r.add_argument("--file", required=True)
    r.add_argument("--max-bytes", type=int, default=8000)
    args = ap.parse_args()
    path = Path(args.file)
    text = path.read_text(errors="replace") if path.is_file() else ""
    sys.stdout.write(recent(text, args.max_bytes, source=str(path)))


if __name__ == "__main__":
    main()
