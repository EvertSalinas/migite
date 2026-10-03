#!/usr/bin/env python3
"""Label the findings in golden/findings.yaml: for each unlabeled one, read the claim and the
code it points at, and say whether the finding is a real problem.

    t  true    the finding is right: the code really has this problem
    f  false   the finding is wrong
    s  skip    not sure, or the code shown is not what the reviewer saw (nothing is saved)
    q  quit

Labels are saved after every answer, so you can stop and come back. Label what is true of the
code at that commit, not what the author later chose to do about it.
"""

import argparse
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import common  # noqa: E402


def excerpt(entry: dict, radius: int = 10) -> str:
    path, line = common.split_location(entry["location"])
    text = common.show_file(common.expand(entry["repo"]), entry["ref"], path)
    if text is None:
        return f"  ({path} does not exist at {entry['ref'][:8]}: the finding may not match this commit)"
    rows = text.splitlines()
    lo, hi = (max(0, line - 1 - radius), min(len(rows), line + radius)) if line else (0, min(len(rows), 2 * radius))
    return "\n".join(f"{'>' if line == n else ' '} {n:4} {rows[n - 1]}" for n in range(lo + 1, hi + 1))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", default="", help="Label only findings whose id contains this text")
    args = ap.parse_args()

    entries = common.load_golden()
    todo = [e for e in entries if e.get("label") is None and e.get("ref") and args.only in e["id"]]
    done = sum(1 for e in entries if e.get("label") is not None)
    print(f"  {done} labeled, {len(todo)} to go (q quits, progress is saved)\n")
    for n, e in enumerate(todo, 1):
        print("=" * 100)
        print(f"[{n}/{len(todo)}] {e['id']}   {e['severity'].upper()}   {e['location']}")
        print(f"commit {e['ref'][:8]}{' (approximate: the review only has a date)' if e.get('ref_approximate') else ''}"
              f"   later commits touching the file: {(e.get('hint') or {}).get('commits_touching_the_file_after_the_review')}")
        print(f"\n{e['title']}\n{textwrap.fill(e['problem'], 100)}\n")
        print(excerpt(e))
        while True:
            answer = input("\ntrue / false / skip / quit [t/f/s/q]: ").strip().lower()
            if answer in ("t", "f", "s", "q"):
                break
        if answer == "q":
            break
        if answer in ("t", "f"):
            e["label"] = answer == "t"
            common.save_golden(entries)
    labeled = [e for e in entries if e.get("label") is not None]
    print(f"\n  {len(labeled)} labeled ({sum(1 for e in labeled if e['label'])} true, "
          f"{sum(1 for e in labeled if not e['label'])} false)")


if __name__ == "__main__":
    main()
