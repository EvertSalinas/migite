#!/usr/bin/env python3
# migite.tools.plan (migite-plan) — autonomous LangGraph planning agent
#
# Reads intake.md, fans out parallel codebase explorations, synthesises a plan,
# runs the architecture critic, refines the plan, then writes plan.md +
# architecture-critic.md + sentinel. Called by migite's spawn_langgraph().
#
# Usage:
#   python -m migite.tools.plan --intake <file> --plan-output <file> --critic-output <file> \
#               --repo-root <dir> --sentinel <file> [--task-type <type>] [--knowledge <file>]

import argparse
import glob
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

# ── Models ─────────────────────────────────────────────────────────────────────
# Calls name a role, never a model: explore (fast tier) for file analysis, think
# and critic (strong tier) for synthesis, refine, and the critique. The gateway
# resolves each role for the configured agent (models.* in the config).

# Prompts ship in the repo's prompts/ dir; resolve() finds the checkout from this
# module's real path. Missing files are a hard error
# in main() — the old "read if exists else ''" silently ran the planner with no
# plan format at all on a fresh machine.
MIGITE_HOME = Path(__file__).resolve().parents[2]   # migite/tools/<x>.py → the checkout
PROMPTS_DIR = MIGITE_HOME / "prompts"
# Defaults; main() re-resolves them through the config so `prompts.dir` can override per project.
PLAN_CMD_PATH = PROMPTS_DIR / "plan.md"
CRITIC_CMD_PATH = PROMPTS_DIR / "architecture_critic.md"


def read_prompt(path: Path) -> str:
    if not path.is_file():
        print(f"  ✘ migite-plan: prompt file missing: {path} — is the migite checkout intact?",
              file=sys.stderr, flush=True)
        sys.exit(1)
    return path.read_text()

EXPLORE_AREAS = [
    ("models",             ["app/models/**/*.rb"]),
    ("controllers",        ["app/controllers/**/*.rb"]),
    ("services",           ["app/services/**/*.rb", "app/interactors/**/*.rb", "app/commands/**/*.rb"]),
    ("serializers",        ["app/serializers/**/*.rb"]),
    ("specs",              ["spec/**/*_spec.rb"]),
    ("migrations_schema",  ["db/migrate/*.rb", "db/schema.rb"]),
    ("routes_config",      ["config/routes.rb", "config/application.rb", "config/initializers/*.rb"]),
]

# Used when --stack generic (no recognized stack profile — see detect_stack in
# lib/stack.sh): one broad area instead of Rails' MVC-shaped split, since a
# generic repo has no convention to organize exploration areas around.
_GENERIC_SOURCE_EXTENSIONS = (
    "py", "js", "jsx", "ts", "tsx", "go", "rb", "java", "kt", "rs",
    "c", "cc", "cpp", "h", "hpp", "cs", "php", "swift",
)
GENERIC_EXPLORE_AREAS = [
    ("source", [f"**/*.{ext}" for ext in _GENERIC_SOURCE_EXTENSIONS]),
]

# The Hotwire half of a Rails app: views, ViewComponents, helpers, Stimulus
# controllers and the importmap. An eighth explorer, added only when the task
# may touch the frontend (frontend_decision below), so a backend-only task, or
# an API-only repo, doesn't pay for it.
FRONTEND_EXPLORE_AREA = ("views_frontend", [
    "app/views/**/*.erb", "app/components/**/*.rb", "app/components/**/*.erb",
    "app/helpers/**/*.rb", "app/javascript/**/*.js", "app/javascript/**/*.ts",
    "config/importmap.rb",
])
# Gems that mean the app renders its own UI, not just JSON.
FRONTEND_GEMS = ("turbo-rails", "stimulus-rails", "view_component")

# Code-fence language per file extension for the excerpts explorers read.
FENCE_LANG = {".rb": "ruby", ".erb": "erb", ".js": "javascript", ".ts": "typescript", ".py": "python"}

# Directory components excluded from every glob's file listing — vendored/
# build-artifact trees that would otherwise dominate a generic repo's
# unscoped "**/*.<ext>" glob (Rails' EXPLORE_AREAS patterns are already
# scoped to app/spec/db/config, so this mainly matters for GENERIC_EXPLORE_AREAS).
EXCLUDE_DIR_COMPONENTS = {
    "node_modules", "vendor", ".venv", "venv", "dist", "build", "target",
    "coverage", "tmp", "log", "__pycache__", ".next", ".cache", ".git",
}

