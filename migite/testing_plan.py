#!/usr/bin/env python3
"""migite.testing_plan - writes testing-plan.md: the QA/dev steps a human runs by hand to
confirm the change works. One prompt, two moments:

  plan.testing_plan_when: plan    migite-plan (Phase 1) writes it from the finished plan.
  plan.testing_plan_when: review  Phase 3 writes it from the plan and the diff, before the
                                  browser check and the reviewers read it.

This module imports no langgraph, so bash can run it on its own (as it runs migite.doc_edits):

  python -m migite.testing_plan --plan plan.md --out testing-plan.md \\
         [--diff change.txt] [--frontend] [--repo-root DIR]

Exit 0 and --out written; 1 when the config or the call failed; 2 when the reply was empty.
--out is never touched on a non-zero exit, so a failed call can't truncate a good file.
"""

import argparse
import os
import sys
from pathlib import Path

from migite import config
from migite import gateway

ROLE = "testing_plan"                  # standard tier (migite/config.py ROLE_TIERS)
LABEL = "generate_testing_plan"        # the ledger label, the same at both moments
TOOL = "migite-plan"


def build_prompt(plan: str, *, frontend: bool = False, diff: str = "") -> str:
    """The generation prompt. With `diff` (the change as built, already capped by the caller)
    the document is written from what exists, and the diff wins where it and the plan differ."""
    frontend_steps = (
        "\n<This plan touches the frontend: for every UI step give the page path on the local dev "
        "server, the exact action (click, fill, submit), and what should change on the page, "
        "including whether Turbo should update it in place without a full page reload. Add a step "
        "to check the browser console for JavaScript errors. These steps are written so a person, "
        "or an agent driving a browser, can follow them literally.>"
        if frontend else ""
    )
    built = (
        f"\n## What was built (the diff against the base branch, then the files changed)\n{diff}\n\n"
        "The code is already written. Where it and the plan disagree, the diff is right: use its real "
        "routes, parameter names, response shapes and log lines, and never invent ones it does not show.\n"
        if diff else ""
    )
    return f"""You are writing a standalone QA/dev verification document for the implementation
plan below — the concrete steps a human runs by hand, after the code is built, to confirm it
actually works. This document lives on its own (not inside the plan) precisely so it can be
regenerated in full whenever the implementation changes, without touching the plan's history.

## Implementation plan
{plan}
{built}
## Instructions
Output ONLY the document below, no preamble, no meta-commentary. Use exactly this structure:

# Testing Plan

### Prerequisites — seed records (Rails console)
```ruby
<a runnable Rails console script that seeds whatever records this plan's verification needs.
Use only generic emails (test.user@example.com, admin.qa@example.com) — never real addresses.>
```

### Verification steps
<numbered steps — curl commands or browser/UI actions that exercise this plan's scope. Cover the
happy path and the key error/edge cases called out in the plan. Include at least one step naming
a log line to `grep` for as evidence the code path actually ran.>{frontend_steps}

### Teardown
```ruby
<a runnable Rails console script that removes exactly the seed records created above>
```

If the plan involves no endpoints or user-facing behaviour to verify by hand (e.g. a pure internal
refactor with only spec coverage), say so explicitly under each section and state what to verify
instead (e.g. "run the full spec suite for X") rather than inventing steps that don't apply."""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="migite.testing_plan: write testing-plan.md")
    ap.add_argument("--plan", required=True, help="Path to plan.md")
    ap.add_argument("--out", required=True, help="Path to write testing-plan.md (only on success)")
    ap.add_argument("--diff", default="", help="Path to a file holding the change as built (capped diff and file list)")
    ap.add_argument("--frontend", action="store_true", help="The change touches views or JavaScript: write browser-ready steps")
    ap.add_argument("--repo-root", default=None)
    args = ap.parse_args(argv)
    try:
        cfg = config.load(args.repo_root or os.getcwd())
    except config.ConfigError as e:
        print(f"  ✘ testing_plan: config error: {e}", file=sys.stderr, flush=True)
        return 1
    gateway.configure_from(cfg)
    plan = Path(args.plan).read_text()
    diff = Path(args.diff).read_text(errors="replace") if args.diff and Path(args.diff).is_file() else ""
    prompt = build_prompt(plan, frontend=args.frontend, diff=diff)
    try:
        text = gateway.call_agent(prompt, ROLE, label=LABEL, tool=TOOL).text
    except gateway.AgentError as e:
        print(f"  ✘ testing_plan: {e}", file=sys.stderr, flush=True)
        return 1
    if not text.strip():
        print("  ⚠ testing_plan: the reply was empty", flush=True)
        return 2
    Path(args.out).write_text(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
