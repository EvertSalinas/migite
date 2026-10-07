#!/usr/bin/env python3
# migite.tools.review (migite-review) — autonomous LangGraph review agent
#
# Reads plan.md, any amendments, implementation notes, rubocop/rspec logs (a stack
# profile's lint/test logs, with --stack <name>), and git diff.
# Fans out 4 parallel specialist reviewers (3 with review.dimensions.testing_plan
# off, 5 when the diff touches views or JavaScript: see FRONTEND_DIMENSION), has
# a second agent try to disprove their
# Criticals (migite/verify.py), synthesises a verdict, and writes
# review.md + sentinel. Called by migite's spawn_langgraph().
#
# Usage:
#   python -m migite.tools.review --plan <file> --implementation <file> \
#                 --rubocop-log <file> --rspec-log <file> \
#                 --repo-root <dir> --review-output <file> --sentinel <file>

import argparse
import hashlib
import json
import operator
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Annotated, Any, Mapping, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from migite import gateway
from migite import config
from migite import paths
from migite import verify
from migite import checklists as checklists_lib

# Defaults from config's single table; main() replaces them from the loaded
# config (one role per review dimension, plus `verdict`).

# Structured-output schema for the verdict synthesis. An agent with structured output
# validates the model's reply against this, so the verdict the commit gate
# reads is a typed enum, not a regex over prose. `document` carries the full
# human review; it is written to review.md unchanged.
REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["READY TO COMMIT", "NEEDS FIXES"]},
        "reason": {"type": "string", "description": "1-3 sentences: the single factor that decided the verdict"},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "severity": {"type": "string", "enum": ["critical", "warning", "note"]},
                    "dimension": {"type": "string"},
                    "file": {"type": "string"},
                    "line": {"type": ["integer", "null"]},
                    "problem": {"type": "string"},
                    "fix": {"type": "string"},
                },
                "required": ["severity", "problem"],
            },
        },
        "document": {"type": "string", "description": "the complete review document in the required markdown format"},
    },
    "required": ["verdict", "reason", "findings", "document"],
}

VERDICT_KEY = {"NEEDS FIXES": "needs_fixes", "READY TO COMMIT": "ready"}

# Ships in the repo's prompts/ dir next to this script (resolve() follows the
# ~/.local/bin symlink); `prompts.dir` in the config can override it per project.
# Hard error if missing — see read_prompt().
MIGITE_HOME = Path(__file__).resolve().parents[2]   # migite/tools/<x>.py → the checkout
REVIEW_CMD_PATH = MIGITE_HOME / "prompts" / "review.md"


def read_prompt(path: Path) -> str:
    if not path.is_file():
        print(f"  ✘ migite-review: prompt file missing: {path} — is the migite checkout intact?",
              file=sys.stderr, flush=True)
        sys.exit(1)
    return path.read_text()

# What each reviewer checks comes from the stack's checklist (migite/checklists.py,
# prompts/checklists/<stack>.md). The rails one is the default when no --stack is
# given; main() loads the stack's own, with any prompts.dir override on top. The
# frontend reviewer runs only when migite passes --frontend-files (lib/stack.sh
# changed_frontend_files found views or JavaScript in the diff, rails only), so a
# backend-only review stays at four reviewers.
RAILS_CHECKLIST = checklists_lib.load("rails", MIGITE_HOME)
REVIEW_DIMENSIONS = RAILS_CHECKLIST.review_dimensions()
FRONTEND_DIMENSION = ("frontend", RAILS_CHECKLIST.review["frontend"])


def frontend_criteria(system_specs: bool, base: str | None = None) -> str:
    """FRONTEND_DIMENSION's criteria plus the system-spec rule, which depends on
    whether the repo already has spec/system: demanding a first system spec (and
    a browser driver) as part of an unrelated change is scope creep."""
    if system_specs:
        rule = ("The repo has system specs (spec/system): a new interactive flow (Stimulus behaviour, "
                "Turbo frame navigation, a form that updates the page) with no system spec is a 🟡 Warning.")
    else:
        rule = ("The repo has no system specs: do not demand one, but add a 🟢 Note when a new "
                "interactive flow is covered only by the testing plan's manual steps.")
    return f"{base or FRONTEND_DIMENSION[1]} {rule}"