STOP_WORDS = {
    "this", "that", "with", "from", "have", "been", "will", "when", "where", "what",
    "which", "their", "there", "they", "also", "into", "more", "some", "than", "then",
    "type", "name", "each", "such", "well", "just", "only", "should", "would", "could",
    "these", "those", "about", "after", "before", "other", "first", "class", "return",
    "method", "endpoint", "rails", "ruby", "model", "controller", "service",
}


# ── Audit condensation ─────────────────────────────────────────────────────────

def condense_audit(audit_text: str, limit: int = 4000) -> str:
    """
    Extract all Critical/Warning lines first (never dropped), then append
    remaining raw text up to the limit. Prevents hard truncation from silently
    dropping high-severity findings when an audit report exceeds the limit.
    """
    lines = audit_text.splitlines()
    critical_lines = [l for l in lines if "🔴" in l or "🟡" in l]
    findings_block = "\n".join(critical_lines)
    if len(findings_block) >= limit:
        return findings_block[:limit]
    remaining = limit - len(findings_block) - 2
    raw_tail = audit_text[:remaining] if remaining > 0 else ""
    return (findings_block + "\n\n" + raw_tail).strip()


# ── Agent call ───────────────────────────────────────────────────────────────────

def call_agent(prompt: str, role: str = "think", thinking: bool = False, label: str = "") -> str:
    """Thin wrapper over the shared gateway.call_agent: same text-in/text-out
    contract the nodes below expect, plus per-call usage recorded to the run's
    ledger ($MIGITE_USAGE_LEDGER) under this tool name and the node label. `role`
    selects the configured --effort level (models.effort / models.roles_effort)."""
    return gateway.call_agent(
        prompt, role, thinking=thinking, tool="migite-plan", label=label
    ).text


def count_open_questions(plan: str) -> int:
    """`### N. <question>` entries under the plan's `## Open questions` section."""
    in_section = False
    n = 0
    for line in plan.splitlines():
        if line.startswith("## "):
            in_section = line.strip().lower().startswith("## open questions")
            continue
        if in_section and re.match(r"^###\s+\d+\.", line):
            n += 1
    return n


def extract_headings(text: str) -> set[str]:
    return {line.strip() for line in text.splitlines() if line.strip().startswith("#")}


def looks_like_stub(text: str, min_headings: int = 3, min_chars: int = 400) -> bool:
    """
    True if `text` reads as a one-line confirmation/narrative ("Plan written to
    Obsidian...", "Revised plan written... all findings addressed...") instead of an
    actual multi-section plan document — an observed failure mode for both
    synthesize_plan and refine_plan. The plan template requires at least six `##`
    sections (Summary/Scope/Approach/Test plan/Risks/Out of scope at minimum), so a
    genuine plan is never this short or this bare; a stub reliably has zero headings.
    """
    return len(extract_headings(text)) < min_headings or len(text) < min_chars


# ── File discovery helpers ──────────────────────────────────────────────────────

def extract_keywords(text: str) -> set[str]:
    words = re.findall(r"\b[A-Za-z][a-zA-Z]{3,}\b", text)
    return {w.lower() for w in words} - STOP_WORDS


def camelize(name: str) -> str:
    return "".join(part.capitalize() for part in re.split(r"[-_]", name) if part)


def discover_vendored_gems(repo_root: str) -> list[str]:
    """Gem names declared in the Gemfile — candidates for module-reference matching below."""
    gemfile = Path(repo_root) / "Gemfile"
    if not gemfile.exists():
        return []
    text = gemfile.read_text(errors="replace")
    return re.findall(r'^\s*gem\s+["\']([\w-]+)["\']', text, re.MULTILINE)


def intake_frontend(intake: str) -> str:
    """The intake's `**Frontend:**` answer: "yes", "no", or "" when the line is
    absent, blank, still the template's comment, or "unknown"."""
    m = re.search(r"^\*\*Frontend:\*\*(.*)$", intake, re.MULTILINE)
    if not m:
        return ""
    answer = re.sub(r"<!--.*?-->", "", m.group(1)).strip().lower()
    word = answer.split()[0].strip(".,;:-") if answer else ""
    return word if word in ("yes", "no") else ""


def repo_has_frontend(repo_root: str) -> bool:
    """True when the Rails app renders a UI worth exploring: an app/javascript
    dir, an importmap, or a frontend gem (FRONTEND_GEMS) in the Gemfile.
    app/views alone isn't enough - API-only apps still have mailer templates."""
    root = Path(repo_root)
    if (root / "app" / "javascript").is_dir() or (root / "config" / "importmap.rb").is_file():
        return True
    return any(gem in FRONTEND_GEMS for gem in discover_vendored_gems(repo_root))


