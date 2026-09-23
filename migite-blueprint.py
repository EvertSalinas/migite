#!/usr/bin/env python3
# migite-blueprint — Project definition tool using LangGraph
#
# Fans out 5 parallel specialist analysts (domain, users, API, conventions, risks),
# synthesises a full project blueprint with Opus 5, then extracts sequenced milestone
# intake files + a knowledge.md seed. Called by the migite-blueprint bash wrapper.
#
# Stack-agnostic: a project has no existing code to read, so unlike migite-explore
# (which discovers a stack from files already in the repo) this tool determines one
# up front — either taken verbatim from --stack, or inferred from the brief by
# determine_stack() — and threads it through every analyst, synthesis, and
# extraction prompt instead of assuming Rails.
#
# Modes:
#   Full run:  --brief-file <path> --project-name <name> --output <dir> [--stack <stack>]
#   Re-extract: --from-blueprint <path> --output <dir>

import argparse
import operator
import os
import re
import sys
from datetime import date
from pathlib import Path
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

import migite_call
import migite_config
import migite_paths


VAULT_BASE = os.environ.get("DEV_LOG_BASE", str(Path.home() / "dev-log"))

ANALYSIS_DIMENSIONS = [
    (
        "domain_model",
        """\
Analyse the project brief and define the core domain model, idiomatic to the target stack
(see Stack context above):

- List every entity the system needs to persist. For each: name it the way it would actually
  appear in code for this stack (e.g. a PascalCase model class in Rails/Java, a snake_case
  table in raw SQL, a TypeScript interface), its role in the system, and 4-6 key attributes
  with types.
- Map the relationships in plain terms (one-to-one, one-to-many, many-to-many), plus the
  concrete construct the chosen stack uses for it (e.g. Rails `belongs_to`/`has_many`, a
  Prisma/TypeORM relation, a Django `ForeignKey`, a foreign key column).
- List the key business rules governing those entities: validations, state machines,
  uniqueness constraints, immutability rules.
- Flag any entities that need soft delete, audit logging, or versioning.
- Flag any shared-table inheritance or polymorphic association the domain seems to want,
  and whether the chosen stack's idioms favor using it or avoiding it.
- Identify any join/junction entities that will need their own business logic.

Be specific. Name entities and fields exactly as they would appear in this stack's code. Under 450 words.""",
    ),
    (
        "user_roles_and_flows",
        """\
Analyse the project brief and define the human side of the system:

- List every distinct user role (who interacts with the system and in what capacity).
- For each role, describe their 3 most critical journeys through the system as numbered steps.
- Separate must-have MVP flows from nice-to-have future flows.
- Flag any multi-tenancy, account hierarchy, or delegated-admin complexity.
- Identify any external actors: webhooks, third-party service callbacks, scheduled jobs acting on behalf of users.
- Note any flows that require asynchronous behaviour (user initiates → background job → notification).

Be concrete. Describe flows as step sequences. Under 450 words.""",
    ),
    (
        "api_surface",
        """\
Analyse the project brief and design the MVP API surface, idiomatic to the target stack
(see Stack context above):

- List all endpoints/operations needed for MVP, in whichever style fits the chosen stack
  (REST, GraphQL, RPC). Format: `METHOD /api/v1/path` or `operation Name` — one-line description.
- Specify the authentication mechanism appropriate to this stack (JWT, session cookies,
  a framework-specific auth library, API key, OAuth2) and say why it fits.
- Identify which endpoints/operations need authorization scoping (by account, by role, by ownership).
- Flag endpoints that trigger background jobs (async operations, webhooks, notifications).
- Note any non-standard operations required: bulk operations, search, webhooks received.
- Identify the 2-3 most complex response shapes (nested associations, computed fields, conditional fields).
- Call out any endpoints that are expensive and will need caching or rate limiting.

Format entries as: `METHOD /path` (or this stack's equivalent) — description. Under 450 words.""",
    ),
    (
        "tech_conventions",
        """\
Analyse the project brief and prescribe the technical conventions for this project, idiomatic
to the target stack (see Stack context above):

- Dependencies: list every library/package the project will need (auth, authorization,
  background jobs, serialization, pagination, file storage, monitoring, etc.) and one sentence
  on why each is the right choice for this stack.
- Patterns: decide on the business-logic organization pattern idiomatic to this stack
  (e.g. service objects/interactors in Rails, use-case classes, plain functions/hooks in a
  JS/TS stack). State which pattern and why.
- Scopes/guards: identify which entities need authorization scoping (by account, by user) and name them.
- N+1 / overfetching risks: identify the relationships most likely to cause N+1 queries or
  overfetching from the domain shape. Name the eager-loading or batching strategy required.
- Background jobs: list the queues/workers needed and what jobs belong in each.
- Database: call out non-obvious index needs, JSON column usage, or features specific to the
  chosen database.
- Error handling: define the error response format and how validation errors surface to the client.

Be prescriptive — this feeds directly into the project's knowledge.md. Under 450 words.""",
    ),
    (
        "risks_and_hard_parts",
        """\
Analyse the project brief and identify the hardest problems to get right:

- List the 4-5 decisions made early that will be hardest to change later. For each: what the
  decision is, what makes it hard to reverse, and what the right choice is.
- Flag data consistency risks: race conditions, double-writes, stale reads, eventual consistency traps.
- Identify the most complex business logic that needs the most careful test coverage.
  Name the edge cases that will be easy to miss.
- Note third-party integrations that introduce reliability risk and how to isolate them.
- Flag scale concerns relevant even at MVP: unbounded queries, missing indexes, synchronous bottlenecks.
- Name the most common mistakes made in projects like this one (auth gaps, missing scopes,
  missing idempotency on jobs, etc.).

Be direct. Name specific risks, not categories. Under 450 words.""",
    ),
]


