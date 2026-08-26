#!/usr/bin/env python3
# migite-audit — Autonomous codebase audit using LangGraph
#
# Fans out parallel specialist auditors across the codebase, synthesises
# findings ranked by severity, and writes an audit report.
# Called by the migite-audit bash wrapper.

import argparse
import glob
import operator
import os
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

import migite_paths

EXPLORE_MODEL = "claude-haiku-4-5-20251001"
THINK_MODEL   = "claude-sonnet-5"

VAULT_BASE = os.environ.get("DEV_LOG_BASE", str(Path.home() / "dev-log"))

AUDIT_AREAS = [
    (
        "models",
        ["app/models/**/*.rb"],
        """\
- N+1 risks: associations loaded lazily inside loops or serializers
- Callbacks with side effects (API calls, jobs enqueued) that break idempotency
- Missing `dependent:` on has_many (orphan records risk)
- Scopes that can return unbounded result sets
- Validations that silently fail in bulk operations""",
    ),
    (
        "controllers",
        ["app/controllers/**/*.rb"],
        """\
- Actions missing authorization (Pundit policy, CanCanCan, or manual check)
- Collections or records not scoped to current_user / current_account
- Business logic inline in action bodies (belongs in a service)
- Actions accepting raw params — missing strong params
- Before-action filters with `:only`/`:except` that can be bypassed""",
    ),
    (
        "services",
        ["app/services/**/*.rb", "app/interactors/**/*.rb", "app/commands/**/*.rb"],
        """\
- Multiple DB writes outside a transaction (partial write risk)
- Exceptions rescued and swallowed silently
- God objects — services doing more than one thing
- Methods with no clear return value / result object contract""",
    ),
    (
        "serializers",
        ["app/serializers/**/*.rb"],
        """\
- Associations accessed without eager loading (N+1 in serializer)
- Sensitive fields exposed (tokens, internal IDs, password digests)
- Attributes that bypass authorization checks""",
    ),
    (
        "jobs",
        ["app/jobs/**/*.rb", "app/workers/**/*.rb"],
        """\
- Non-idempotent perform method (retrying changes state incorrectly)
- Heavy business logic inline in perform (should delegate to a service)
- Missing explicit queue_as
- Jobs that fan out more jobs without deduplication
- Raised exceptions not logged before re-raise""",
    ),
    (
        "migrations",
        ["db/migrate/*.rb"],
        """\
- Irreversible operations in `change` with no `up`/`down`
- NOT NULL column added to existing table with no default or data migration
- Foreign key added without a corresponding index
- Destructive operations (column drops, renames) missing a phased deployment plan""",
    ),
    (
        "schema_indexes",
        ["db/schema.rb"],
        """\
- Foreign key columns (ending in _id) without a matching index
- Columns likely used in .where / .order (status, type, state, role, created_at) without indexes
- Polymorphic type+id pairs missing a composite index
- Unique constraint candidates (email, token, slug) missing a unique index""",
    ),
]


# ── Claude call ─────────────────────────────────────────────────────────────────

def call_claude(prompt: str, model: str = THINK_MODEL) -> str:
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


# ── File reading ─────────────────────────────────────────────────────────────────

def read_area_files(patterns: list[str], repo_root: str, max_chars: int = 14000) -> str:
    all_files: list[str] = []
    for p in patterns:
        all_files.extend(glob.glob(f"{repo_root}/{p}", recursive=True))
    all_files = sorted(set(all_files))

    listing = "\n".join(f.replace(f"{repo_root}/", "") for f in all_files) or "(none)"
    parts = [f"## File listing\n{listing}\n\n## File contents"]
    total = len(parts[0])

    for f in all_files:
        try:
            content = Path(f).read_text(errors="replace")[:2500]
            rel = f.replace(f"{repo_root}/", "")
            chunk = f"\n### {rel}\n```ruby\n{content}\n```"
            if total + len(chunk) > max_chars:
                break
            parts.append(chunk)
            total += len(chunk)
        except Exception:
            pass
    return "\n".join(parts)


# ── State ────────────────────────────────────────────────────────────────────────

class AuditState(TypedDict):
    repo_root: str
    repo_name: str
    focus: str
    output: str
    findings: Annotated[list[str], operator.add]
    report: str


class AuditAreaInput(TypedDict):
    area: str
    glob_patterns: list[str]
    checks: str
    repo_root: str
    focus: str


# ── Nodes ─────────────────────────────────────────────────────────────────────────

def load_context(state: AuditState) -> dict:
    print(f"  ▶ Auditing {state['repo_name']}", flush=True)
    return {}


def route_to_auditors(state: AuditState) -> list[Send]:
    areas = AUDIT_AREAS
    if state["focus"]:
        focus_lower = state["focus"].lower()
        areas = [(a, p, c) for a, p, c in areas if focus_lower in a.lower()]
        if not areas:
            print(f"  ⚠ No areas matched focus '{state['focus']}' — auditing all", flush=True)
            areas = AUDIT_AREAS
    print(f"  ▶ Dispatching {len(areas)} parallel auditors", flush=True)
    return [
        Send("audit_area", {
            "area": area,
            "glob_patterns": patterns,
            "checks": checks,
            "repo_root": state["repo_root"],
            "focus": state["focus"],
        })
        for area, patterns, checks in areas
    ]