def amendments_block(amendments: list[str], limit: int) -> str:
    """The prompt section for a task's amendments, newest first so a `limit`
    cut drops the oldest ones, not the latest. Empty when there are none."""
    texts = [a.strip() for a in amendments if a.strip()]
    if not texts:
        return ""
    body = "\n\n".join(reversed(texts))[:limit]
    return ("\n## Amendments (approved after the plan, newest first; where one conflicts with the plan, "
            f"the amendment wins)\n{body}\n")


CARRIED_NOTE = "(not re-run: clean in the previous review, and nothing it checks was flagged)"


def has_findings(body: str) -> bool:
    """A dimension's output reports something (any severity) or its reviewer failed."""
    return any(mark in body for mark in ("🔴", "🟡", "🟢")) or "reviewer failed" in body


def dims_to_rerun(previous: dict, testing_plan: str, dim_names: list[str] | None = None) -> list[str]:
    """After a fix round, the dimensions worth re-running: correctness always, every
    dimension whose last output had findings (or failed, or is missing), and the
    testing-plan dimension when the testing plan changed since. The rest carry their
    clean result over. `previous` is review-dimensions.json from the last review.
    `dim_names` are the dimensions active this run (the frontend one joins only when
    the diff touches views or JavaScript); a dimension the last review never ran re-runs."""
    last = previous.get("dimensions") or {}
    rerun = []
    for dim in dim_names or [d for d, _ in REVIEW_DIMENSIONS]:
        body = last.get(dim)
        if dim == "correctness" or body is None or has_findings(body):
            rerun.append(dim)
        elif dim == "testing_plan" and previous.get("testing_plan_sha") != _sha(testing_plan):
            rerun.append(dim)
    return rerun


def carried_findings(previous: dict, rerun: list[str], dim_names: list[str] | None = None) -> list[str]:
    """The findings blocks for the dimensions not re-run, from the last review,
    marked as carried over. The note goes on once, however many rounds a
    dimension is carried."""
    last = previous.get("dimensions") or {}
    names = dim_names or [n for n, _ in REVIEW_DIMENSIONS]
    return [f"### {d}\n{CARRIED_NOTE}\n{last[d].replace(CARRIED_NOTE, '').strip()}"
            for d in names if d not in rerun and d in last]


def _sha(text: str) -> str:
    return hashlib.sha1(text.encode()).hexdigest()


# ── Agent call ───────────────────────────────────────────────────────────────────

def call_agent(prompt: str, role: str, label: str = "", schema: dict | None = None) -> gateway.CallResult:
    """Shared wrapper (see gateway): records usage under this tool + label;
    with `schema`, the result carries schema-validated `.structured` output;
    `role` selects the configured --effort level."""
    return gateway.call_agent(prompt, role, tool="migite-review", label=label, schema=schema)


# Each reviewer is its own config role (review_<dimension>), so correctness and
# security can sit on the strong tier while checklist dimensions stay standard.


def verdict_from_markdown(doc: str) -> str:
    """Deterministic fallback mirroring lib/gate.sh review_verdict(): anchored on
    the Verdict heading, never a whole-file keyword grep."""
    lines = doc.splitlines()
    for i, line in enumerate(lines):
        if re.match(r"^#+\s*[Vv]erdict", line):
            rest = re.sub(r"^#+\s*[Vv]erdict\s*:?\s*", "", line)
            if not re.search(r"[^\s*_]", rest):
                rest = next((l for l in lines[i + 1:] if l.strip()), "")
            if re.search(r"NEEDS (FIXES|CHANGES)", rest):
                return "needs_fixes"
            if re.search(r"READY TO (COMMIT|MERGE)|APPROVED", rest):
                return "ready"
            return "unknown"
    return "unknown"


# ── LangGraph state ─────────────────────────────────────────────────────────────