# ── Agent call ────────────────────────────────────────────────────────────────

def call_agent(prompt: str, role: str, label: str = "") -> str:
    """Shared wrapper (migite_call): JSON envelope, usage ledger, config-driven timeouts/permissions/effort."""
    return migite_call.call_agent(prompt, role, tool="migite-blueprint", label=label).text


def apply_config(repo_root: str | None) -> None:
    global VAULT_BASE
    try:
        cfg = migite_config.load(repo_root)
    except migite_config.ConfigError as e:
        print(f"✘ config error: {e}", file=sys.stderr)
        sys.exit(1)
    for w in cfg.warnings:
        print(f"  ⚠ config: {w}", flush=True)
    VAULT_BASE    = str(cfg.expanded_path("vault.base"))
    migite_call.configure_from(cfg)
    try:
        migite_call.require_cli()
    except migite_call.AgentError as e:
        print(f"✘ {e}", file=sys.stderr)
        sys.exit(1)


# ── State ─────────────────────────────────────────────────────────────────────

class BlueprintState(TypedDict):
    brief: str
    project_name: str
    output_dir: str
    today: str
    stack_override: str
    stack: str
    analyses: Annotated[list[str], operator.add]
    blueprint: str
    milestones_raw: str
    knowledge_seed: str


class DimensionInput(TypedDict):
    dimension: str
    analyst_prompt: str
    brief: str
    stack: str


# ── Nodes ─────────────────────────────────────────────────────────────────────

def load_brief(state: BlueprintState) -> dict:
    print(f"  ▶ Brief loaded ({len(state['brief'])} chars) — project: {state['project_name']}", flush=True)
    return {}


def determine_stack(state: BlueprintState) -> dict:
    """No code exists yet for a new project, so there's nothing to discover a stack
    from (unlike migite-explore). Use --stack verbatim if given, otherwise infer one
    from the brief. Every downstream prompt is generic about the stack — this is the
    one place that has to actually name it."""
    override = state.get("stack_override", "").strip()
    if override:
        print(f"  ▶ Stack (explicit): {override}", flush=True)
        return {"stack": override}

    print("  ▶ Inferring target stack from brief", flush=True)
    prompt = f"""Project brief:
{state['brief']}

Determine the technical stack this project should be built on.

Rules:
- If the brief explicitly names a language, framework, or platform, use exactly that.
- If the brief is silent on tech, infer the most conventional, production-ready modern
  choice for the kind of system described. Be decisive — name one concrete stack, not a menu.
- Note whether this was stated or inferred.

Output exactly this block, nothing else:
Language:
Framework:
Database:
Test framework:
Lint/format tool:
Package manager:
Inferred: <yes|no>"""
    try:
        stack = call_agent(prompt, label="determine_stack", role="analyst")
    except Exception as e:
        stack = f"Language: unknown\nFramework: unknown\n(stack inference failed: {e})"
    first_line = stack.splitlines()[0] if stack else "(empty)"
    print(f"    {first_line}", flush=True)
    return {"stack": stack}