def frontend_decision(intake: str, repo_root: str, stack: str) -> tuple[bool, str]:
    """(explore the frontend?, why). The intake's `**Frontend:**` answer wins
    both ways; with no answer, the repo decides (repo_has_frontend). The
    generic stack already globs every source file, JavaScript included."""
    if stack == "generic":
        return False, "generic stack"
    answer = intake_frontend(intake)
    if answer:
        return answer == "yes", f"intake says {answer}"
    if repo_has_frontend(repo_root):
        return True, "detected in repo"
    return False, "no frontend detected"


def read_vendored_gem_methods(repo_root: str, gem_name: str) -> str:
    """
    Enumerate every method defined in a gem's own lib/ source via `bundle show`.
    Exists so a plan's claim that a gem "doesn't support X" / "has no bulk equivalent"
    is checked against what the gem actually exposes, not just against which of its
    methods the app already happens to call.
    """
    try:
        show = subprocess.run(
            ["bundle", "show", gem_name], cwd=repo_root, capture_output=True, text=True, timeout=15
        )
    except Exception:
        return ""
    gem_path = show.stdout.strip()
    if show.returncode != 0 or not gem_path or not Path(gem_path).is_dir():
        return ""
    lib_dir = Path(gem_path) / "lib"
    if not lib_dir.is_dir():
        return ""
    lines = []
    for rb_file in sorted(lib_dir.rglob("*.rb")):
        rel = rb_file.relative_to(gem_path)
        try:
            for i, line in enumerate(rb_file.read_text(errors="replace").splitlines(), start=1):
                m = re.match(r"\s*def\s+(self\.)?([\w?!=]+)", line)
                if m:
                    lines.append(f"{rel}:{i}: def {m.group(1) or ''}{m.group(2)}")
        except Exception:
            continue
    return "\n".join(lines[:200])


def read_external_gem_context(repo_root: str, area_text: str) -> str:
    """
    If an area's file contents reference a vendored gem's module namespace (e.g.
    `FormsService::Client` for gem "forms_service" — Ruby's gem-name-to-module
    convention), pull that gem's full method inventory straight from its lib/ source
    and hand it to the planner as ground truth, rather than letting the plan infer
    what the gem can/can't do from which methods the app already calls.
    """
    blocks = []
    for gem_name in discover_vendored_gems(repo_root):
        module_name = camelize(gem_name)
        if len(module_name) < 4 or module_name not in area_text:
            continue
        methods = read_vendored_gem_methods(repo_root, gem_name)
        if methods:
            blocks.append(f"## Vendored gem: {gem_name} — full method inventory (from gem source)\n{methods}")
    return "\n\n".join(blocks)


def read_area_context(area: str, patterns: list[str], repo_root: str, keywords: set[str], base_branch: str) -> str:
    """
    Discover files in `area`, score by keyword relevance, read the most relevant ones.
    Changed files (vs base_branch) are always included first.
    """
    # Changed files in this area
    pat_str = " ".join(f"'{p}'" for p in patterns)
    changed_out = subprocess.run(
        f"git diff {shlex.quote(base_branch)} --name-only -- {pat_str}",
        shell=True, capture_output=True, text=True, cwd=repo_root
    ).stdout.strip()
    changed = [f for f in changed_out.split("\n") if f]

    # All files in area
    all_files: list[str] = []
    for pattern in patterns:
        all_files.extend(glob.glob(f"{repo_root}/{pattern}", recursive=True))
    all_files = sorted(
        f for f in set(all_files)
        if not EXCLUDE_DIR_COMPONENTS & set(Path(f).relative_to(repo_root).parts)
    )

    listing = "\n".join(f.replace(f"{repo_root}/", "") for f in all_files) or "(none)"

    # Read once, reused for both scoring below and the excerpt loop further down
    contents: dict[str, str] = {}
    for f in all_files:
        try:
            contents[f] = Path(f).read_text(errors="replace")[:2500]
        except Exception:
            contents[f] = ""

    # Score by keyword hits in path and in content. Path hits are weighted higher —
    # a deliberately-named file ("invoice_service.rb") is a stronger relevance signal
    # than an incidental keyword mention — but content hits catch relevant files whose
    # names don't reflect the intake's vocabulary, which path-only scoring missed.
    def score(f: str) -> int:
        path_hits = sum(1 for kw in keywords if kw in f.replace(repo_root, "").lower())
        body = contents[f].lower()
        content_hits = sum(body.count(kw) for kw in keywords)
        return path_hits * 5 + content_hits

    scored = sorted(((score(f), f) for f in all_files), reverse=True)

    # Read: changed files first, then top-scored
    to_read: list[str] = []
    seen: set[str] = set()
    for cf in changed:
        full = f"{repo_root}/{cf}"
        if full not in seen and Path(full).exists():
            to_read.append(full)
            seen.add(full)
    for _, f in scored[:8]:
        if f not in seen:
            to_read.append(f)
            seen.add(f)

    parts = [f"## {area} — file listing\n{listing}\n\n## Relevant file contents"]
    total = len(parts[0])
    for f in to_read[:14]:
        content = contents.get(f)
        if content is None:
            try:
                content = Path(f).read_text(errors="replace")[:2500]
            except Exception:
                continue
        rel = f.replace(f"{repo_root}/", "")
        chunk = f"\n### {rel}\n```{FENCE_LANG.get(Path(rel).suffix, '')}\n{content}\n```"
        if total + len(chunk) > 14000:
            break
        parts.append(chunk)
        total += len(chunk)

    return "\n".join(parts)