class ReviewState(TypedDict):
    plan: str
    implementation: str
    rubocop_log: str
    rspec_log: str
    stack: str                  # rails, generic or a stacks.<name> profile: names the two logs
    criteria: dict[str, str]    # the stack's checklist: dimension -> what its reviewer checks
    expertise: str              # "a senior <expertise> engineer" (the checklist's Expertise line)
    refute_how: str | None      # the checklist's refute section; None = verify.HOW_TO_WORK
    git_diff: str
    repo_root: str
    base_branch: str
    testing_plan: str
    testing_plan_enabled: bool  # review.dimensions.testing_plan; False = no testing-plan reviewer
    frontend_files: list[str]   # changed views/JS; empty = no frontend reviewer
    frontend_lint_log: str
    system_specs: bool          # the repo has spec/system
    browser_check: str          # browser-check.md from Phase 3.1, when it ran
    amendments: list[str]
    review_output: str
    sentinel: str
    review_cmd: str
    findings: Annotated[list[str], operator.add]
    verified: list[str]  # findings after verify_findings: confirmed, demoted or removed
    refuted: list[dict]  # what verification disproved, for the appendix and review.json
    verdict: str
    review_meta: dict   # structured fields for review.json, set by synthesize_verdict
    rerun: list[str]    # dimensions to review this pass; the others were carried over


class DimensionInput(TypedDict):
    dimension: str
    description: str
    plan: str
    implementation: str
    rubocop_log: str
    rspec_log: str
    stack: str
    expertise: str
    git_diff: str
    testing_plan: str
    frontend_files: list[str]
    frontend_lint_log: str
    browser_check: str
    amendments: list[str]


# ── Nodes ───────────────────────────────────────────────────────────────────────

def load_inputs(state: ReviewState) -> dict:
    print("  ▶ Loading inputs + git diff", flush=True)
    try:
        diff = subprocess.run(
            f"git diff {shlex.quote(state['base_branch'])}",
            shell=True, capture_output=True, text=True,
            cwd=state["repo_root"], timeout=30,
        ).stdout[:10000]   # what review_dimension uses; reviewers can open files for more
    except Exception:
        diff = "(could not get git diff)"
    return {"git_diff": diff}


def active_dimensions(state: Mapping[str, Any]) -> list[tuple[str, str]]:
    """The checklist's reviewers (state["criteria"], else the rails checklist) without the
    testing-plan reviewer when review.dimensions.testing_plan is off, plus the frontend
    reviewer when the diff touches views or JS."""
    criteria = state.get("criteria") or dict(REVIEW_DIMENSIONS)
    dims = [(d, criteria[d]) for d in checklists_lib.REVIEW_REQUIRED
            if d != "testing_plan" or state.get("testing_plan_enabled", True)]
    if state.get("frontend_files"):
        dims.append((FRONTEND_DIMENSION[0], frontend_criteria(state.get("system_specs", False), criteria.get("frontend"))))
    return dims


def route_to_reviewers(state: ReviewState) -> list[Send]:
    dims = active_dimensions(state)
    rerun = state.get("rerun") or [d for d, _ in dims]
    carried = [d for d, _ in dims if d not in rerun]
    print(f"  ▶ Fanning out {len(rerun)} specialist reviewer(s) in parallel"
          + (f" ({', '.join(carried)} clean last time, carried over)" if carried else ""), flush=True)
    return [
        Send("review_dimension", {
            "dimension": dim,
            "description": desc,
            "plan": state["plan"],
            "implementation": state["implementation"],
            "rubocop_log": state["rubocop_log"],
            "rspec_log": state["rspec_log"],
            "stack": state.get("stack", "rails"),
            "expertise": state.get("expertise", "Rails"),
            "git_diff": state["git_diff"],
            "testing_plan": state["testing_plan"],
            "frontend_files": state.get("frontend_files", []),
            "frontend_lint_log": state.get("frontend_lint_log", ""),
            "browser_check": state.get("browser_check", ""),
            "amendments": state.get("amendments", []),
        })
        for dim, desc in dims if dim in rerun
    ]


