#!/usr/bin/env python3
# migite-pr-review — Autonomous PR reviewer using LangGraph
#
# Diffs a local branch against a base, fans out 4 parallel specialist reviewers,
# synthesises an APPROVED / NEEDS CHANGES verdict, and writes a review report.
# Called by the migite-pr-review bash wrapper.

import argparse
import operator
import os
import shlex
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

import migite_paths

REVIEW_MODEL  = "claude-sonnet-5"
CRITIC_MODEL  = "claude-opus-5"

VAULT_BASE = os.environ.get("DEV_LOG_BASE", str(Path.home() / "dev-log"))

PR_REVIEW_DIMENSIONS = [
    (
        "correctness",
        """\
- Logic errors or bugs in the implementation
- Edge cases not handled (nil values, empty collections, boundary conditions)
- Incorrect conditionals, off-by-one errors, or flipped logic
- Methods that can return unexpected types or nils
- Unintended behaviour changes — side effects beyond the PR's stated scope""",
    ),
    (
        "security",
        """\
- Controller actions not covered by Pundit policy or manual authorization check
- Collections or records not scoped to current_user / current_account
- N+1 queries introduced by the PR (associations loaded inside loops or serializers)
- Raw SQL without ActiveRecord parameterisation
- Actions missing strong params or accepting raw params
- Sensitive data exposed in serializer or log output
- Hardcoded secrets, tokens, or credentials""",
    ),
    (
        "test_coverage",
        """\
- New public methods without unit specs
- New endpoints without request specs covering success, 401 (unauthorized), and 422 (invalid input)
- Context blocks that don't start with 'when', 'with', or 'without' (RSpec convention)
- Missing factory definitions for new models or associations
- Tests that call real external services instead of stubbing them
- Specs that assert implementation details rather than behaviour""",
    ),
    (
        "conventions_and_migrations",
        """\
- Business logic inline in controller actions (belongs in a service object)
- Rubocop-flagged violations in the diff
- Migrations that: add a NOT NULL column without a default or data migration, lack `down` / reversible
- Foreign keys in migrations without a corresponding `add_index`
- Background jobs that are non-idempotent or lack an explicit queue
- Rails anti-patterns: find instead of find_by!, rescue Exception, memoization with ||= on falsy values""",
    ),
]


# ── Claude call ──────────────────────────────────────────────────────────────────

def call_claude(prompt: str, model: str = REVIEW_MODEL) -> str:
    import time
    cmd = ["claude", "--print", "--output-format", "text", "--model", model]
    permission_mode = os.environ.get("MIGITE_PERMISSION_MODE", "")
    if permission_mode:
        cmd += ["--permission-mode", permission_mode]
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
    print(f"      claude cmd: {' '.join(cmd)}", flush=True)
    t0 = time.monotonic()
    try:
        r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, env=env, timeout=600)
    except subprocess.TimeoutExpired:
        elapsed = time.monotonic() - t0
        raise RuntimeError(f"claude --print timed out after {elapsed:.0f}s (limit=600s, model={model})")
    elapsed = time.monotonic() - t0
    print(f"      ✔ claude returned in {elapsed:.1f}s (exit {r.returncode})", flush=True)
    if r.returncode != 0:
        raise RuntimeError(f"claude --print failed (exit {r.returncode}, {elapsed:.1f}s): {r.stderr[:300]}")
    return r.stdout.strip()


def run_cmd(cmd: str, cwd: str, timeout: int = 60) -> str:
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, cwd=cwd, timeout=timeout)
    return (r.stdout + r.stderr).strip()


# ── State ────────────────────────────────────────────────────────────────────────

class PRReviewState(TypedDict):
    branch: str
    base: str
    repo_root: str
    output: str
    skip_tests: bool
    jira: str
    diff: str
    commits: str
    changed_files: list
    file_contents: str
    rubocop_log: str
    rspec_log: str
    findings: Annotated[list[str], operator.add]
    verdict: str


class DimensionInput(TypedDict):
    dimension: str
    checks: str
    diff: str
    commits: str
    file_contents: str
    rubocop_log: str
    rspec_log: str
    branch: str


# ── Nodes ─────────────────────────────────────────────────────────────────────────