# ── LangGraph state ─────────────────────────────────────────────────────────────

class PlanState(TypedDict):
    intake: str
    knowledge: str
    audit: str
    blueprint: str
    task_file: str
    jira_context: str
    plan_cmd: str
    critic_cmd: str
    repo_root: str
    base_branch: str
    task_type: str
    stack: str
    frontend: bool          # explore views/JS and plan for them (frontend_decision)
    frontend_source: str    # why: "intake says yes", "detected in repo", ...
    plan_output: str
    critic_output: str
    testing_plan_output: str
    sentinel: str
    explorations: Annotated[list[str], operator.add]
    plan_draft: str
    critic_findings: str
    plan_final: str
    testing_plan: str
    # Provenance for plan.json — how many stub retries synthesis needed, and
    # what refine_plan did with the critic findings.
    synth_retries: int
    refine_status: str


class ExploreInput(TypedDict):
    area: str
    glob_patterns: list[str]
    intake: str
    knowledge: str
    jira_context: str
    repo_root: str
    base_branch: str


# ── Nodes ───────────────────────────────────────────────────────────────────────

def load_context(state: PlanState) -> dict:
    print("  ▶ Context loaded", flush=True)
    return {}


def route_to_explorers(state: PlanState) -> list[Send]:
    areas = GENERIC_EXPLORE_AREAS if state.get("stack") == "generic" else list(EXPLORE_AREAS)
    if state.get("frontend"):
        areas.append(FRONTEND_EXPLORE_AREA)
    print(f"  ▶ Fanning out {len(areas)} explorers in parallel (stack={state.get('stack', 'rails')})", flush=True)
    return [
        Send("explore", {
            "area": area,
            "glob_patterns": patterns,
            "intake": state["intake"],
            "knowledge": state["knowledge"],
            "jira_context": state.get("jira_context", ""),
            "repo_root": state["repo_root"],
            "base_branch": state["base_branch"],
        })
        for area, patterns in areas
    ]


def explore(state: ExploreInput) -> dict:
    area = state["area"]
    print(f"    ◦ {area}", flush=True)
    keywords = extract_keywords(state["intake"] + "\n" + state.get("jira_context", ""))
    context = read_area_context(area, state["glob_patterns"], state["repo_root"], keywords, state["base_branch"])
    gem_context = read_external_gem_context(state["repo_root"], context)
    if gem_context:
        context += f"\n\n{gem_context}"
    knowledge_block = (
        f"Repository lessons (apply these):\n{state['knowledge'][:800]}\n\n"
        if state.get("knowledge") else ""
    )
    gem_instruction = (
        "\n4. If a \"Vendored gem\" method inventory is included above, treat it as the "
        "authoritative list of what that gem supports. Never conclude a capability is "
        "missing just because the app doesn't currently call the method that provides it — "
        "check the inventory first."
        if gem_context else ""
    )
    prompt = f"""Task intake:
{state['intake'][:1500]}

{knowledge_block}{context}

Analyse the **{area}** area above for this task. Report:
1. Files, classes, and methods directly relevant to implementing this task
2. Patterns and conventions the implementation must follow
3. Anything that constrains or enables the implementation{gem_instruction}

Be specific — name files and methods. Under 350 words."""

    try:
        findings = call_agent(prompt, label=f"explore:{area}", role="explore")
    except Exception as e:
        findings = f"Exploration failed: {e}"
    return {"explorations": [f"### {area}\n{findings}"]}


