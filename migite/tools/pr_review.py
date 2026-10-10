#!/usr/bin/env python3
# migite-pr-review — Autonomous PR reviewer using LangGraph
#
# Diffs a local branch against a base, fans out 4 parallel specialist reviewers,
# has a second agent try to disprove their Criticals (migite/verify.py),
# synthesises an APPROVED / NEEDS CHANGES verdict, and writes a review report.
# Called by the migite-pr-review bash wrapper.

import argparse
import operator
import os
import re
import shlex
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from migite import gateway
from migite import config
from migite import paths
from migite import verify
from migite import checklists as checklists_lib

# Defaults from config's single table; main() replaces them from the loaded
# config (one role per dimension, plus `pr_verdict`).

VAULT_BASE = os.environ.get("DEV_LOG_BASE", str(Path.home() / "dev-log"))

# Same ticket-in-branch-name heuristic as lib/phases/amend.sh's BRANCH_TICKET —
# a branch like "feature/bb-3136-add-pdf-export" embeds the ticket key amid
# other text, so this can't reuse paths.extract_ticket_key (anchored,
# whole-string match only).
BRANCH_TICKET_RE = re.compile(r"[A-Za-z]+-\d+")

# What each reviewer checks comes from the stack's checklist (migite/checklists.py,
# prompts/checklists/<stack>.md). The rails one is the default when no --stack is
# given; main() loads the stack's own, with any prompts.dir override on top.
MIGITE_HOME = Path(__file__).resolve().parents[2]   # migite/tools/<x>.py → the checkout
RAILS_CHECKLIST = checklists_lib.load("rails", MIGITE_HOME)
PR_REVIEW_DIMENSIONS = RAILS_CHECKLIST.pr_review_dimensions()


# ── Agent call ───────────────────────────────────────────────────────────────────

def call_agent(prompt: str, role: str, label: str = "") -> str:
    """Shared wrapper (gateway): JSON envelope, usage ledger, config-driven timeouts/permissions/effort."""
    return gateway.call_agent(prompt, role, tool="migite-pr-review", label=label).text


# Each reviewer is its own config role (pr_review_<dimension>).


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
    stack: str              # rails, generic or a stacks.<name> profile
    dimensions: list        # [(dimension, checks)] from the stack's checklist
    expertise: str          # "a senior <expertise> engineer"
    refute_how: str | None  # the checklist's refute section; None = verify.HOW_TO_WORK
    lint_log_file: str      # a profile's lint / test output, run by bin/migite-pr-review
    test_log_file: str
    diff: str
    commits: str
    changed_files: list
    file_contents: str
    rubocop_log: str
    rspec_log: str
    findings: Annotated[list[str], operator.add]
    verified: list[str]     # findings after verify_findings: confirmed, demoted or removed
    refuted: list[dict]     # what verification disproved, for the report's appendix
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
    stack: str
    expertise: str


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

    head = run_cmd("git rev-parse --abbrev-ref HEAD", repo_root)
    if head and head != branch:
        print(f"    ⚠ the working tree is on '{head}', not '{branch}': reviewers and the refuter read "
              f"files from the working tree, so check out '{branch}' first for a trustworthy review", flush=True)

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
            chunk = f"### {rel_path}\n```{checklists_lib.fence(state.get('stack', 'rails'), rel_path)}\n{content}\n```"
            file_parts.append(chunk)
            total += len(chunk)
        except Exception:
            pass
    file_contents = "\n\n".join(file_parts)

    rubocop_log = ""
    rspec_log   = ""

    stack = state.get("stack", "rails")
    if not state["skip_tests"] and stack != "rails":
        # bin/migite-pr-review ran a profile's lint and tests (lib/stack.sh); generic has neither.
        def profile_log(path: str, kind: str) -> str:
            if path and Path(path).is_file():
                return Path(path).read_text(errors="replace")[:3000]
            return f"(not run: the {stack} stack has no {kind} command)"
        rubocop_log = profile_log(state.get("lint_log_file", ""), "lint")
        rspec_log = profile_log(state.get("test_log_file", ""), "test")
    elif not state["skip_tests"]:
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
    dimensions = state.get("dimensions") or PR_REVIEW_DIMENSIONS
    print(f"  ▶ Dispatching {len(dimensions)} parallel reviewers", flush=True)
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
            "stack":        state.get("stack", "rails"),
            "expertise":    state.get("expertise", "Rails"),
        })
        for dim, checks in dimensions
    ]