def route_to_analysts(state: BlueprintState) -> list[Send]:
    print(f"  ▶ Fanning out {len(ANALYSIS_DIMENSIONS)} parallel analysts", flush=True)
    return [
        Send("analyze_dimension", {
            "dimension":       dim,
            "analyst_prompt":  prompt,
            "brief":           state["brief"],
            "stack":           state["stack"],
        })
        for dim, prompt in ANALYSIS_DIMENSIONS
    ]


def analyze_dimension(state: DimensionInput) -> dict:
    dim = state["dimension"]
    print(f"    ◦ {dim}", flush=True)
    prompt = f"""Project brief:
{state['brief']}

## Stack context
{state['stack']}

---

{state['analyst_prompt']}"""
    try:
        result = call_agent(prompt, label=f"analyst:{dim}", role="analyst")
    except Exception as e:
        result = f"Analysis failed: {e}"
    return {"analyses": [f"### {dim}\n{result}"]}


def synthesize_blueprint(state: BlueprintState) -> dict:
    print(f"  ▶ Synthesising blueprint ({migite_call.model_for('blueprint_synth') or 'default'})", flush=True)
    analyses_text = "\n\n".join(state["analyses"])

    prompt = f"""You are a senior software architect synthesising a project blueprint from specialist analyses.

## Project brief
{state['brief']}

## Target stack
{state['stack']}

## Specialist analyses
{analyses_text}

Produce a complete, developer-ready project blueprint. This document will be:
1. Reviewed and edited by the developer before any code is written
2. The architectural record the project is built from
3. Ingested by migite's planning agent for each milestone

Output the blueprint in this exact structure — include every section:

# Blueprint: {state['project_name']}
Date: {state['today']}

## What we're building
<2-3 sentences: what the system does, who it's for, what problem it solves>

## Domain model

### Entities
<one bullet per entity — format: **ModelName** — role — key attributes: attr1:type, attr2:type, ...>

### Relationships
<bullet list, one relationship per line, plain English plus the chosen stack's construct for
it, e.g. "User → many Accounts (Rails: `belongs_to :account`)" or "User 1—* Account (Prisma relation)">

### Business rules
<bullet list: validations, state machine flows, constraints, immutability rules>

## User roles & flows

### Roles
<one bullet per role — name and what they can do>

### Critical flows (MVP)
<numbered list of must-have user journeys as step sequences>

## API surface (MVP)
<`METHOD /api/v1/path` — one-line description, one per line>

## Tech decisions
<bullet list — format: **gem or pattern** — why it's right for this project>

## MVP scope

### In scope
<bullet list — specific enough to know what's a "day-1 feature">

### Out of scope (future)
<bullet list>

## Hard problems & risks
<bullet list of the 4-5 hardest things to get right, with specific guidance on each>

## Milestones
Order milestones so each one is independently implementable in 1-3 developer-days
and builds on a stable foundation from previous milestones.
Number them. Include a "Foundation" milestone first.

Format each milestone as:
**N. Title**
Delivers: <one sentence — what is working at the end of this milestone>
Depends on: <milestone N title, or "none">
Key files: <comma-separated list of the main files/directories this milestone will create>

Output only the blueprint document. No preamble or meta-commentary."""

    try:
        blueprint = call_agent(prompt, label="synthesize_blueprint", role="blueprint_synth")
    except Exception as e:
        blueprint = f"# Blueprint: {state['project_name']}\nDate: {state['today']}\n\nSynthesis failed: {e}"
    return {"blueprint": blueprint}