def review_dimension(state: DimensionInput) -> dict:
    dim = state["dimension"]
    print(f"    ◦ {dim}  ({gateway.model_for(f'review_{dim}') or 'default'})", flush=True)
    testing_plan_block = ""
    if dim == "testing_plan":
        content = state.get("testing_plan") or "(no testing-plan.md found for this task)"
        testing_plan_block = f"\n## Testing plan document (full)\n{content}\n"
    if dim == FRONTEND_DIMENSION[0]:
        files = "\n".join(f"- {f}" for f in state.get("frontend_files", []))
        testing_plan_block = (
            f"\n## Changed views and JavaScript\n{files}\n"
            f"\n## Frontend lint results (erb_lint / eslint)\n{state.get('frontend_lint_log', '')[:1500] or '(not run)'}\n"
        )
        if state.get("browser_check"):
            testing_plan_block += (
                f"\n## Browser check report (the agent walked the testing plan in a real browser)\n"
                f"{state['browser_check'][:3000]}\n"
                "A FAIL step or a JavaScript console error is 🔴 Critical unless the report shows it is an "
                "environment problem (server not running, seed data missing), which is a 🟡 Warning.\n"
            )
    evidence_field = "" if dim in verify.SKIP_DIMENSIONS else f"  - {verify.EVIDENCE_LINE}\n"
    grounding = (f"You have read-only tools (Read, Grep, Glob) on the repository, and nothing else. Open a file "
                 f"whenever a claim needs it, and no more: each costs time and tokens.\n"
                 if dim in verify.SKIP_DIMENSIONS else
                 verify.GROUNDING + "\nEach file you open costs time and tokens: open what a claim needs, no more.")
    lint_label, test_label = checklists_lib.log_labels(state.get("stack", "rails"))
    prompt = f"""You are a senior {state.get('expertise', 'Rails')} engineer doing a focused code review.
Your dimension: **{dim}**

Criteria: {state['description']}

## Plan (first 2500 chars)
{state['plan'][:2500]}
{amendments_block(state.get('amendments') or [], 4000)}{testing_plan_block}
## Implementation notes
{state['implementation'][:2000]}

## Git diff (first 10k chars)
{state['git_diff'][:10000]}

## {lint_label} results
{state['rubocop_log'][:1500]}

## {test_label} results
{state['rspec_log'][:1500]}

Review for **{dim}** only. Report each finding as a block:

- 🔴 **Critical** (or 🟡 **Warning**, or 🟢 **Note**) · `path/file.rb:N` · <short title>
  - **Problem:** <what is wrong, and what it causes in practice>
{evidence_field}  - **Fix:** <the change you recommend, concrete enough to apply>

Severity:
- 🔴 **Critical** — will break in production or is a security risk
- 🟡 **Warning** — likely issue under load or edge cases
- 🟢 **Note** — low severity, worth being aware of

If nothing found, output exactly: ✅ No issues in this dimension.
Do not repeat findings that other dimensions would cover.

{grounding}"""

    try:
        result = call_agent(prompt, f"review_{dim}", label=f"review:{dim}").text
    except Exception as e:
        if "max-budget-usd" in str(e):
            # A runaway reviewer, not a defect in the code: say so, without blocking the gate.
            result = (f"🟡 **Warning**: the {dim} reviewer stopped at its budget cap "
                      f"(budget.review_call_max_usd) before finishing; re-run the review, or raise the cap: {e}")
        else:
            result = f"🔴 **Critical** — reviewer failed: {e}"
    return {"findings": [f"### {dim}\n{result}"]}


def reviewed(state: ReviewState) -> list[str]:
    """The reviewers' findings after verification, or as reported when it did not run."""
    return state.get("verified") or state["findings"]


def verify_findings(state: ReviewState) -> dict:
    """Evidence gate and refuter over every dimension's findings, before the synthesis sees them."""
    intent = "\n".join([state["plan"], state["implementation"], *state.get("amendments", [])])
    context = ("\nThe change under review is in the repository working tree (uncommitted or on this branch). "
               f"The approved plan, for intent:\n{state['plan'][:2500]}\n")
    blocks, refuted = verify.verify_blocks(
        state["findings"],
        ask=lambda prompt, label: call_agent(prompt, verify.REFUTE_ROLE, label=label).text,
        read_file=verify.make_reader(state["repo_root"]),
        extra=f"{state['git_diff']}\n{intent}",
        context=context,
        expertise=state.get("expertise", "Rails"),
        how_to_work=state.get("refute_how"),
    )
    return {"verified": blocks, "refuted": refuted}


def synth_frontend_block(state: ReviewState) -> str:
    """Frontend lint results and the browser check's Result line for the verdict
    synthesis, when the diff touched views or JavaScript; "" otherwise."""
    if not state.get("frontend_files"):
        return ""
    block = f"\n## Frontend lint (erb_lint / eslint)\n{state.get('frontend_lint_log', '')[:800] or '(not run)'}\n"
    result = next((l for l in state.get("browser_check", "").splitlines() if l.startswith("Result:")), "")
    if result:
        block += f"\n## Browser check\n{result}\n"
    return block


