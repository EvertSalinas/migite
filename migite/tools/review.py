#!/usr/bin/env python3
# migite.tools.review (migite-review) — autonomous LangGraph review agent
#
# Reads plan.md, implementation notes, rubocop/rspec logs, and git diff.
# Fans out 4 parallel specialist reviewers (5 when the diff touches views or
# JavaScript: see FRONTEND_DIMENSION), synthesises a verdict, and writes
# review.md + sentinel. Called by migite's spawn_langgraph().
#
# Usage:
#   python -m migite.tools.review --plan <file> --implementation <file> \
#                 --rubocop-log <file> --rspec-log <file> \
#                 --repo-root <dir> --review-output <file> --sentinel <file>

import argparse
import operator
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from migite import gateway
from migite import config
from migite import paths

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

REVIEW_DIMENSIONS = [
    (
        "correctness",
        "Implementation matches the approved plan. No scope creep. All acceptance criteria covered. "
        "Logic is correct. No dead code or commented-out blocks.",
    ),
    (
        "security",
        "All controller actions authorised. Resources scoped to current_user. No N+1 queries "
        "(check .each over AR collections). No raw SQL without parameterisation. No hardcoded secrets. "
        "Strong params on every action. No SQL injection vectors. In views: no user-supplied content "
        "passed through html_safe, raw or <%== %>. Turbo Stream broadcasts (broadcasts_to, "
        "broadcast_*_to, turbo_stream_from) are scoped to the user or account allowed to see them, "
        "never a shared stream name that carries private data.",
    ),
    (
        "test_coverage",
        "New public methods have unit specs. New endpoints have request specs covering success, 401, 422. "
        "Actions that respond with turbo_stream have request specs asserting the <turbo-stream> action and target. "
        "Factories used (not fixtures). No real HTTP calls in specs. Spec descriptions use 'when/with/without' for context blocks.",
    ),
    (
        "testing_plan",
        "The testing plan document exists and is complete: contains a real Rails console seed script "
        "(generic emails only), step-by-step verification actions (curl or browser steps; for UI changes, "
        "the page, the exact action and what should change on the page), log lines to grep, "
        "and a teardown script. A missing, placeholder, or empty testing plan is a critical failure. If this "
        "task had any amendments, the testing plan must reflect the CURRENT amended behaviour, not just the "
        "original plan — flag any step that still describes pre-amendment behaviour.",
    ),
]


# Runs only when migite passes --frontend-files (lib/stack.sh changed_frontend_files
# found views or JavaScript in the diff), so a backend-only review stays at four
# reviewers. XSS and broadcast scoping live under security; this is the Hotwire
# mechanics that break silently in the browser rather than in a spec.
FRONTEND_DIMENSION = (
    "frontend",
    "Hotwire and Stimulus correctness in the changed views and JavaScript. Form submissions that fail "
    "validation render with status :unprocessable_entity (422), and redirects after a successful non-GET "
    "use :see_other (303); otherwise Turbo won't show the errors or follow the redirect. Every "
    "turbo_frame_tag id and turbo_stream target the diff references exists in the rendered markup, with "
    "dom_id used the same way on both sides. Stimulus controllers are registered (importmap pin or "
    "controllers/index.js), and data-controller, data-<name>-target, data-<name>-value and data-action "
    "names match the controller's file name and its static targets/values. No inline <script> or on* "
    "attribute handlers. Partials rendered per row don't query per row (N+1 in collection rendering). "
    "Frontend lint problems that remain (log below) are warnings unless they break the page.",
)


def frontend_criteria(system_specs: bool) -> str:
    """FRONTEND_DIMENSION's criteria plus the system-spec rule, which depends on
    whether the repo already has spec/system: demanding a first system spec (and
    a browser driver) as part of an unrelated change is scope creep."""
    if system_specs:
        rule = ("The repo has system specs (spec/system): a new interactive flow (Stimulus behaviour, "
                "Turbo frame navigation, a form that updates the page) with no system spec is a 🟡 Warning.")
    else:
        rule = ("The repo has no system specs: do not demand one, but add a 🟢 Note when a new "
                "interactive flow is covered only by the testing plan's manual steps.")
    return f"{FRONTEND_DIMENSION[1]} {rule}"


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
    git_diff: str
    repo_root: str
    base_branch: str
    testing_plan: str
    frontend_files: list[str]   # changed views/JS; empty = no frontend reviewer
    frontend_lint_log: str
    system_specs: bool          # the repo has spec/system
    browser_check: str          # browser-check.md from Phase 3.1, when it ran
    review_output: str
    sentinel: str
    review_cmd: str
    findings: Annotated[list[str], operator.add]
    verdict: str
    review_meta: dict   # structured fields for review.json, set by synthesize_verdict


class DimensionInput(TypedDict):
    dimension: str
    description: str
    plan: str
    implementation: str
    rubocop_log: str
    rspec_log: str
    git_diff: str
    testing_plan: str
    frontend_files: list[str]
    frontend_lint_log: str
    browser_check: str


# ── Nodes ───────────────────────────────────────────────────────────────────────

def load_inputs(state: ReviewState) -> dict:
    print("  ▶ Loading inputs + git diff", flush=True)
    try:
        diff = subprocess.run(
            f"git diff {shlex.quote(state['base_branch'])}",
            shell=True, capture_output=True, text=True,
            cwd=state["repo_root"], timeout=30,
        ).stdout[:14000]
    except Exception:
        diff = "(could not get git diff)"
    return {"git_diff": diff}