def extract_milestones(state: BlueprintState) -> dict:
    print("  ▶ Extracting milestone intake files", flush=True)

    prompt = f"""You are reading a project blueprint and producing migite intake files for each milestone.

## Blueprint
{state['blueprint']}

## Task
For each milestone in the "## Milestones" section, produce a migite intake file.

Rules:
- The `Title:` line is required — it is used for directory slug extraction
- Scope bullets must be specific enough that a developer knows exactly which files/modules
  to create, in the terms of the blueprint's chosen stack
- Acceptance criteria must be testable (not "works correctly" — name specific behaviours)
- Each intake should be self-contained: include enough context that a developer
  starting fresh on just this milestone knows what to build

Output each intake between delimiters — nothing else outside them:

---INTAKE-START: <N>---
Title: <milestone title — same as in blueprint>
Type: feature
Date: {state['today']}

## Objective
<one paragraph: what this milestone accomplishes and why it comes at this point in the sequence.
Include what a developer must have completed before starting this milestone.>

## Scope
<bullet list — specific artefacts in the blueprint's stack: model/class names, migration or
schema names, service/handler names, controller or route actions, serializer/DTO fields,
job/worker names, test files>

## Acceptance Criteria
- [ ] <specific, testable criterion — name the endpoint, model, or behaviour>
- [ ] <the stack's lint/format command reports no new issues — name the real command from
  the blueprint's Tech decisions, e.g. `bundle exec rubocop`, `npm run lint`, `ruff check`>
- [ ] <the stack's test suite remains green — name the real command, e.g. `bundle exec rspec`,
  `npm test`, `pytest`>

## Notes for the planner
<domain-specific constraints from the blueprint the planner must respect:
naming conventions, authorization model, N+1 risks, gem choices>
---INTAKE-END: <N>---

Produce one block per milestone. Output only the delimited blocks."""

    try:
        milestones_raw = call_agent(prompt, label="extract_milestones", role="extract")
    except Exception as e:
        milestones_raw = f"Milestone extraction failed: {e}"
    return {"milestones_raw": milestones_raw}


def extract_knowledge_seed(state: BlueprintState) -> dict:
    print("  ▶ Extracting knowledge.md seed", flush=True)

    prompt = f"""You are producing the initial knowledge.md for a new project.

knowledge.md is injected into every migite planning and implementation run for this project.
It captures decisions that apply to every task — things a developer must know before touching any file.

## Blueprint
{state['blueprint']}

Produce a knowledge.md with these sections (markdown bullets, concise):

## Stack & dependencies
<what's chosen and the one-sentence rationale for each key choice>

## Domain glossary
<key terms and what they mean in this specific system — especially any terms that could be ambiguous>

## Authorization model
<one paragraph: how auth works end to end — who can see/do what, how scoping works>

## Naming conventions
<model names, service class naming pattern, job class naming pattern, scope naming pattern>

## N+1 watchlist
<the specific associations most likely to cause N+1 — name the eager loads>

## Hard constraints (do not violate)
<the 3-5 most important architectural decisions from the blueprint that must not be revisited per-task>

Under 600 words total. Output only the knowledge.md content — no preamble."""

    try:
        knowledge = call_agent(prompt, label="extract_knowledge_seed", role="extract")
    except Exception as e:
        knowledge = f"Knowledge seed extraction failed: {e}"
    return {"knowledge_seed": knowledge}


def write_outputs(state: BlueprintState) -> dict:
    print("  ▶ Writing outputs", flush=True)
    out = Path(state["output_dir"])
    out.mkdir(parents=True, exist_ok=True)

    # blueprint.md
    blueprint_path = out / "blueprint.md"
    blueprint_path.write_text(state["blueprint"])
    print(f"    ✔ blueprint.md → {blueprint_path}", flush=True)

    # knowledge.md
    knowledge_path = out / "knowledge.md"
    knowledge_path.write_text(state["knowledge_seed"])
    print(f"    ✔ knowledge.md → {knowledge_path}", flush=True)

    # Parse and write intake files
    intake_pattern = re.compile(
        r"---INTAKE-START:\s*(\d+)---(.*?)---INTAKE-END:\s*\1---",
        re.DOTALL,
    )
    matches = intake_pattern.findall(state["milestones_raw"])
    if matches:
        for n_str, content in matches:
            n = int(n_str)
            content = content.strip()
            title_match = re.search(r"^Title:\s*(.+)$", content, re.MULTILINE)
            title = title_match.group(1).strip() if title_match else f"milestone-{n}"
            slug = migite_paths.slugify(title)  # one shared slugify — see migite_paths
            filename = f"intake-{n:02d}-{slug}.md"
            (out / filename).write_text(content)
            print(f"    ✔ {filename}", flush=True)
    else:
        # Parsing failed — dump raw so nothing is lost
        raw_path = out / "milestones-raw.md"
        raw_path.write_text(state["milestones_raw"])
        print(f"    ⚠ Could not parse milestone blocks — raw output at: {raw_path}", flush=True)

    print(f"\n  Output directory: {out}", flush=True)
    return {}


# ── Graph ──────────────────────────────────────────────────────────────────────