def synthesize_plan(state: PlanState) -> dict:
    print(f"  ▶ Synthesising plan from {len(state['explorations'])} exploration reports", flush=True)
    explorations_text = "\n\n".join(state["explorations"])
    audit_block = (
        f"## Known codebase issues (from audit — do not worsen these, address if relevant)\n"
        f"{condense_audit(state['audit'])}\n\n"
        if state.get("audit") else ""
    )
    blueprint_block = (
        f"## Pre-decided architecture (from blueprint — build on these decisions, do not re-derive)\n"
        f"{state['blueprint'][:6000]}\n\n"
        if state.get("blueprint") else ""
    )
    task_file_block = (
        f"## Additional task details (supplied separately from the intake — treat as authoritative)\n"
        f"{state['task_file']}\n\n"
        if state.get("task_file") else ""
    )
    jira_block = (
        f"## Jira ticket (fetched from Jira — authoritative source for scope, acceptance criteria, and priority)\n"
        f"{state['jira_context']}\n\n"
        if state.get("jira_context") else ""
    )
    prompt = f"""## Task intake
{state['intake']}

{task_file_block}{jira_block}{frontend_block(state)}## Repository knowledge
{state['knowledge'] or '(none)'}

{blueprint_block}{audit_block}## Codebase exploration
{explorations_text}

## Plan format and instructions
{state['plan_cmd']}

## Formatting rules (apply these on top of the format above — they override default prose style)

These rules exist so the plan can be skimmed quickly. Every rule is mandatory:

- **Use `###` subheadings** inside sections whenever a section has 3+ distinct items (e.g. New files, Modified files, Schema). Never use bold text as a pseudo-header — that buries structure in a wall of text.
- **One blank line between every top-level bullet** in any list longer than 3 items.
- **Keep individual bullets under 4 lines.** If an explanation needs more, break it into a sub-bullet (`  -`) or a `> Note:` block. Do not write paragraph-length bullets.
- **File entries get their own sub-section.** Each new or modified file gets a `###` heading with its path, then a short description below — not a single giant bullet that combines path + rationale + caveats.
- **Use `---` between major top-level sections** to create visible breaks when skimming.
- **Tables over prose for comparisons.** Anything that reads "X does Y, but Z does W" belongs in a two-column table.
- **No inline parenthetical tangents.** If something is important enough to write, give it its own bullet. If it is not, omit it.

Write the complete development plan. Output only the plan document — no preamble or meta-commentary."""

    draft = call_agent(prompt, thinking=True, label="synthesize_plan")
    retries = 0

    # Guard against the model returning a one-line stub confirmation ("Plan written
    # to Obsidian...") instead of the actual document — observed in practice, and
    # nothing downstream (critic, refine, write_outputs) can recover from a broken
    # draft, so this must be caught here rather than propagating garbage.
    if looks_like_stub(draft):
        print("  ⚠ Synthesis returned a stub instead of a plan document — retrying once", flush=True)
        retry_prompt = prompt + (
            "\n\nYour previous output was not a plan document — it read as a short "
            "confirmation sentence instead of the actual multi-section plan. Output "
            "the FULL plan document itself, with real section headings, not a "
            "one-line description of what you would write."
        )
        retries = 1
        draft = call_agent(retry_prompt, thinking=True, label="synthesize_plan:retry")
        if looks_like_stub(draft):
            raise RuntimeError(
                "migite-plan: synthesis produced a stub instead of an actual plan "
                "document twice in a row — aborting rather than passing broken "
                f"content downstream (last output: {len(draft)} chars, "
                f"{len(extract_headings(draft))} headings)."
            )

    return {"plan_draft": draft, "synth_retries": retries}


def frontend_block(state: PlanState) -> str:
    """Planning guidance for the frontend half, from frontend_decision: plan the
    Hotwire layers when they're in play, and flag a backend-only intake whose
    acceptance criteria turn out to need UI work rather than silently adding it."""
    if state.get("frontend"):
        return (
            f"## Frontend ({state.get('frontend_source', '')})\n"
            "This task may touch the frontend; the views_frontend exploration covers views, "
            "components, helpers and Stimulus/Turbo code. If the task changes UI, list those files "
            "in Scope under their own `###` subsection, and in the Test plan include request specs "
            "that assert on turbo_stream responses and a system spec for each new interactive flow "
            "when the repo already has spec/system. If it turns out to be backend-only, say so and "
            "plan no view or JavaScript changes.\n\n"
        )
    if state.get("frontend_source") == "intake says no":
        return (
            "## Frontend (intake says no)\n"
            "The intake marks this task backend-only, so views and JavaScript were not explored. "
            "Plan no view or JavaScript changes; if the acceptance criteria can't be met without "
            "them, raise that under Open questions instead of planning them blind.\n\n"
        )
    return ""