def active_dimensions(state: ReviewState) -> list[tuple[str, str]]:
    """REVIEW_DIMENSIONS, plus the frontend reviewer when the diff touches views or JS."""
    dims = list(REVIEW_DIMENSIONS)
    if state.get("frontend_files"):
        dims.append((FRONTEND_DIMENSION[0], frontend_criteria(state.get("system_specs", False))))
    return dims


def route_to_reviewers(state: ReviewState) -> list[Send]:
    dims = active_dimensions(state)
    print(f"  ▶ Fanning out {len(dims)} specialist reviewers in parallel", flush=True)
    return [
        Send("review_dimension", {
            "dimension": dim,
            "description": desc,
            "plan": state["plan"],
            "implementation": state["implementation"],
            "rubocop_log": state["rubocop_log"],
            "rspec_log": state["rspec_log"],
            "git_diff": state["git_diff"],
            "testing_plan": state["testing_plan"],
            "frontend_files": state.get("frontend_files", []),
            "frontend_lint_log": state.get("frontend_lint_log", ""),
            "browser_check": state.get("browser_check", ""),
        })
        for dim, desc in dims
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
    prompt = f"""You are a senior Rails engineer doing a focused code review.
Your dimension: **{dim}**

Criteria: {state['description']}

## Plan (first 2500 chars)
{state['plan'][:2500]}
{testing_plan_block}
## Implementation notes
{state['implementation'][:2000]}

## Git diff (first 10k chars)
{state['git_diff'][:10000]}

## Rubocop results
{state['rubocop_log'][:1500]}

## RSpec results
{state['rspec_log'][:1500]}

Review for **{dim}** only. List each finding with severity:
- 🔴 **Critical** — will break in production or is a security risk
- 🟡 **Warning** — likely issue under load or edge cases
- 🟢 **Note** — low severity, worth being aware of

If nothing found, output exactly: ✅ No issues in this dimension.
Do not repeat findings that other dimensions would cover."""

    try:
        result = call_agent(prompt, f"review_{dim}", label=f"review:{dim}").text
    except Exception as e:
        result = f"🔴 **Critical** — reviewer failed: {e}"
    return {"findings": [f"### {dim}\n{result}"]}


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


def synthesize_verdict(state: ReviewState) -> dict:
    print(f"  ▶ Synthesising verdict from {len(state['findings'])} reviews", flush=True)
    findings_text = "\n\n".join(state["findings"])
    base_prompt = f"""## Review format
{state['review_cmd']}

## Plan summary (first 1500 chars)
{state['plan'][:1500]}

## Specialist findings
{findings_text}

## Rubocop
{state['rubocop_log'][:800]}

## RSpec
{state['rspec_log'][:800]}
{synth_frontend_block(state)}
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
            return {"verdict": s["document"], "review_meta": meta}
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
    return {"verdict": doc, "review_meta": meta}


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
    for block in state["findings"]:
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
        "dimensions": dimensions,
        "source": meta.get("source", "markdown"),
        "outputs": {"review": str(review_path)},
        "usage": gateway.summarize(own_records)["total"] if own_records else None,
    }
    json_path = review_path.with_name("review.json")
    gateway.write_json(json_path, envelope)
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
    g.add_node("synthesize_verdict", synthesize_verdict)
    g.add_node("write_review", write_review)

    g.add_edge(START, "load_inputs")
    g.add_conditional_edges("load_inputs", route_to_reviewers, ["review_dimension"])
    g.add_edge("review_dimension", "synthesize_verdict")
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
    ap.add_argument("--testing-plan",    default="", help="Path to testing-plan.md (optional)")
    ap.add_argument("--frontend-files",  default="", help="Space-separated changed views/JS; adds the frontend reviewer (optional)")
    ap.add_argument("--frontend-lint-log", default="", help="Path to the erb_lint/eslint log (optional)")
    ap.add_argument("--system-specs",    action="store_true", help="The repo has spec/system (shapes the frontend reviewer's system-spec rule)")
    ap.add_argument("--browser-check",   default="", help="Path to browser-check.md from Phase 3.1 (optional)")
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
    review_cmd     = read_prompt(REVIEW_CMD_PATH)

    dim_names = [d for d, _ in REVIEW_DIMENSIONS] + ([FRONTEND_DIMENSION[0]] if frontend_files else [])
    dims = "  ".join(f"{d}={gateway.model_for(f'review_{d}') or 'default'}" for d in dim_names)
    print(f"\n  migite-review | {dims}  verdict={gateway.model_for('verdict') or 'default'}  base branch: {base_branch}", flush=True)

    graph = build_graph()
    try:
        graph.invoke({
            "plan": plan,
            "implementation": implementation,
            "rubocop_log": rubocop_log,
            "rspec_log": rspec_log,
            "git_diff": "",
            "repo_root": args.repo_root,
            "base_branch": base_branch,
            "testing_plan": testing_plan,
            "frontend_files": frontend_files,
            "frontend_lint_log": frontend_lint_log,
            "system_specs": args.system_specs,
            "browser_check": browser_check,
            "review_output": args.review_output,
            "sentinel": args.sentinel,
            "review_cmd": review_cmd,
            "findings": [],
            "verdict": "",
            "review_meta": {},
        })
        print("\n  ✔ migite-review complete", flush=True)
    except Exception as e:
        print(f"\n  ✘ migite-review failed: {e}", file=sys.stderr, flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