def synth_testing_plan_block(state: ReviewState) -> str:
    """The verdict synthesis has no tools and review.md tells it a missing testing plan is a
    NEEDS FIXES; when the reviewer was switched off, say so instead of leaving it to guess."""
    if state.get("testing_plan_enabled", True):
        return ""
    return ("\n## Testing plan\nThe testing-plan reviewer did not run (review.dimensions.testing_plan is off). "
            "Mark the testing-plan checklist item N/A and do not base the verdict on the testing plan.\n")


def synthesize_verdict(state: ReviewState) -> dict:
    print(f"  ▶ Synthesising verdict from {len(state['findings'])} reviews", flush=True)
    findings_text = "\n\n".join(reviewed(state))
    refuted = state.get("refuted") or []
    lint_label, test_label = checklists_lib.log_labels(state.get("stack", "rails"))
    base_prompt = f"""## Review format
{state['review_cmd']}

## Plan summary (first 1500 chars)
{state['plan'][:1500]}
{amendments_block(state.get('amendments') or [], 2000)}
## Specialist findings
{findings_text}

## {lint_label}
{state['rubocop_log'][:800]}

## {test_label}
{state['rspec_log'][:800]}
{synth_frontend_block(state)}{synth_testing_plan_block(state)}
Synthesise these findings into a single review document following the format above.
De-duplicate overlapping findings. Assign the final verdict:
- READY TO COMMIT — no critical issues
- NEEDS FIXES — one or more critical issues or significant warnings remain"""

    # Preferred path: schema-validated structured output. The verdict the commit
    # gate reads is then a typed enum and the findings are a real list — no regex
    # over prose. `document` is the same human review as before.
    structured_prompt = base_prompt + """

Return a JSON object matching the provided schema:
- `verdict`: exactly "READY TO COMMIT" or "NEEDS FIXES"
- `reason`: the single factor that decided it (1-3 sentences)
- `findings`: every de-duplicated finding as an object (severity, dimension, file, line, problem, fix)
- `document`: the COMPLETE review document in the format above, as markdown, with the same verdict"""
    if not gateway.supports("structured_output"):
        print(f"  ▶ {gateway.AGENT.name} backend has no structured output - text synthesis + markdown verdict", flush=True)
    try:
        if not gateway.supports("structured_output"):
            raise RuntimeError("structured output unsupported on this backend")
        res = call_agent(structured_prompt, label="synthesize_verdict", schema=REVIEW_SCHEMA, role="verdict")
        s = res.structured if isinstance(res.structured, dict) else None
        if s and s.get("verdict") in VERDICT_KEY and s.get("document"):
            meta = {
                "verdict": VERDICT_KEY[s["verdict"]],
                "verdict_label": s["verdict"],
                "reason": s.get("reason", ""),
                "findings": s.get("findings") or [],
                "source": "structured",
            }
            return {"verdict": verify.append_refuted(s["document"], refuted), "review_meta": meta}
        print("  ⚠ Structured verdict missing or malformed — falling back to text synthesis", flush=True)
    except Exception as e:
        if gateway.supports("structured_output"):
            print(f"  ⚠ Structured synthesis failed ({str(e)[:160]}) — falling back to text synthesis", flush=True)

    # Fallback: plain document, verdict and counts derived deterministically from it.
    try:
        doc = call_agent(base_prompt + "\n\nOutput only the review document.",
                          label="synthesize_verdict:text", role="verdict").text
    except Exception as e:
        doc = f"# Review\n\n## Verdict: NEEDS FIXES\n\nReview synthesis failed: {e}"
    meta = {
        "verdict": verdict_from_markdown(doc),
        "verdict_label": "",
        "reason": "",
        "findings": [],
        "source": "markdown",
    }
    return {"verdict": verify.append_refuted(doc, refuted), "review_meta": meta}