def run_architecture_critic(state: PlanState) -> dict:
    print("  ▶ Architecture critic", flush=True)
    audit_block = (
        f"## Known codebase issues (from audit)\n{condense_audit(state['audit'])}\n\n"
        if state.get("audit") else ""
    )
    blueprint_block = (
        f"## Pre-decided architecture (from blueprint)\n{state['blueprint'][:4000]}\n\n"
        if state.get("blueprint") else ""
    )
    prompt = f"""{state['critic_cmd']}

{blueprint_block}{audit_block}## Plan to review:
{state['plan_draft']}"""
    findings = call_agent(prompt, thinking=True, label="architecture_critic", role="critic")
    return {"critic_findings": findings}


def refine_plan(state: PlanState) -> dict:
    findings = state.get("critic_findings", "")
    if "No architectural concerns" in findings:
        print("  ▶ Refine: no concerns — plan unchanged", flush=True)
        return {"plan_final": state["plan_draft"], "refine_status": "no_concerns"}
    print("  ▶ Refining plan with critic findings", flush=True)
    prompt = f"""Original plan:
{state['plan_draft']}

Architecture critic findings:
{findings}

Revise the plan to address every finding above. Keep the same structure and format — update only sections that need changes.

Formatting rules to preserve and enforce in the revised output:
- `###` subheadings inside sections with 3+ items — no bold pseudo-headers
- One blank line between every top-level bullet in lists longer than 3 items
- Bullets under 4 lines — split longer ones into sub-bullets or `> Note:` blocks
- File entries use `###` + path heading, not a giant single bullet
- `---` between major top-level sections
- Tables for comparisons, not inline prose

Output only the revised plan document."""
    refined = call_agent(prompt, thinking=True, label="refine_plan")
    status = "applied"

    # Guard against the model returning a narrative recap of its changes, or a bare
    # stub confirmation sentence, instead of the actual plan document (both observed
    # in practice). The heading-overlap check alone has a blind spot: `if
    # draft_headings and ...` silently no-ops whenever plan_draft itself has zero
    # `#`-headings, which can't be distinguished from a genuinely broken refine
    # output — that gap is exactly how a stub slipped through here even with this
    # check in place. looks_like_stub() examines `refined` directly and has no such
    # blind spot, so it's checked unconditionally alongside the overlap ratio.
    draft_headings = extract_headings(state["plan_draft"])
    kept = len(draft_headings & extract_headings(refined))
    overlap_regressed = bool(draft_headings) and (kept / len(draft_headings) < 0.6)
    if overlap_regressed or looks_like_stub(refined):
        print("  ⚠ Refine output didn't preserve plan structure — retrying once", flush=True)
        retry_prompt = prompt + (
            "\n\nYour previous output was not the plan document — it read as a summary "
            "or description of changes instead. Output the FULL revised plan itself, "
            "with the same headings as the original plan, not a recap of what changed."
        )
        refined = call_agent(retry_prompt, thinking=True, label="refine_plan:retry")
        status = "applied_after_retry"
        kept = len(draft_headings & extract_headings(refined))
        overlap_regressed = bool(draft_headings) and (kept / len(draft_headings) < 0.6)
        if overlap_regressed or looks_like_stub(refined):
            print("  ⚠ Refine still failed structure check — keeping pre-refine plan; "
                  "see architecture-critic.md for the findings that weren't applied", flush=True)
            return {"plan_final": state["plan_draft"], "refine_status": "kept_draft"}

    return {"plan_final": refined, "refine_status": status}