def load_pr(state: PRReviewState) -> dict:
    branch    = state["branch"]
    base      = state["base"]
    repo_root = state["repo_root"]

    print(f"  ▶ Loading PR: {branch}..{base}", flush=True)

    safe_base, safe_branch_ref = shlex.quote(base), shlex.quote(branch)
    diff = run_cmd(f"git diff {safe_base}...{safe_branch_ref}", repo_root)[:16000]
    if not diff:
        raise RuntimeError(
            f"No diff found between '{base}' and '{branch}'. "
            "Is the branch checked out locally? Does it have commits ahead of {base}?"
        )

    commits = run_cmd(f"git log {safe_base}...{safe_branch_ref} --oneline", repo_root)
    changed_files_raw = run_cmd(f"git diff {safe_base}...{safe_branch_ref} --name-only", repo_root)
    changed_files = [f for f in changed_files_raw.split("\n") if f.strip()]

    print(f"    {len(changed_files)} files changed, {len(commits.splitlines())} commit(s)", flush=True)

    # Read full content of changed files for richer context
    file_parts: list[str] = []
    total = 0
    for rel_path in changed_files:
        if total > 12000:
            file_parts.append("(remaining files omitted — context limit)")
            break
        full = Path(repo_root) / rel_path
        try:
            content = full.read_text(errors="replace")[:2500]
            chunk = f"### {rel_path}\n```ruby\n{content}\n```"
            file_parts.append(chunk)
            total += len(chunk)
        except Exception:
            pass
    file_contents = "\n\n".join(file_parts)

    rubocop_log = ""
    rspec_log   = ""

    if not state["skip_tests"]:
        ruby_src = [f for f in changed_files if f.endswith(".rb") and not f.endswith("_spec.rb")]
        spec_files = [f for f in changed_files if f.endswith("_spec.rb")]

        if ruby_src:
            print(f"    ▶ Running rubocop on {len(ruby_src)} file(s)", flush=True)
            try:
                rubocop_log = run_cmd(
                    f"bundle exec rubocop {' '.join(shlex.quote(f) for f in ruby_src)} --format progress --no-color",
                    repo_root, timeout=90
                )[:3000]
            except Exception as e:
                rubocop_log = f"(rubocop failed: {e})"

        if spec_files:
            print(f"    ▶ Running rspec on {len(spec_files)} spec file(s)", flush=True)
            try:
                rspec_log = run_cmd(
                    f"bundle exec rspec {' '.join(shlex.quote(f) for f in spec_files)} --format progress --no-color",
                    repo_root, timeout=120
                )[:3000]
            except Exception as e:
                rspec_log = f"(rspec failed: {e})"
    else:
        print("    ▶ Skipping rubocop + rspec (--skip-tests)", flush=True)

    return {
        "diff":          diff,
        "commits":       commits,
        "changed_files": changed_files,
        "file_contents": file_contents,
        "rubocop_log":   rubocop_log,
        "rspec_log":     rspec_log,
    }


def route_to_reviewers(state: PRReviewState) -> list[Send]:
    print(f"  ▶ Dispatching {len(PR_REVIEW_DIMENSIONS)} parallel reviewers", flush=True)
    return [
        Send("review_dimension", {
            "dimension":    dim,
            "checks":       checks,
            "diff":         state["diff"],
            "commits":      state["commits"],
            "file_contents": state["file_contents"],
            "rubocop_log":  state["rubocop_log"],
            "rspec_log":    state["rspec_log"],
            "branch":       state["branch"],
        })
        for dim, checks in PR_REVIEW_DIMENSIONS
    ]


def review_dimension(state: DimensionInput) -> dict:
    dim = state["dimension"]
    print(f"    ◦ {dim}", flush=True)

    prompt = f"""You are a senior Rails engineer reviewing a pull request on branch **{state['branch']}**.
Your focus: **{dim}** only. Do not repeat issues covered by other dimensions.

## Commits
{state['commits']}

## Git diff (first 12k chars)
{state['diff'][:12000]}

## Full file contents (context)
{state['file_contents'][:6000]}

## Rubocop
{state['rubocop_log'][:1500] or '(not run)'}

## RSpec
{state['rspec_log'][:1500] or '(not run)'}

Check for:
{state['checks']}

For each finding output exactly:
- 🔴 **Critical** / 🟡 **Warning** / 🟢 **Note** — `path/file.rb:N` — problem — suggested fix

If no issues found: ✅ No issues in {dim}.
No preamble. Findings only."""

    try:
        result = call_claude(prompt)
    except Exception as e:
        result = f"🔴 **Critical** — reviewer failed: {e}"
    return {"findings": [f"### {dim}\n{result}"]}


def synthesize_verdict(state: PRReviewState) -> dict:
    print(f"  ▶ Synthesising verdict", flush=True)
    findings_text = "\n\n".join(state["findings"])
    today = date.today().isoformat()
    jira_line = f"Jira: {state['jira']}\n" if state.get("jira") else ""
    jira_context = f"\nJira ticket for reference: {state['jira']}" if state.get("jira") else ""

    prompt = f"""You are synthesising a PR review for branch **{state['branch']}** against **{state['base']}** (date: {today}).{jira_context}

## Commits
{state['commits']}

## Specialist findings
{findings_text}

Produce a single PR review document:
1. De-duplicate findings reported by multiple reviewers
2. Group by severity: Critical → Warnings → Notes
3. Keep file paths for every finding

Output format:
# PR Review: {state['branch']}
Date: {today}
Base: {state['base']}
{jira_line}Changed files: {len(state['changed_files'])}

## Summary
<2-3 sentences: what this PR does and overall quality assessment>

## Findings
### Critical
- 🔴 `path/file.rb:N` — problem — fix

### Warnings
- 🟡 `path/file.rb:N` — problem — fix

### Notes
- 🟢 `path/file.rb:N` — observation

## Verdict
APPROVED — no issues requiring changes before merge
APPROVED WITH COMMENTS — minor issues; can merge after addressing
NEEDS CHANGES — one or more issues must be fixed before merging

(If no issues found in any dimension, omit Findings entirely)
Output only the review document."""

    try:
        verdict = call_claude(prompt, model=CRITIC_MODEL)
    except Exception as e:
        verdict = (
            f"# PR Review: {state['branch']}\nDate: {today}\n\n"
            f"## Verdict\nNEEDS CHANGES\n\nSynthesis failed: {e}"
        )
    return {"verdict": verdict}