def audit_area(state: AuditAreaInput) -> dict:
    area = state["area"]
    print(f"    ◦ {area}", flush=True)
    context = read_area_files(state["glob_patterns"], state["repo_root"])
    focus_line = f"\nFocus: {state['focus']}\n" if state["focus"] else ""
    prompt = f"""You are a senior Rails architect auditing the **{area}** layer of an existing codebase.
You are looking at LIVE CODE — report only real issues you can see, not theoretical risks.{focus_line}

{context}

Check for the following:
{state['checks']}

For each finding output exactly:
- 🔴 **Critical** / 🟡 **Warning** / 🟢 **Note** — `path/to/file.rb` (approx line) — problem — fix

If nothing found output exactly: ✅ No issues in {area}.
No preamble. Findings only."""

    try:
        result = call_claude(prompt, model=EXPLORE_MODEL)
    except Exception as e:
        result = f"🔴 **Critical** — audit failed for {area}: {e}"
    return {"findings": [f"### {area}\n{result}"]}


def synthesize_report(state: AuditState) -> dict:
    print(f"  ▶ Synthesising findings from {len(state['findings'])} auditors", flush=True)
    findings_text = "\n\n".join(state["findings"])
    today = date.today().isoformat()
    prompt = f"""Synthesise codebase audit findings for **{state['repo_name']}** (date: {today}).

Raw findings from specialist auditors:
{findings_text}

Produce the final audit report:
1. Skip areas that reported ✅ No issues
2. Deduplicate findings reported by multiple auditors
3. Group remaining findings by severity — Critical first, then Warnings, then Notes
4. Keep file paths for every finding

Output format:
# Codebase Audit — {state['repo_name']}
Date: {today}

## Critical
- 🔴 `path/file.rb:N` — problem — fix

## Warnings
- 🟡 `path/file.rb:N` — problem — fix

## Notes
- 🟢 `path/file.rb:N` — problem — fix

## Summary
<one paragraph: overall codebase health and top priorities>

If all auditors found no issues:
# Codebase Audit — {state['repo_name']}
Date: {today}
✅ No architectural issues found.

Output only the report."""

    try:
        report = call_claude(prompt, model=THINK_MODEL)
    except Exception as e:
        report = f"# Codebase Audit — {state['repo_name']}\n\nSynthesis failed: {e}"
    return {"report": report}


def write_report(state: AuditState) -> dict:
    print(f"  ▶ Writing report", flush=True)
    out = Path(state["output"])
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(state["report"])
    print(f"    ✔ {out}", flush=True)

    # Print summary to terminal
    critical = state["report"].count("🔴")
    warnings = state["report"].count("🟡")
    notes    = state["report"].count("🟢")
    print(f"\n  Results: {critical} critical  {warnings} warnings  {notes} notes", flush=True)
    if critical > 0:
        print("  → Run: migite --type refactor  to open a refactor session", flush=True)
    return {}


# ── Graph ─────────────────────────────────────────────────────────────────────────

def build_graph() -> StateGraph:
    g = StateGraph(AuditState)
    g.add_node("load_context",    load_context)
    g.add_node("audit_area",      audit_area)
    g.add_node("synthesize_report", synthesize_report)
    g.add_node("write_report",    write_report)

    g.add_edge(START, "load_context")
    g.add_conditional_edges("load_context", route_to_auditors, ["audit_area"])
    g.add_edge("audit_area",      "synthesize_report")
    g.add_edge("synthesize_report", "write_report")
    g.add_edge("write_report",    END)
    return g.compile()


# ── CLI ───────────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description="migite-audit: autonomous codebase auditor")
    ap.add_argument("--repo-root", required=True)
    ap.add_argument("--focus",  default="", help="Limit audit to areas matching this keyword")
    ap.add_argument("--output", default="", help="Output file path (default: vault)")
    ap.add_argument("--id",     default="", help="Jira ticket key or URL — groups this audit under the ticket's existing folder")
    args = ap.parse_args()

    repo_name = Path(args.repo_root).name
    org       = migite_paths.detect_org(args.repo_root)
    today     = date.today().isoformat()
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")

    run_dir = None
    if not args.output and args.id:
        try:
            run_dir = migite_paths.resolve_run_dir(VAULT_BASE, org, repo_name, id=args.id)
        except migite_paths.InvalidRunKeyError as e:
            print(f"✘ Not a Jira ticket key: {e}", file=sys.stderr)
            sys.exit(1)
        except migite_paths.AmbiguousRunDirError as e:
            print(f"✘ Ambiguous run folder for '{e.slug}': {', '.join(e.candidates)}", file=sys.stderr)
            print("  Pass --output explicitly to disambiguate.", file=sys.stderr)
            sys.exit(1)

    output = args.output or (
        str(run_dir / f"audit-{timestamp}.md") if run_dir
        else str(Path(VAULT_BASE) / org / repo_name / f"audit-{today}.md")
    )

    print(f"\n  migite-audit | model: explore={EXPLORE_MODEL}  think={THINK_MODEL}", flush=True)
    print(f"  Repo: {org}/{repo_name}", flush=True)
    if args.focus:
        print(f"  Focus: {args.focus}", flush=True)

    graph = build_graph()
    try:
        graph.invoke({
            "repo_root": args.repo_root,
            "repo_name": repo_name,
            "focus":     args.focus,
            "output":    output,
            "findings":  [],
            "report":    "",
        })
        print(f"\n  ✔ migite-audit complete", flush=True)
    except Exception as e:
        print(f"\n  ✘ migite-audit failed: {e}", file=sys.stderr, flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
