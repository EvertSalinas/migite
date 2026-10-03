#!/usr/bin/env python3
"""Pull the Critical and Warning findings out of past migite-pr-review reports into
golden/findings.yaml, ready to label (label.py). Re-running adds new findings and keeps
every label and edit already in the file.

    python extract_candidates.py [--reviews DIR] [--code-root ~/Code]

A report at <vault>/<org>/<repo>/.../pr-review-*.md is matched to the checkout at
<code-root>/<org>/<repo>. `ref` is the branch's commit at the time of the review: exact when
the file name carries a time, otherwise the start of the review's day (`ref_approximate`),
so check the code excerpt label.py shows before trusting a label.
"""

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import common  # noqa: E402

SEVERITY = {"🔴": "critical", "🟡": "warning"}   # Notes are not worth calibrating a judge on
HEADING_RE = re.compile(r"^#{3,4}\s*(?P<mark>[🔴🟡🟢])\s*(?:\d+\.\s*)?(?P<title>.*)\s·\s`(?P<loc>[^`]+)`\s*$")
BULLET_RE = re.compile(r"^[-*]\s*(?P<mark>[🔴🟡🟢])\s*(?P<spans>(?:`[^`]+`(?:,\s*)?)+)\s*[\u2014-]\s*(?P<text>.+)$")
FIELD_RE = re.compile(r"^\*\*(?P<name>[A-Za-z ]+):\*\*\s*(?P<val>.*)$")
TIME_RE = re.compile(r"-(\d{8})-(\d{6})\.md$")


def _problem_of(text: str) -> tuple[str, str]:
    """(title, problem) from an older one-line finding: 'problem - fix', the fix is dropped."""
    problem = re.split(r"\s[\u2014]\s", text)[0].strip()
    title = re.sub(r"\s+", " ", problem)
    return (title[:80].rstrip(" .,;:") + ("..." if len(title) > 80 else ""), problem)


def parse_review(text: str) -> list[dict]:
    """Critical and Warning findings of one report, in order, in either report format:
    `#### 🟡 1. title · `path:line`` blocks with a **Problem:** field, or one-line
    `- 🟡 `path:line` - problem - fix` bullets."""
    found, lines, i = [], text.splitlines(), 0
    while i < len(lines):
        line = lines[i].strip()
        head = HEADING_RE.match(line)
        bullet = BULLET_RE.match(line)
        if head and head["mark"] in SEVERITY:
            problem, grabbing, j = [], False, i + 1
            while j < len(lines) and not re.match(r"^#{2,4}\s", lines[j]):
                m = FIELD_RE.match(lines[j].strip())
                if m:
                    grabbing = m["name"].strip().lower() == "problem"
                    if grabbing:
                        problem.append(m["val"])
                elif grabbing and lines[j].strip():
                    problem.append(lines[j].strip())
                j += 1
            found.append({"severity": SEVERITY[head["mark"]], "location": head["loc"].strip(),
                          "title": head["title"].strip(), "problem": " ".join(problem).strip()})
            i = j
            continue
        if bullet and bullet["mark"] in SEVERITY:
            location = re.findall(r"`([^`]+)`", bullet["spans"])[0]
            title, problem = _problem_of(bullet["text"])
            found.append({"severity": SEVERITY[bullet["mark"]], "location": location, "title": title, "problem": problem})
        i += 1
    return [f for f in found if f["problem"] and "(none)" not in f["location"]]


def review_time(path: Path, text: str) -> tuple[datetime | None, bool]:
    """(when the review ran, exact?). The file name carries a time on newer reports; older ones only the Date line."""
    m = TIME_RE.search(path.name)
    if m:
        return datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S"), True
    d = re.search(r"^Date:\s*(\d{4}-\d{2}-\d{2})", text, re.M)
    return (datetime.strptime(d.group(1), "%Y-%m-%d"), False) if d else (None, False)


def locate_branch(repo: Path, branch: str) -> str:
    for candidate in (branch, f"origin/{branch}"):
        if common.git(repo, "rev-parse", "--verify", "-q", f"{candidate}^{{commit}}"):
            return candidate
    return ""


def entries_for(path: Path, vault: Path, code_root: Path) -> list[dict]:
    text = path.read_text()
    parsed = parse_review(text)
    if not parsed:
        return []
    rel = path.relative_to(vault)
    org, repo_name = rel.parts[0], rel.parts[1]
    repo = code_root / org / repo_name
    branch_m = re.search(r"^# PR Review:\s*(.+)$", text, re.M)
    branch = branch_m.group(1).strip() if branch_m else ""
    when, exact = review_time(path, text)
    ref, tip = "", ""
    if repo.is_dir() and branch and when:
        tip = locate_branch(repo, branch)
        if tip:
            ref = common.git(repo, "rev-list", "-1", f"--before={when:%Y-%m-%d %H:%M:%S}", tip)
    out = []
    for n, f in enumerate(parsed, 1):
        file_path, _ = common.split_location(f["location"])
        later = common.git(repo, "rev-list", "--count", f"--after={when:%Y-%m-%d %H:%M:%S}", tip, "--", file_path) if tip and when else ""
        out.append({
            "id": f"{path.stem}#{n}",
            "repo": f"~/{repo.relative_to(Path.home())}" if repo.is_relative_to(Path.home()) else str(repo),
            "ref": ref or None,
            "ref_approximate": not exact,
            "severity": f["severity"],
            "location": f["location"],
            "title": f["title"],
            "problem": f["problem"],
            "source": str(rel),
            "hint": {"branch": branch, "commits_touching_the_file_after_the_review": int(later) if later.isdigit() else None},
            "label": None,
        })
    return out


def main() -> None:
    from migite import config
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reviews", help="Directory holding the reports (default: the vault.base from your migite config)")
    ap.add_argument("--code-root", default=str(Path.home() / "Code"), help="Where the repos are checked out (default: ~/Code)")
    args = ap.parse_args()

    vault = Path(args.reviews).expanduser() if args.reviews else config.load(".").expanded_path("vault.base")
    if vault is None or not vault.is_dir():
        sys.exit(f"  no reports directory: {vault}; pass --reviews")
    code_root = Path(args.code_root).expanduser()
    existing = {e["id"]: e for e in common.load_golden()}
    added = 0
    for path in sorted(vault.rglob("pr-review-*.md")):
        try:
            entries = entries_for(path, vault, code_root)
        except (OSError, ValueError, IndexError) as e:
            print(f"  skipped {path.name}: {e}", file=sys.stderr)
            continue
        for e in entries:
            if e["id"] not in existing:
                existing[e["id"]] = e
                added += 1
    common.save_golden(list(existing.values()))
    todo = [e for e in existing.values() if e["label"] is None]
    usable = [e for e in todo if e["ref"]]
    print(f"  {len(existing)} findings in {common.GOLDEN} ({added} new); {len(todo)} unlabeled, "
          f"{len(usable)} of them have a commit to check them against")


if __name__ == "__main__":
    main()