def build_full_graph() -> StateGraph:
    g = StateGraph(BlueprintState)
    g.add_node("load_brief",           load_brief)
    g.add_node("determine_stack",      determine_stack)
    g.add_node("analyze_dimension",    analyze_dimension)
    g.add_node("synthesize_blueprint", synthesize_blueprint)
    g.add_node("extract_milestones",   extract_milestones)
    g.add_node("extract_knowledge",    extract_knowledge_seed)
    g.add_node("write_outputs",        write_outputs)

    g.add_edge(START,                  "load_brief")
    g.add_edge("load_brief",           "determine_stack")
    g.add_conditional_edges("determine_stack", route_to_analysts, ["analyze_dimension"])
    g.add_edge("analyze_dimension",    "synthesize_blueprint")
    g.add_edge("synthesize_blueprint", "extract_milestones")
    g.add_edge("extract_milestones",   "extract_knowledge")
    g.add_edge("extract_knowledge",    "write_outputs")
    g.add_edge("write_outputs",        END)
    return g.compile()


# ── Re-extract mode (--from-blueprint) ────────────────────────────────────────

def run_from_blueprint(blueprint_path: str, output_dir: str) -> None:
    """Skip analysis + synthesis. Re-extract milestones + knowledge from an existing blueprint."""
    blueprint = Path(blueprint_path).read_text()

    # Extract project name from first # heading
    name_match = re.search(r"^#\s+Blueprint:\s*(.+)$", blueprint, re.MULTILINE)
    project_name = name_match.group(1).strip() if name_match else Path(blueprint_path).stem

    today = date.today().isoformat()

    # Fake a minimal state for the extraction nodes. Stack is left blank — the
    # blueprint text itself already carries the tech decisions from the original run.
    state: BlueprintState = {
        "brief":         "",
        "project_name":  project_name,
        "output_dir":    output_dir,
        "today":         today,
        "stack_override": "",
        "stack":         "",
        "analyses":      [],
        "blueprint":     blueprint,
        "milestones_raw": "",
        "knowledge_seed": "",
    }

    print(f"  ▶ Re-extracting from blueprint: {project_name}", flush=True)
    state.update(extract_milestones(state))
    state.update(extract_knowledge_seed(state))
    write_outputs(state)


# ── CLI ────────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description="migite-blueprint: project definition tool")
    ap.add_argument("--brief-file",      help="Path to a file containing the project brief")
    ap.add_argument("--project-name",    help="Project name (slug)")
    ap.add_argument("--output",          required=True, help="Output directory")
    ap.add_argument("--from-blueprint",  help="Re-extract milestones from an existing blueprint.md")
    ap.add_argument("--stack",           default="", help="Explicit tech stack, e.g. 'Rails 7 API + Postgres' "
                                                            "or 'Next.js + TypeScript + Postgres'. If omitted, "
                                                            "inferred from the brief.")
    args = ap.parse_args()

    # No repo is required for a blueprint; look for .migite.yml in the current directory.
    apply_config(os.getcwd())

    if args.from_blueprint:
        try:
            run_from_blueprint(args.from_blueprint, args.output)
            print("\n  ✔ migite-blueprint re-extraction complete", flush=True)
        except Exception as e:
            print(f"\n  ✘ migite-blueprint failed: {e}", file=sys.stderr, flush=True)
            sys.exit(1)
        return

    if not args.brief_file:
        print("✘ --brief-file is required for a full run", file=sys.stderr)
        sys.exit(1)
    if not args.project_name:
        print("✘ --project-name is required for a full run", file=sys.stderr)
        sys.exit(1)

    brief = Path(args.brief_file).read_text().strip()
    if not brief:
        print("✘ Brief file is empty", file=sys.stderr)
        sys.exit(1)

    today = date.today().isoformat()
    print(f"\n  migite-blueprint | analysts={migite_call.model_for('analyst') or 'default'}  synth={migite_call.model_for('blueprint_synth') or 'default'}", flush=True)
    if args.stack:
        print(f"  Stack:   {args.stack} (explicit)", flush=True)
    else:
        print("  Stack:   inferring from brief", flush=True)

    graph = build_full_graph()
    try:
        graph.invoke({
            "brief":          brief,
            "project_name":   args.project_name,
            "output_dir":     args.output,
            "today":          today,
            "stack_override": args.stack,
            "stack":          "",
            "analyses":       [],
            "blueprint":      "",
            "milestones_raw": "",
            "knowledge_seed": "",
        })
        print("\n  ✔ migite-blueprint complete", flush=True)
    except Exception as e:
        print(f"\n  ✘ migite-blueprint failed: {e}", file=sys.stderr, flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