def write_review(state: ReviewState) -> dict:
    print("  ▶ Writing review", flush=True)

    review_path = Path(state["review_output"])
    review_path.parent.mkdir(parents=True, exist_ok=True)
    review_path.write_text(state["verdict"])
    print(f"    ✔ review.md → {review_path}", flush=True)

    # review.json — the machine-readable envelope the commit gate reads.
    meta = state.get("review_meta") or {"verdict": verdict_from_markdown(state["verdict"]), "findings": [], "source": "markdown"}
    findings = meta.get("findings") or []
    if findings:
        counts = {sev: sum(1 for f in findings if f.get("severity") == sev) for sev in ("critical", "warning", "note")}
    else:
        counts = gateway.severity_counts(state["verdict"])
    dimensions = {}
    for block in reviewed(state):
        head, _, body = block.partition("\n")
        dim = head.lstrip("# ").strip()
        dimensions[dim] = {**gateway.severity_counts(body), "failed": "reviewer failed" in body}
    ledger_path = os.environ.get(gateway.LEDGER_ENV)
    own_records = [r for r in gateway.read_ledger(ledger_path) if r.get("tool") == "migite-review"] if ledger_path else []
    envelope = {
        **gateway.envelope_base("migite-review"),
        "base_branch": state["base_branch"],
        "verdict": meta.get("verdict", "unknown"),
        "verdict_label": meta.get("verdict_label", ""),
        "reason": meta.get("reason", ""),
        "counts": counts,
        "findings": findings,
        "refuted": state.get("refuted") or [],
        "dimensions": dimensions,
        "source": meta.get("source", "markdown"),
        "outputs": {"review": str(review_path)},
        "usage": gateway.summarize(own_records)["total"] if own_records else None,
    }
    json_path = review_path.with_name("review.json")
    gateway.write_json(json_path, envelope)
    # Each dimension's own output, for a later re-review to carry clean ones over.
    gateway.write_json(review_path.with_name("review-dimensions.json"), {
        "dimensions": {head.lstrip("# ").strip(): body.strip() for head, _, body in
                       (block.partition("\n") for block in reviewed(state))},
        "testing_plan_sha": _sha(state["testing_plan"]),
    })
    print(f"    ✔ review.json → {json_path}  (verdict={envelope['verdict']}, "
          f"{counts['critical']}🔴 {counts['warning']}🟡 {counts['note']}🟢, source={envelope['source']})", flush=True)

    Path(state["sentinel"]).touch()
    print("    ✔ sentinel written", flush=True)
    return {}


# ── Graph ────────────────────────────────────────────────────────────────────────

def build_graph() -> StateGraph:
    g = StateGraph(ReviewState)
    g.add_node("load_inputs", load_inputs)
    g.add_node("review_dimension", review_dimension)
    g.add_node("verify_findings", verify_findings)
    g.add_node("synthesize_verdict", synthesize_verdict)
    g.add_node("write_review", write_review)

    g.add_edge(START, "load_inputs")
    g.add_conditional_edges("load_inputs", route_to_reviewers, ["review_dimension"])
    g.add_edge("review_dimension", "verify_findings")
    g.add_edge("verify_findings", "synthesize_verdict")
    g.add_edge("synthesize_verdict", "write_review")
    g.add_edge("write_review", END)
    return g.compile()