def review_dimension(state: DimensionInput) -> dict:
    dim = state["dimension"]
    print(f"    ◦ {dim}  ({gateway.model_for(f'pr_review_{dim}') or 'default'})", flush=True)

    lint_label, test_label = checklists_lib.log_labels(state.get("stack", "rails"))
    prompt = f"""You are a senior {state.get('expertise', 'Rails')} engineer reviewing a pull request on branch **{state['branch']}**.
Your focus: **{dim}** only. Do not repeat issues covered by other dimensions.

## Commits
{state['commits']}

## Git diff (first 12k chars)
{state['diff'][:12000]}

## Full file contents (context)
{state['file_contents'][:6000]}

## {lint_label}
{state['rubocop_log'][:1500] or '(not run)'}

## {test_label}
{state['rspec_log'][:1500] or '(not run)'}

Check for:
{state['checks']}

For each finding, output this block. The Fix line is required on every finding, Notes included. The
Evidence line (one line, each part in single backticks) is required on every Critical and Warning:

- 🔴 **Critical** (or 🟡 **Warning**, or 🟢 **Note**) · `path/file.rb:N` · <short title>
  - **Problem:** <what is wrong, and what it causes in practice>
  - **Evidence:** `path/file.rb:N` `the exact line of code, copied verbatim`
  - **Fix:** <the change you recommend: which file, which method or line, and what to change,
    concrete enough to apply without re-reading the diff. When the change is small and local,
    include it as a short fenced code block.>
  - **Alternative:** <another viable approach, and the trade-off that would make someone pick it>

Rules for fixes:
- Give one or two Alternatives only when they genuinely exist; omit the line when there is one sensible fix.
- Name the code to change. Never "consider refactoring" or "add tests" without saying which test
  and what it asserts.
- Stay within this PR's scope. When the right fix is larger, give the minimal safe fix as Fix
  and the larger change as the Alternative.

{verify.GROUNDING}

If no issues found: ✅ No issues in {dim}.
No preamble. Findings only."""

    try:
        result = call_agent(prompt, f"pr_review_{dim}", label=f"review:{dim}")
    except Exception as e:
        result = (f"- 🔴 **Critical** · (none) · the {dim} reviewer failed\n"
                  f"  - **Problem:** {e}\n"
                  f"  - **Fix:** re-run migite-pr-review; if it fails again, check the agent CLI with `migite doctor`.")
    return {"findings": [f"### {dim}\n{result}"]}


FINDING_RE = re.compile(r"^#{3,4}\s*[🔴🟡🟢]")


def findings_without_fix(doc: str) -> list[str]:
    """Titles of findings in the review's Findings section that have no **Fix:** line.
    A finding is a heading that starts with a severity mark; its block runs to the next
    heading of the same kind or the next top-level section."""
    missing: list[str] = []
    in_findings = False
    title, has_fix = None, False
    for line in doc.splitlines() + ["## end"]:
        stripped = line.strip()
        if stripped.startswith("## "):
            if title is not None and not has_fix:
                missing.append(title)
            title, has_fix = None, False
            in_findings = stripped.lower().startswith("## findings")
            continue
        if not in_findings:
            continue
        if FINDING_RE.match(stripped):
            if title is not None and not has_fix:
                missing.append(title)
            title, has_fix = stripped.lstrip("#").strip(), False
        elif title is not None and re.match(r"^[-*\s]*\*\*Fix:\*\*", stripped):
            has_fix = True
    return missing


def reviewed(state: PRReviewState) -> list[str]:
    """The reviewers' findings after verification, or as reported when it did not run."""
    return state.get("verified") or state["findings"]


def verify_findings(state: PRReviewState) -> dict:
    """Evidence gate and refuter over every dimension's findings, before the synthesis sees them."""
    context = (f"\nThe pull request is branch `{state['branch']}` against `{state['base']}`; read files from "
               "the repository working tree.\n")
    blocks, refuted = verify.verify_blocks(
        state["findings"],
        ask=lambda prompt, label: call_agent(prompt, verify.REFUTE_ROLE, label=label),
        read_file=verify.make_reader(state["repo_root"], ref=state["branch"]),
        extra=state["diff"],
        context=context,
        expertise=state.get("expertise", "Rails"),
        how_to_work=state.get("refute_how"),
    )
    return {"verified": blocks, "refuted": refuted}