def write_review(state: PRReviewState) -> dict:
    print(f"  ▶ Writing review", flush=True)
    out = Path(state["output"])
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(state["verdict"])
    print(f"    ✔ {out}", flush=True)

    # Print verdict line to terminal
    for line in state["verdict"].splitlines():
        stripped = line.strip()
        if stripped.startswith("APPROVED") or stripped.startswith("NEEDS CHANGES"):
            print(f"\n  Verdict: {stripped}", flush=True)
            break
    return {}


# ── Graph ─────────────────────────────────────────────────────────────────────────

def build_graph() -> StateGraph:
    g = StateGraph(PRReviewState)
    g.add_node("load_pr",           load_pr)
    g.add_node("review_dimension",  review_dimension)
    g.add_node("synthesize_verdict", synthesize_verdict)
    g.add_node("write_review",      write_review)

    g.add_edge(START, "load_pr")
    g.add_conditional_edges("load_pr", route_to_reviewers, ["review_dimension"])
    g.add_edge("review_dimension",   "synthesize_verdict")
    g.add_edge("synthesize_verdict", "write_review")
    g.add_edge("write_review",       END)
    return g.compile()


# ── CLI ───────────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description="migite-pr-review: autonomous PR reviewer")
    ap.add_argument("--branch",     required=True, help="Branch to review")
    ap.add_argument("--base",       default=None, help="Base branch (default: auto-detect origin/HEAD, then main/master/develop)")
    ap.add_argument("--repo-root",  required=True)
    ap.add_argument("--output",     default="", help="Output file path (default: vault)")
    ap.add_argument("--skip-tests", action="store_true", help="Skip rubocop + rspec")
    ap.add_argument("--jira",       default="", help="Jira ticket key or URL (optional — groups this review under the ticket's existing folder)")
    args = ap.parse_args()

    if not args.base:
        args.base = migite_paths.detect_base_branch(args.repo_root)

    jira = args.jira
    jira_key = None
    if args.jira:
        try:
            jira_key = migite_paths.extract_ticket_key(args.jira)
        except migite_paths.InvalidRunKeyError:
            jira_key = None  # free-text reference (e.g. "see ENG board") — not used for folder resolution

    repo_name = Path(args.repo_root).name
    org       = migite_paths.detect_org(args.repo_root)
    today     = date.today().isoformat()
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe_branch = args.branch.replace("/", "-")

    run_dir = None
    if not args.output and jira_key:
        try:
            run_dir = migite_paths.resolve_run_dir(VAULT_BASE, org, repo_name, id=jira_key)
        except migite_paths.AmbiguousRunDirError as e:
            print(f"✘ Ambiguous run folder for '{e.slug}': {', '.join(e.candidates)}", file=sys.stderr)
            print("  Pass --output explicitly to disambiguate.", file=sys.stderr)
            sys.exit(1)

    output = args.output or (
        str(run_dir / f"pr-review-{safe_branch}-{timestamp}.md") if run_dir
        else str(Path(VAULT_BASE) / org / repo_name / f"pr-review-{safe_branch}-{today}.md")
    )

    print(f"\n  migite-pr-review | model={REVIEW_MODEL}", flush=True)
    print(f"  Branch: {args.branch}  Base: {args.base}  Repo: {org}/{repo_name}", flush=True)
    if jira:
        print(f"  Jira: {jira}", flush=True)

    graph = build_graph()
    try:
        graph.invoke({
            "branch":        args.branch,
            "base":          args.base,
            "repo_root":     args.repo_root,
            "output":        output,
            "skip_tests":    args.skip_tests,
            "jira":          jira,
            "diff":          "",
            "commits":       "",
            "changed_files": [],
            "file_contents": "",
            "rubocop_log":   "",
            "rspec_log":     "",
            "findings":      [],
            "verdict":       "",
        })
        print(f"\n  ✔ migite-pr-review complete", flush=True)
    except Exception as e:
        print(f"\n  ✘ migite-pr-review failed: {e}", file=sys.stderr, flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
