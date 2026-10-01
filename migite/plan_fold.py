#!/usr/bin/env python3
"""plan_fold - keep a task's plan.md true to what its runs actually built.

At the end of every run, migite asks the agent (the `plan_fold` role) for the
smallest set of exact text edits that make plan.md describe the design as it now
stands: an amendment's scope, the implementation's recorded deviations, and
fix-round changes that alter the design. The agent never re-emits the plan.
Each edit replaces one passage that must appear exactly once, and anything
else is rejected and reported. A 40 KB plan can't lose a section to a sloppy
rewrite this way, and the output stays a few hundred tokens.

Every fold also appends one line to the plan's "## Revision history" section,
even when no edit was needed. That section is how migite knows which runs the
plan already reflects: their amendments are left out of later prompts, so
prompt size stays flat however many times a task is amended. The original,
approved plan is kept separately in 00-build/plan.md.

  python -m migite.plan_fold fold --plan P --run-slug S --date D --out O --report R
         [--amendment F] [--implementation F] [--fix F]... [--review F] [--repo-root DIR]

Writes the updated plan to --out and a JSON report to --report (revision line,
applied and rejected edits). Exit 1 when the agent call fails or its reply has
no usable JSON; the plan is left alone either way, since bash decides whether
to apply --out.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

from migite import config
from migite import doc_edits
from migite import gateway

HISTORY_HEADING = "## Revision history"
RUN_SLUG_RE = re.compile(r"^[0-9][0-9]+-(build|amend-[a-z0-9-]+)$")

# The edit contract is shared with every other exact-edit update (migite.doc_edits).
FOLD_SCHEMA = doc_edits.EDIT_SCHEMA
parse_reply = doc_edits.parse_reply


# ── Pure helpers ─────────────────────────────────────────────────────────────

def split_history(plan: str) -> tuple[str, str]:
    """(body, history): history is the "## Revision history" section to the end
    of the file, heading included, or "" when the plan has none yet."""
    m = re.search(rf"^{re.escape(HISTORY_HEADING)}[ \t]*$", plan, re.MULTILINE)
    if not m:
        return plan, ""
    return plan[:m.start()], plan[m.start():]


def folded_runs(plan: str) -> list[str]:
    """Run slugs the plan's revision history records as folded, in order."""
    _, history = split_history(plan)
    runs = []
    for token in re.findall(r"`([^`]+)`", history):
        if RUN_SLUG_RE.match(token) and token not in runs:
            runs.append(token)
    return runs


def revision_line(date: str, run_slug: str, text: str) -> str:
    return f"- {date} `{run_slug}`: {' '.join(text.split())}"


def add_revision(plan: str, line: str) -> str:
    body, history = split_history(plan)
    if not history:
        return body.rstrip("\n") + f"\n\n{HISTORY_HEADING}\n\n{line}\n"
    return body + history.rstrip("\n") + f"\n{line}\n"


def apply_edits(plan: str, edits: list[dict]) -> tuple[str, list[dict], list[dict]]:
    """doc_edits.apply_edits on the plan body only: the revision history, the record
    of which runs the plan reflects, is never editable by the model."""
    body, history = split_history(plan)
    body, applied, rejected = doc_edits.apply_edits(body, edits)
    return body + history, applied, rejected


def build_prompt(plan: str, run_slug: str, *, amendment: str = "", implementation: str = "",
                 fixes: list[tuple[str, str]] | None = None, review: str = "") -> str:
    body, _ = split_history(plan)
    fix_block = "\n".join(f"### {name}\n{text[:3000]}" for name, text in (fixes or [])) or "(none)"
    amendment_block = f"\n## Amendment this run implemented\n{amendment}\n" if amendment.strip() else ""
    return f"""You are keeping a software task's living plan current after one run of work ({run_slug}).
plan.md must describe the design AS IT NOW STANDS, so the next person or agent to read it isn't misled.
Propose the smallest set of exact text edits to plan.md that make it true to what this run decided and built.
{amendment_block}
## Implementation notes from this run (first 8000 characters)
{implementation[:8000] or "(none)"}

## Fix rounds in this run
{fix_block}

## Final review of this run (first 3000 characters)
{review[:3000] or "(none)"}

## plan.md (revision history omitted)
{body}

## Rules
- Each edit's `find` is a passage copied VERBATIM from plan.md above (whitespace included), long enough
  to appear exactly once. `replace` is that passage corrected. Edits that don't match exactly once are dropped.
- Change only what this run made untrue or incomplete: behaviour, values, files, steps, risks, and open
  questions this run settled (record the answer where the question is). Keep everything else verbatim,
  including headings and structure. Don't reword for style.
- Write each `replace` as the design now is, as if it had always been planned that way. Never mention
  the run, the amendment, "now", "no longer", or what the text used to say: migite records the change
  in the revision history, and the approved original is kept separately.
- If nothing in the plan became untrue, return an empty `edits` list.
- `revision`: one sentence, at most 25 words, saying what changed in the plan
  (e.g. "Enqueue delay is 15 seconds, not 1 minute."), or "No plan changes." for an empty list.

Return ONLY a JSON object: {{"revision": "...", "edits": [{{"find": "...", "replace": "...", "why": "..."}}]}}"""