def synthesize_verdict(state: PRReviewState) -> dict:
    print(f"  ▶ Synthesising verdict", flush=True)
    findings_text = "\n\n".join(reviewed(state))
    today = date.today().isoformat()
    jira_line = f"Jira: {state['jira']}\n" if state.get("jira") else ""
    jira_context = f"\nJira ticket for reference: {state['jira']}" if state.get("jira") else ""

    prompt = f"""You are synthesising a PR review for branch **{state['branch']}** against **{state['base']}** (date: {today}).{jira_context}

## Commits
{state['commits']}

## Specialist findings
{findings_text}

Produce a single PR review document:
1. De-duplicate findings reported by multiple reviewers. When two reviewers suggest different fixes
   for the same issue, keep the more concrete one as Fix and the other as an Alternative.
2. Group by severity: Critical, then Warnings, then Notes. Number findings continuously across the
   groups (1, 2, 3, ...) so they can be referenced in PR comments.
3. Keep the file path and line for every finding.
4. Every finding keeps its Fix, and its Alternatives when a reviewer gave them. Copy fixes and
   their code blocks in full; never shorten a fix to a phrase or drop it.
5. Keep each finding's Evidence, Verified and Verification lines as given. A finding already
   demoted to a Note by verification stays a Note; never promote it.
6. The verdict is NEEDS CHANGES only when a Critical finding remains, or a Warning would break
   behaviour the PR claims to deliver. Warnings and Notes alone are APPROVED WITH COMMENTS.

Output format:
# PR Review: {state['branch']}
Date: {today}
Base: {state['base']}
{jira_line}Changed files: {len(state['changed_files'])}

## Summary
<2-3 sentences: what this PR does and overall quality assessment>

## Findings
### Critical
#### 🔴 1. <short title> · `path/file.rb:N`
**Problem:** <what is wrong, and what it causes>
**Evidence:** <the quoted line, when the reviewer gave one>
**Fix:** <the recommended change, with a short code block when it helps>
**Alternative:** <another approach and its trade-off; one line per alternative, omitted when there is none>

### Warnings
#### 🟡 2. <short title> · `path/file.rb:N`
**Problem:** ...
**Fix:** ...

### Notes
#### 🟢 3. <short title> · `path/file.rb:N`
**Problem:** ...
**Fix:** ...

## Verdict
APPROVED — no issues requiring changes before merge
APPROVED WITH COMMENTS — minor issues; can merge after addressing
NEEDS CHANGES — one or more issues must be fixed before merging

(Omit a severity group that has no findings. If no issues were found in any dimension, omit Findings entirely.)
Output only the review document."""

    try:
        verdict = call_agent(prompt, label="synthesize_verdict", role="pr_verdict")
        missing = findings_without_fix(verdict)
        if missing:
            # The one thing a reader acts on is the fix; a finding without one is half a review.
            print(f"    ⚠ {len(missing)} finding(s) came back without a fix; asking once more", flush=True)
            retry = (prompt + "\n\nYour previous draft left these findings without a **Fix:** line: "
                     + "; ".join(missing) + ". Every finding needs one. Output the full document again.")
            second = call_agent(retry, label="synthesize_verdict:retry", role="pr_verdict")
            if len(findings_without_fix(second)) < len(missing):
                verdict = second
            still = findings_without_fix(verdict)
            if still:
                print(f"    ⚠ still without a fix: {'; '.join(still)}", flush=True)
    except Exception as e:
        verdict = (
            f"# PR Review: {state['branch']}\nDate: {today}\n\n"
            f"## Verdict\nNEEDS CHANGES\n\nSynthesis failed: {e}"
        )
    return {"verdict": verify.append_refuted(verdict, state.get("refuted") or [])}


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
    g.add_node("verify_findings",   verify_findings)
    g.add_node("synthesize_verdict", synthesize_verdict)
    g.add_node("write_review",      write_review)

    g.add_edge(START, "load_pr")
    g.add_conditional_edges("load_pr", route_to_reviewers, ["review_dimension"])
    g.add_edge("review_dimension",   "verify_findings")
    g.add_edge("verify_findings",    "synthesize_verdict")
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
    ap.add_argument("--skip-tests", action="store_true", help="Skip the lint and tests (rubocop + rspec on rails)")
    ap.add_argument("--stack",      default="rails", help="rails, generic or a stacks.<name> profile: picks the checklist")
    ap.add_argument("--lint-log",   default="", help="A stack profile's lint output (bin/migite-pr-review runs it)")
    ap.add_argument("--test-log",   default="", help="A stack profile's test output (bin/migite-pr-review runs it)")
    ap.add_argument("--jira",       default="", help="Jira ticket key or URL (optional — groups this review under the ticket's existing folder)")
    args = ap.parse_args()

    global VAULT_BASE
    try:
        cfg = config.load(args.repo_root)
    except config.ConfigError as e:
        print(f"✘ config error: {e}", file=sys.stderr)
        sys.exit(1)
    for w in cfg.warnings:
        print(f"  ⚠ config: {w}", flush=True)
    VAULT_BASE   = str(cfg.expanded_path("vault.base"))
    gateway.configure_from(cfg)
    try:
        gateway.require_cli()
    except gateway.AgentError as e:
        print(f"✘ {e}", file=sys.stderr)
        sys.exit(1)

    if not args.base:
        args.base = paths.detect_base_branch(args.repo_root)
    try:
        checklist = checklists_lib.load(args.stack, MIGITE_HOME, cfg.override_dir("prompts", args.repo_root))
    except checklists_lib.ChecklistError as e:
        print(f"✘ {e}", file=sys.stderr)
        sys.exit(1)
    dimensions = checklist.pr_review_dimensions()

    jira = args.jira
    jira_key = None
    if args.jira:
        try:
            jira_key = paths.extract_ticket_key(args.jira)
        except paths.InvalidRunKeyError:
            jira_key = None  # free-text reference (e.g. "see ENG board") — not used for folder resolution

    repo_name = Path(args.repo_root).name
    org       = paths.detect_org(args.repo_root)
    today     = date.today().isoformat()
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe_branch = args.branch.replace("/", "-")

    run_dir = None
    if not args.output and jira_key:
        try:
            run_dir = paths.resolve_run_dir(VAULT_BASE, org, repo_name, id=jira_key)
        except paths.AmbiguousRunDirError as e:
            print(f"✘ Ambiguous run folder for '{e.slug}': {', '.join(e.candidates)}", file=sys.stderr)
            print("  Pass --output explicitly to disambiguate.", file=sys.stderr)
            sys.exit(1)

    # No --jira (or no matching ticket folder) — fall back to the branch's own
    # task folder, if one already exists (e.g. from an earlier `migite` run on
    # this branch). Never created on demand, unlike the --jira folder above:
    # an ad hoc PR review on an arbitrary branch shouldn't seed a new vault dir.
    branch_run_dir = None
    if not args.output and not run_dir:
        branch_ticket_match = BRANCH_TICKET_RE.search(args.branch)
        if branch_ticket_match:
            branch_slug = paths.slugify(branch_ticket_match.group(0).upper())
            candidate = Path(VAULT_BASE) / org / repo_name / branch_slug
            if candidate.is_dir():
                branch_run_dir = candidate

    output = args.output or (
        str(run_dir / f"pr-review-{safe_branch}-{timestamp}.md") if run_dir
        else str(branch_run_dir / f"pr-review-{today}.md") if branch_run_dir
        else str(Path(VAULT_BASE) / org / repo_name / f"pr-review-{safe_branch}-{today}.md")
    )

    print(f"\n  migite-pr-review | " + "  ".join(f"{d}={gateway.model_for(f'pr_review_{d}') or 'default'}" for d, _ in dimensions) + f"  refute={gateway.model_for(verify.REFUTE_ROLE) or 'default'}  verdict={gateway.model_for('pr_verdict') or 'default'}", flush=True)
    print(f"  Branch: {args.branch}  Base: {args.base}  Repo: {org}/{repo_name}  Stack: {args.stack}", flush=True)
    print(f"  Checklist: {' + '.join(checklist.files)}", flush=True)
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
            "stack":         args.stack,
            "dimensions":    dimensions,
            "expertise":     checklist.expertise,
            "refute_how":    checklist.refute,
            "lint_log_file": args.lint_log,
            "test_log_file": args.test_log,
            "diff":          "",
            "commits":       "",
            "changed_files": [],
            "file_contents": "",
            "rubocop_log":   "",
            "rspec_log":     "",
            "findings":      [],
            "verified":      [],
            "refuted":       [],
            "verdict":       "",
        })
        print(f"\n  ✔ migite-pr-review complete", flush=True)
    except Exception as e:
        print(f"\n  ✘ migite-pr-review failed: {e}", file=sys.stderr, flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