def generate_testing_plan(state: PlanState) -> dict:
    print("  ▶ Generating testing plan", flush=True)
    frontend_steps = (
        "\n<This plan touches the frontend: for every UI step give the page path on the local dev "
        "server, the exact action (click, fill, submit), and what should change on the page, "
        "including whether Turbo should update it in place without a full page reload. Add a step "
        "to check the browser console for JavaScript errors. These steps are written so a person, "
        "or an agent driving a browser, can follow them literally.>"
        if state.get("frontend") else ""
    )
    prompt = f"""You are writing a standalone QA/dev verification document for the implementation
plan below — the concrete steps a human runs by hand, after the code is built, to confirm it
actually works. This document lives on its own (not inside the plan) precisely so it can be
regenerated in full whenever the implementation changes, without touching the plan's history.

## Implementation plan
{state['plan_final']}

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

    testing_plan = call_agent(prompt, label="generate_testing_plan")
    return {"testing_plan": testing_plan}


def write_outputs(state: PlanState) -> dict:
    print("  ▶ Writing outputs", flush=True)

    plan_path = Path(state["plan_output"])
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(state["plan_final"])
    print(f"    ✔ plan.md → {plan_path}", flush=True)

    critic_path = Path(state["critic_output"])
    critic_path.parent.mkdir(parents=True, exist_ok=True)
    critic_path.write_text(state["critic_findings"])
    print(f"    ✔ architecture-critic.md → {critic_path}", flush=True)

    testing_plan_path = Path(state["testing_plan_output"])
    testing_plan_path.parent.mkdir(parents=True, exist_ok=True)
    testing_plan_path.write_text(state["testing_plan"])
    print(f"    ✔ testing-plan.md → {testing_plan_path}", flush=True)

    # plan.json — the machine-readable envelope beside plan.md. Everything here
    # is derived deterministically from the documents already written, so it can
    # never disagree with them; usage comes from this process's ledger records.
    critic = state["critic_findings"]
    critic_counts = gateway.severity_counts(critic)
    failed_explorers = [
        block.splitlines()[0].lstrip("# ").strip()
        for block in state["explorations"]
        if "Exploration failed:" in block
    ]
    ledger_path = os.environ.get(gateway.LEDGER_ENV)
    own_records = [r for r in gateway.read_ledger(ledger_path) if r.get("tool") == "migite-plan"] if ledger_path else []
    envelope = {
        **gateway.envelope_base("migite-plan"),
        "base_branch": state["base_branch"],
        "stack": state.get("stack", ""),
        "task_type": state.get("task_type", ""),
        "frontend": {
            "explored": bool(state.get("frontend")),
            "source": state.get("frontend_source", ""),
        },
        "critic": {
            "clean": "No architectural concerns" in critic,
            **critic_counts,
        },
        "open_questions": count_open_questions(state["plan_final"]),
        "plan_headings": [l.strip() for l in state["plan_final"].splitlines() if l.startswith("## ")],
        "synth_retries": state.get("synth_retries", 0),
        "refine_status": state.get("refine_status", ""),
        "explorers": {
            "count": len(state["explorations"]),
            "failed": failed_explorers,
        },
        "outputs": {
            "plan": str(plan_path),
            "critic": str(critic_path),
            "testing_plan": str(testing_plan_path),
        },
        "usage": gateway.summarize(own_records)["total"] if own_records else None,
    }
    json_path = plan_path.with_name("plan.json")
    gateway.write_json(json_path, envelope)
    print(f"    ✔ plan.json → {json_path}", flush=True)

    Path(state["sentinel"]).touch()
    print("    ✔ sentinel written", flush=True)
    return {}


# ── Graph ────────────────────────────────────────────────────────────────────────

def build_graph() -> StateGraph:
    g = StateGraph(PlanState)
    g.add_node("load_context", load_context)
    g.add_node("explore", explore)
    g.add_node("synthesize_plan", synthesize_plan)
    g.add_node("architecture_critic", run_architecture_critic)
    g.add_node("refine_plan", refine_plan)
    g.add_node("generate_testing_plan", generate_testing_plan)
    g.add_node("write_outputs", write_outputs)

    g.add_edge(START, "load_context")
    g.add_conditional_edges("load_context", route_to_explorers, ["explore"])
    g.add_edge("explore", "synthesize_plan")
    g.add_edge("synthesize_plan", "architecture_critic")
    g.add_edge("architecture_critic", "refine_plan")
    g.add_edge("refine_plan", "generate_testing_plan")
    g.add_edge("generate_testing_plan", "write_outputs")
    g.add_edge("write_outputs", END)
    return g.compile()


# ── CLI ──────────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description="migite-plan: autonomous LangGraph planner")
    ap.add_argument("--intake",        required=True, help="Path to intake.md")
    ap.add_argument("--plan-output",   required=True, help="Path to write plan.md")
    ap.add_argument("--critic-output", required=True, help="Path to write architecture-critic.md")
    ap.add_argument("--testing-plan-output", required=True, help="Path to write testing-plan.md")
    ap.add_argument("--repo-root",     required=True, help="Git repo root directory")
    ap.add_argument("--sentinel",      required=True, help="Sentinel file to touch on success")
    ap.add_argument("--base-branch",   default=None, help="Branch explorers diff against (default: auto-detect origin/HEAD, then main/master/develop)")
    ap.add_argument("--task-type",     default="feature")
    ap.add_argument("--stack",         default="rails", help="Detected project stack (rails, generic, ...) — selects explore-area globs")
    ap.add_argument("--knowledge",     default="", help="Path to knowledge.md (optional)")
    ap.add_argument("--audit",         default="", help="Path to audit.md (optional)")
    ap.add_argument("--blueprint",     default="", help="Path to blueprint.md (optional)")
    ap.add_argument("--task-file",     default="", help="Path to task.md — supplementary details on top of --intake (optional)")
    ap.add_argument("--jira-context",  default="", help="Path to a pre-fetched Jira ticket summary (optional)")
    args = ap.parse_args()

    # Layered config: models, timeouts, headless permission mode, prompt overrides.
    global PLAN_CMD_PATH, CRITIC_CMD_PATH
    try:
        cfg = config.load(args.repo_root)
    except config.ConfigError as e:
        print(f"  ✘ migite-plan: config error: {e}", file=sys.stderr, flush=True)
        sys.exit(1)
    for w in cfg.warnings:
        print(f"  ⚠ config: {w}", flush=True)
    gateway.configure_from(cfg)
    try:
        gateway.require_cli()
    except gateway.AgentError as e:
        print(f"  ✘ migite-plan: {e}", file=sys.stderr, flush=True)
        sys.exit(1)
    PLAN_CMD_PATH   = cfg.prompt_path("plan", MIGITE_HOME, args.repo_root)
    CRITIC_CMD_PATH = cfg.prompt_path("architecture_critic", MIGITE_HOME, args.repo_root)

    intake     = Path(args.intake).read_text()
    knowledge  = Path(args.knowledge).read_text()   if args.knowledge  and Path(args.knowledge).exists()  else ""
    audit      = Path(args.audit).read_text()       if args.audit      and Path(args.audit).exists()      else ""
    blueprint  = Path(args.blueprint).read_text()   if args.blueprint  and Path(args.blueprint).exists()  else ""
    task_file  = Path(args.task_file).read_text()   if args.task_file  and Path(args.task_file).exists()  else ""
    jira_context = Path(args.jira_context).read_text() if args.jira_context and Path(args.jira_context).exists() else ""
    plan_cmd   = read_prompt(PLAN_CMD_PATH)
    critic_cmd = read_prompt(CRITIC_CMD_PATH)
    base_branch = args.base_branch or paths.detect_base_branch(args.repo_root)
    frontend, frontend_source = frontend_decision(intake, args.repo_root, args.stack)

    print(f"  migite-plan | base branch: {base_branch}", flush=True)
    if args.stack != "generic":
        print(f"  migite-plan | frontend: {'explored' if frontend else 'skipped'} ({frontend_source})", flush=True)
    if audit:
        print(f"  migite-plan | audit context loaded ({len(audit)} chars)", flush=True)
    if blueprint:
        print(f"  migite-plan | blueprint context loaded ({len(blueprint)} chars)", flush=True)
    if task_file:
        print(f"  migite-plan | supplementary task.md loaded ({len(task_file)} chars)", flush=True)
    if jira_context:
        print(f"  migite-plan | Jira ticket context loaded ({len(jira_context)} chars)", flush=True)
    print(f"  migite-plan | explore={gateway.model_for('explore') or 'default'}  think={gateway.model_for('think') or 'default'}  critic={gateway.model_for('critic') or 'default'}", flush=True)

    graph = build_graph()
    try:
        graph.invoke({
            "intake":     intake,
            "knowledge":  knowledge,
            "audit":      audit,
            "blueprint":  blueprint,
            "task_file":  task_file,
            "jira_context": jira_context,
            "plan_cmd":   plan_cmd,
            "critic_cmd": critic_cmd,
            "repo_root":  args.repo_root,
            "base_branch": base_branch,
            "task_type":  args.task_type,
            "stack":      args.stack,
            "frontend":   frontend,
            "frontend_source": frontend_source,
            "plan_output":   args.plan_output,
            "critic_output": args.critic_output,
            "testing_plan_output": args.testing_plan_output,
            "sentinel":       args.sentinel,
            "explorations":   [],
            "plan_draft":     "",
            "critic_findings": "",
            "plan_final":     "",
            "testing_plan":   "",
            "synth_retries":  0,
            "refine_status":  "",
        })
        print("\n  ✔ migite-plan complete", flush=True)
    except Exception as e:
        print(f"\n  ✘ migite-plan failed: {e}", file=sys.stderr, flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