def fold(plan: str, reply: dict, *, date: str, run_slug: str) -> tuple[str, dict]:
    """The updated plan and a report, from a parsed reply."""
    new_plan, applied, rejected = apply_edits(plan, reply["edits"])
    # A revision line claims a change only when an edit actually landed.
    revision = (reply["revision"].strip() or "Plan updated.") if applied else "No plan changes."
    new_plan = add_revision(new_plan, revision_line(date, run_slug, revision))
    report = {
        "run": run_slug,
        "revision": revision_line(date, run_slug, revision),
        "applied": applied,
        "rejected": rejected,
        "changed_body": bool(applied),
    }
    return new_plan, report


# ── CLI ──────────────────────────────────────────────────────────────────────

def _read(path: str | None) -> str:
    if not path:
        return ""
    p = Path(path)
    return p.read_text() if p.is_file() else ""


def _cmd_fold(args: argparse.Namespace) -> int:
    plan = _read(args.plan)
    if not plan.strip():
        print(f"  ✘ plan_fold: no plan at {args.plan}", file=sys.stderr, flush=True)
        return 1
    prompt = build_prompt(
        plan, args.run_slug,
        amendment=_read(args.amendment),
        implementation=_read(args.implementation),
        fixes=[(Path(f).name, _read(f)) for f in args.fix],
        review=_read(args.review),
    )
    schema = FOLD_SCHEMA if gateway.supports("structured_output") else None
    try:
        res = gateway.call_agent(prompt, "plan_fold", label="plan_fold", tool="migite", schema=schema)
    except gateway.AgentError as e:
        print(f"  ✘ plan_fold: {e}", file=sys.stderr, flush=True)
        return 1
    reply = parse_reply(res.text, res.structured)
    if reply is None:
        print("  ✘ plan_fold: the reply had no usable JSON edit list", file=sys.stderr, flush=True)
        return 1
    new_plan, report = fold(plan, reply, date=args.date, run_slug=args.run_slug)
    Path(args.out).write_text(new_plan)
    gateway.write_json(args.report, report)
    print(f"  ✔ {len(report['applied'])} plan edit(s) proposed that apply cleanly, "
          f"{len(report['rejected'])} dropped", flush=True)
    for r in report["rejected"]:
        print(f"    ⚠ dropped ({r['reason']}): {r['find'][:80]!r}", flush=True)
    return 0


def _cmd_folded(args: argparse.Namespace) -> int:
    for run in folded_runs(_read(args.plan)):
        print(run)
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description="plan_fold: keep plan.md current after each run")
    ap.add_argument("--repo-root", default=None)
    sub = ap.add_subparsers(dest="command", required=True)
    f = sub.add_parser("fold", help="propose and apply this run's edits to the plan")
    f.add_argument("--plan", required=True)
    f.add_argument("--run-slug", required=True)
    f.add_argument("--date", required=True)
    f.add_argument("--out", required=True, help="where to write the updated plan")
    f.add_argument("--report", required=True, help="where to write the JSON report")
    f.add_argument("--amendment", default="")
    f.add_argument("--implementation", default="")
    f.add_argument("--fix", action="append", default=[], help="a fix-rN.md; repeat, in order")
    f.add_argument("--review", default="")
    f.add_argument("--repo-root", dest="repo_root", default=argparse.SUPPRESS)
    fd = sub.add_parser("folded", help="list the runs the plan's revision history records")
    fd.add_argument("--plan", required=True)
    args = ap.parse_args()

    if args.command == "folded":
        sys.exit(_cmd_folded(args))
    try:
        cfg = config.load(args.repo_root or os.getcwd())
    except config.ConfigError as e:
        print(f"  ✘ plan_fold: config error: {e}", file=sys.stderr, flush=True)
        sys.exit(1)
    gateway.configure_from(cfg)
    sys.exit(_cmd_fold(args))


if __name__ == "__main__":
    main()