# ── CLI ──────────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description="migite-review: autonomous LangGraph reviewer")
    ap.add_argument("--plan",            required=True)
    ap.add_argument("--implementation",  required=True)
    ap.add_argument("--rubocop-log",     required=True)
    ap.add_argument("--rspec-log",       required=True)
    ap.add_argument("--repo-root",       required=True)
    ap.add_argument("--review-output",   required=True)
    ap.add_argument("--sentinel",        required=True)
    ap.add_argument("--base-branch",     default=None, help="Branch to diff against (default: auto-detect origin/HEAD, then main/master/develop)")
    ap.add_argument("--stack",           default="rails", help="Detected stack (rails, generic or a stacks.<name> profile): names the two logs")
    ap.add_argument("--testing-plan",    default="", help="Path to testing-plan.md (optional)")
    ap.add_argument("--frontend-files",  default="", help="Space-separated changed views/JS; adds the frontend reviewer (optional)")
    ap.add_argument("--frontend-lint-log", default="", help="Path to the erb_lint/eslint log (optional)")
    ap.add_argument("--system-specs",    action="store_true", help="The repo has spec/system (shapes the frontend reviewer's system-spec rule)")
    ap.add_argument("--browser-check",   default="", help="Path to browser-check.md from Phase 3.1 (optional)")
    ap.add_argument("--amendment",       action="append", default=[],
                    help="Path to an amendment-NN.md; repeat for each, oldest first (optional)")
    ap.add_argument("--previous",        default="",
                    help="review-dimensions.json from the last review: re-run only what dims_to_rerun picks (optional)")
    args = ap.parse_args()

    global REVIEW_CMD_PATH
    try:
        cfg = config.load(args.repo_root)
    except config.ConfigError as e:
        print(f"  ✘ migite-review: config error: {e}", file=sys.stderr, flush=True)
        sys.exit(1)
    for w in cfg.warnings:
        print(f"  ⚠ config: {w}", flush=True)
    gateway.configure_from(cfg)
    try:
        gateway.require_cli()
    except gateway.AgentError as e:
        print(f"  ✘ migite-review: {e}", file=sys.stderr, flush=True)
        sys.exit(1)
    REVIEW_CMD_PATH = cfg.prompt_path("review", MIGITE_HOME, args.repo_root)
    try:
        checklist = checklists_lib.load(args.stack, MIGITE_HOME, cfg.override_dir("prompts", args.repo_root))
    except checklists_lib.ChecklistError as e:
        print(f"  ✘ migite-review: {e}", file=sys.stderr, flush=True)
        sys.exit(1)

    base_branch = args.base_branch or paths.detect_base_branch(args.repo_root)

    def read_or_empty(path: str) -> str:
        p = Path(path)
        return p.read_text() if p.exists() else ""

    plan           = read_or_empty(args.plan)
    implementation = read_or_empty(args.implementation)
    rubocop_log    = read_or_empty(args.rubocop_log)
    rspec_log      = read_or_empty(args.rspec_log)
    testing_plan   = read_or_empty(args.testing_plan) if args.testing_plan else ""
    frontend_files = args.frontend_files.split()
    frontend_lint_log = read_or_empty(args.frontend_lint_log) if args.frontend_lint_log else ""
    browser_check  = read_or_empty(args.browser_check) if args.browser_check else ""
    amendments     = [read_or_empty(a) for a in args.amendment]
    review_cmd     = read_prompt(REVIEW_CMD_PATH)

    testing_plan_enabled = str(cfg.get("review.dimensions.testing_plan", "on")) != "off"
    dim_names = [d for d, _ in active_dimensions({"frontend_files": frontend_files, "criteria": checklist.review,
                                                  "testing_plan_enabled": testing_plan_enabled})]
    dims = "  ".join(f"{d}={gateway.model_for(f'review_{d}') or 'default'}" for d in dim_names)
    rerun = list(dim_names)
    carried: list[str] = []
    previous_path = Path(args.previous) if args.previous else None
    if previous_path and previous_path.is_file():
        try:
            previous = json.loads(previous_path.read_text())
        except ValueError:
            previous = {}
        rerun = dims_to_rerun(previous, testing_plan, dim_names)
        carried = carried_findings(previous, rerun, dim_names)

    print(f"\n  migite-review | {dims}  refute={gateway.model_for(verify.REFUTE_ROLE) or 'default'}  verdict={gateway.model_for('verdict') or 'default'}  base branch: {base_branch}", flush=True)
    print(f"  migite-review | checklist: {' + '.join(checklist.files)}", flush=True)

    graph = build_graph()
    try:
        graph.invoke({
            "plan": plan,
            "implementation": implementation,
            "rubocop_log": rubocop_log,
            "rspec_log": rspec_log,
            "stack": args.stack,
            "criteria": checklist.review,
            "expertise": checklist.expertise,
            "refute_how": checklist.refute,
            "git_diff": "",
            "repo_root": args.repo_root,
            "base_branch": base_branch,
            "testing_plan": testing_plan,
            "testing_plan_enabled": testing_plan_enabled,
            "frontend_files": frontend_files,
            "frontend_lint_log": frontend_lint_log,
            "system_specs": args.system_specs,
            "browser_check": browser_check,
            "amendments": amendments,
            "review_output": args.review_output,
            "sentinel": args.sentinel,
            "review_cmd": review_cmd,
            "findings": carried,
            "verified": [],
            "refuted": [],
            "verdict": "",
            "review_meta": {},
            "rerun": rerun,
        })
        print("\n  ✔ migite-review complete", flush=True)
    except Exception as e:
        print(f"\n  ✘ migite-review failed: {e}", file=sys.stderr, flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
