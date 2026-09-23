#!/usr/bin/env python3
# migite-explore - initiative feasibility exploration using LangGraph
#
# Explores a large initiative against an existing codebase WITHOUT implementing
# anything. Fans out parallel analytical lenses, synthesises a feasibility
# document, runs an adversarial challenge pass, and writes the result.
# Called by the migite-explore bash wrapper.
#
# Unlike migite-plan and migite-audit, this tool is language-agnostic: it
# discovers files via `git ls-files` rather than Rails glob patterns, because
# an initiative usually cuts across whatever the repo happens to be written in.

import argparse
import operator
import os
import re
import shlex
import subprocess
import sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from migite import gateway
from migite import config
from migite import paths

# ── Models ──────────────────────────────────────────────────────────────────────
# Exploration quality is the entire product here - there is no implementation
# phase downstream to catch a shallow read, so lenses use the standard tier
# rather than fast; synthesis, the adversarial challenge, and the refine that
# applies it use the strong tier. Calls name the role; the gateway resolves the
# model for the configured agent.

VAULT_BASE = os.environ.get("DEV_LOG_BASE", str(Path.home() / "dev-log"))


def apply_config(repo_root: str | None) -> None:
    global VAULT_BASE
    try:
        cfg = config.load(repo_root)
    except config.ConfigError as e:
        print(f"✘ config error: {e}", file=sys.stderr)
        sys.exit(1)
    for w in cfg.warnings:
        print(f"  ⚠ config: {w}", flush=True)
    VAULT_BASE      = str(cfg.expanded_path("vault.base"))
    gateway.configure_from(cfg)
    try:
        gateway.require_cli()
    except gateway.AgentError as e:
        print(f"✘ {e}", file=sys.stderr)
        sys.exit(1)

NOISE_SUFFIXES = (
    ".lock", ".sum", ".min.js", ".map", ".svg", ".png", ".jpg", ".jpeg",
    ".gif", ".ico", ".pdf", ".woff", ".woff2", ".ttf", ".eot", ".zip",
    ".gz", ".mp4", ".mp3", ".snap",
)
NOISE_DIRS = (
    "node_modules/", "vendor/", "dist/", "build/", "tmp/", "log/",
    "coverage/", ".git/", "public/assets/", "storage/", "__pycache__/",
)

STOP_WORDS = {
    "this", "that", "with", "from", "have", "been", "will", "when", "where",
    "what", "which", "their", "there", "they", "also", "into", "more", "some",
    "than", "then", "type", "name", "each", "such", "well", "just", "only",
    "should", "would", "could", "these", "those", "about", "after", "before",
    "other", "first", "make", "want", "need", "able", "work", "using", "used",
    "like", "much", "many", "them", "very", "over", "same", "both", "does",
}

# Six analytical lenses. Each receives the same shared repo context and applies
# a different question to it - an initiative cuts across the whole repo, so
# splitting by directory (the migite-plan approach) would fragment the analysis.
EXPLORE_LENSES = [
    (
        "current_state",
        """\
- Where does this codebase implement (or partially implement) the concern in the brief TODAY?
- Name the concrete files, functions, classes, and data structures involved
- If the concern does not exist yet, identify the closest analogue already in the repo
- Describe the current control flow in 3-6 numbered steps, naming real symbols at each step
- Quantify what exists: how many call sites, how many files, how deep does it reach?""",
    ),
    (
        "touchpoints",
        """\
- Inventory EVERY file, module, or interface that would need to change for this initiative
- Classify each one as: NEW (create) / MODIFY (edit in place) / REWRITE (substantial rework) / DELETE
- For each, give one line on what specifically changes about it
- Flag anything that is a public interface or contract other code depends on
- Be exhaustive rather than representative - a missed touchpoint is the main failure mode of this analysis""",
    ),
    (
        "coupling_and_risk",
        """\
- What is tightly coupled to the current approach and would break?
- Hardcoded assumptions, implicit contracts, and duplicated logic that must stay in sync
- Which touchpoints have NO test coverage protecting them from regression?
- What is the blast radius if this initiative is done badly - what stops working?
- Where would a partial migration leave the system in a broken or confusing intermediate state?""",
    ),
    (
        "existing_seams",
        """\
- What abstractions, interfaces, config points, env vars, or extension hooks ALREADY exist that this initiative could build on?
- Where does the code already do indirection that could absorb this change without new machinery?
- Prior art: has a similar generalisation or swap already been done somewhere in this repo? Name it.
- What can be reused rather than invented?
- Where is the natural seam - the single narrowest place a change could be introduced?""",
    ),
    (
        "constraints",
        """\
- External contracts that cannot break: CLI flags, file formats, env vars, APIs, output paths other things depend on
- Backwards-compatibility requirements and who or what depends on current behaviour
- Dependency, runtime, or tooling constraints (language versions, CI, build, installed binaries)
- Test suite, configuration, documentation, and install steps that would need updating
- Anything that makes a big-bang change impossible and forces an incremental migration""",
    ),
    (
        "approaches",
        """\
- Propose 2-3 GENUINELY DISTINCT approaches to achieving this initiative
- For each: the core idea, its rough shape in this specific codebase, main advantage, main drawback
- Include at least one minimal/incremental option AND one thorough/clean-slate option
- Do NOT pick a winner - present the options fairly with their real tradeoffs
- For each approach, name the specific files that would carry the bulk of the change""",
    ),
]


# ── Agent call ──────────────────────────────────────────────────────────────────

def call_agent(prompt: str, role: str, thinking: bool = False, label: str = "") -> str:
    """Shared wrapper (gateway): configured backend, usage ledger, timeouts,
    headless permission mode and per-role --effort from the config."""
    return gateway.call_agent(prompt, role, thinking=thinking, tool="migite-explore", label=label).text


def run_cmd(cmd: str, cwd: str, timeout: int = 60) -> str:
    try:
        r = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, cwd=cwd, timeout=timeout
        )
        return r.stdout.strip()
    except Exception:
        return ""


# ── Language-agnostic repo discovery ────────────────────────────────────────────

def extract_keywords(text: str) -> list[str]:
    words = re.findall(r"\b[A-Za-z][a-zA-Z_-]{3,}\b", text)
    seen: dict[str, int] = defaultdict(int)
    for w in words:
        lw = w.lower()
        if lw not in STOP_WORDS:
            seen[lw] += 1
    return [w for w, _ in sorted(seen.items(), key=lambda kv: -kv[1])]


def is_noise(path: str) -> bool:
    if any(path.startswith(d) or f"/{d}" in path for d in NOISE_DIRS):
        return True
    return path.endswith(NOISE_SUFFIXES)


def list_repo_files(repo_root: str) -> list[str]:
    out = run_cmd("git ls-files", repo_root)
    return sorted(f for f in out.split("\n") if f.strip() and not is_noise(f))


def score_files(repo_root: str, files: list[str], keywords: list[str]) -> dict[str, int]:
    """Path matches weigh more than content matches; content search uses git grep
    so it stays language-agnostic and respects the index.

    Keywords are weighted by specificity - a distinctive term like "multi-tenant"
    should outrank a generic one like "data", which otherwise drags in every
    config file that happens to contain the substring.
    """
    def weight(kw: str) -> int:
        return 3 if len(kw) >= 9 else 2 if len(kw) >= 6 else 1

    scores: dict[str, int] = defaultdict(int)
    top_kw = keywords[:20]

    for f in files:
        low = f.lower()
        for kw in top_kw:
            if kw in low:
                scores[f] += weight(kw) * 2

    # Short keywords match nearly everything in content - only grep distinctive ones
    for kw in [k for k in top_kw if len(k) >= 6][:12]:
        out = run_cmd(f"git grep -l -i -- {shlex.quote(kw)}", repo_root, timeout=30)
        for f in out.split("\n"):
            f = f.strip()
            if f and not is_noise(f):
                scores[f] += weight(kw)

    # Recent churn is a signal that an area is active and likely in scope
    churn = run_cmd("git log --name-only --pretty=format: -60", repo_root, timeout=30)
    for f in {f.strip() for f in churn.split("\n") if f.strip()}:
        if f in scores:
            scores[f] += 1

    return scores


def build_tree_summary(files: list[str], limit: int = 350) -> str:
    if len(files) <= limit:
        return "\n".join(files)

    by_dir: dict[str, int] = defaultdict(int)
    for f in files:
        parts = f.split("/")
        if len(parts) > 2:
            key = "/".join(parts[:2]) + "/"
        elif len(parts) == 2:
            key = parts[0] + "/"
        else:
            key = "(repo root)"
        by_dir[key] += 1

    lines = [f"({len(files)} tracked files - showing directory counts)"]
    lines += [f"{d}  ({n} files)" for d, n in sorted(by_dir.items(), key=lambda kv: -kv[1])]
    return "\n".join(lines)


def build_repo_context(repo_root: str, brief: str, max_chars: int = 22000) -> str:
    """Shared context handed to every lens: repo shape, git signal, and the
    contents of the files most relevant to the brief."""
    files = list_repo_files(repo_root)
    if not files:
        return "## Repository\n(no tracked files found - is this a git repo?)"

    keywords = extract_keywords(brief)
    scores = score_files(repo_root, files, keywords)
    ranked = sorted(files, key=lambda f: (-scores.get(f, 0), f))

    tree = build_tree_summary(files)
    recent = run_cmd("git log --oneline -15", repo_root) or "(no history)"
    top_hits = [f for f in ranked if scores.get(f, 0) > 0][:25]

    header = (
        f"## Repository file tree\n{tree}\n\n"
        f"## Recent commits\n{recent}\n\n"
        f"## Files most relevant to the brief (keyword-ranked)\n"
        + ("\n".join(f"{f}  (score {scores.get(f, 0)})" for f in top_hits) or "(no keyword hits)")
        + "\n\n## File contents"
    )

    parts = [header]
    total = len(header)
    for f in ranked[:30]:
        full = Path(repo_root) / f
        try:
            content = full.read_text(errors="replace")[:4000]
        except Exception:
            continue
        chunk = f"\n### {f}\n```\n{content}\n```"
        if total + len(chunk) > max_chars:
            break
        parts.append(chunk)
        total += len(chunk)

    return "\n".join(parts)


# ── State ───────────────────────────────────────────────────────────────────────

class ExploreState(TypedDict):
    brief: str
    title: str
    repo_root: str
    repo_name: str
    org: str
    today: str
    output_dir: str
    want_intakes: bool
    reextract: bool
    focus: str
    knowledge: str
    repo_context: str
    lens_reports: Annotated[list[str], operator.add]
    exploration_draft: str
    challenges: str
    exploration_final: str
    workstreams_raw: str


class LensInput(TypedDict):
    lens: str
    questions: str
    brief: str
    knowledge: str
    repo_context: str
    repo_name: str


# ── Nodes ───────────────────────────────────────────────────────────────────────

def load_context(state: ExploreState) -> dict:
    print(f"  ▶ Indexing {state['repo_name']}", flush=True)
    ctx = build_repo_context(state["repo_root"], state["brief"])
    print(f"    context assembled ({len(ctx)} chars)", flush=True)
    return {"repo_context": ctx}


def route_to_lenses(state: ExploreState) -> list[Send]:
    lenses = EXPLORE_LENSES
    if state["focus"]:
        fl = state["focus"].lower()
        matched = [(n, q) for n, q in lenses if fl in n.lower()]
        if matched:
            lenses = matched
        else:
            print(f"  ⚠ No lens matched focus '{state['focus']}' - running all", flush=True)

    print(f"  ▶ Fanning out {len(lenses)} analytical lenses in parallel", flush=True)
    return [
        Send("run_lens", {
            "lens": name,
            "questions": questions,
            "brief": state["brief"],
            "knowledge": state["knowledge"],
            "repo_context": state["repo_context"],
            "repo_name": state["repo_name"],
        })
        for name, questions in lenses
    ]


def run_lens(state: LensInput) -> dict:
    lens = state["lens"]
    print(f"    ◦ {lens}", flush=True)

    knowledge_block = (
        f"## Repository lessons (accumulated context)\n{state['knowledge'][:1500]}\n\n"
        if state.get("knowledge") else ""
    )

    prompt = f"""You are a staff engineer scoping a large initiative against an existing codebase.
This is EXPLORATION ONLY - nothing will be implemented from your output. Your job is to
establish what is actually true about this repository so a go/no-go decision can be made.

## The initiative
{state['brief']}

{knowledge_block}## Repository: {state['repo_name']}
{state['repo_context']}

## Your lens: {lens}
Answer only through this lens. Do not stray into what other lenses cover.

{state['questions']}

Rules:
- Ground every claim in a real file, symbol, or line you can see above. Cite `path/to/file:symbol`.
- If the context above is insufficient to answer something, say "UNKNOWN: <what you'd need to look at>"
  rather than guessing. Unknowns are valuable output, invented certainty is not.
- No hedging filler. No recommendations to "consider" things - state what is.
- Under 500 words.

Output findings only. No preamble."""

    try:
        findings = call_agent(prompt, label=f"lens:{lens}", role="lens")
    except Exception as e:
        findings = f"UNKNOWN: lens failed - {e}"
    return {"lens_reports": [f"### {lens}\n{findings}"]}


def synthesize_exploration(state: ExploreState) -> dict:
    print(f"  ▶ Synthesising feasibility document from {len(state['lens_reports'])} lenses", flush=True)
    lens_text = "\n\n".join(state["lens_reports"])

    prompt = f"""You are producing a feasibility document for a large initiative on an existing codebase.
The reader is the engineer who will decide whether to do this at all. Nothing has been built.
Your document is the decision artefact.

## The initiative
{state['brief']}

## Repository
{state['org']}/{state['repo_name']}

## Lens reports from parallel specialists
{lens_text}

## Effort scale (use exactly these)
- **S** - under a day
- **M** - 1-3 days
- **L** - 1-2 weeks
- **XL** - more than 2 weeks, or genuinely unbounded until a spike lands

## Required output format

# Initiative Exploration: {state['title']}
Date: {state['today']}
Repo: {state['org']}/{state['repo_name']}

## Verdict
<exactly one of these lines, verbatim:>
✅ PROCEED - the path is clear enough to plan and implement
🔬 SPIKE FURTHER - one or more unknowns must be resolved before committing
⏸️ DEFER - feasible but the cost outweighs the current value
❌ NOT WORTH IT - the coupling or cost makes this a bad investment

<2-4 sentences justifying the verdict. Name the single factor that decided it.>

---

## Summary
<one paragraph: what the initiative is, what it would take, and the shape of the work>

---

## Current State
<how the repo handles this concern today - concrete files and control flow.
If it does not exist, say so and name the closest analogue.>

---

## What Would Change

### New
| File / Module | What it does | Effort |
|---|---|---|

### Modified
| File / Module | Change | Effort |
|---|---|---|

### Rewritten
| File / Module | Why a rewrite | Effort |
|---|---|---|

<omit any of the three tables that would be empty>

---

## Approach Options

| Approach | Core idea | Advantage | Drawback | Effort |
|---|---|---|---|---|

### Recommended: <approach name>
<why this one, in 2-4 sentences. Name what you are trading away.>

---

## Workstreams
<sequenced units of work under the recommended approach>

### 1. <name> - <effort>
<what it covers, what it unblocks, what it depends on>

### 2. <name> - <effort>
...

---

## Open Questions
<the things that genuinely are not answerable from the code alone.
Every UNKNOWN from the lens reports must appear here or be explicitly resolved.>

- ❓ **<question>** - why it matters - how to answer it (spike / read X / ask a human)

---

## Decision Points
<choices a human must make, that are not technical unknowns>

- **<decision>** - options - what it blocks until decided

---

## Risks
- 🔴 **<risk>** - impact - mitigation
- 🟡 **<risk>** - impact - mitigation
- 🟢 **<risk>** - impact - mitigation

---

## Effort Summary
| Workstream | Size | Depends on |
|---|---|---|

**Total: <S/M/L/XL plus a range in days or weeks>**

---

## Formatting rules (mandatory)
- Tables for anything comparative. Never prose where a table fits.
- `###` subheadings inside any section with 3+ distinct items. Never bold text as a pseudo-header.
- Bullets under 4 lines. Split longer ones into sub-bullets.
- `---` between every major top-level section.
- Every file reference as an inline code span with its real path.
- No inline parenthetical tangents - give it its own bullet or omit it.
- Do not pad. If a section has one honest item, it has one item.

Honesty requirements:
- Do not manufacture confidence. If the lenses reported UNKNOWN, it belongs in Open Questions.
- Do not inflate effort estimates to seem careful, or deflate them to seem capable.
- If the honest verdict is ❌ NOT WORTH IT, say so plainly.

Output only the document."""

    draft = call_agent(prompt, thinking=True, label="synthesize_exploration", role="explore_synth")
    return {"exploration_draft": draft}


def challenge_assumptions(state: ExploreState) -> dict:
    print("  ▶ Adversarial challenge pass", flush=True)

    prompt = f"""You are a skeptical principal engineer reviewing a feasibility document written by
a colleague. Feasibility documents fail in predictable ways: they underestimate coupling,
they miss touchpoints, they hand-wave the hard part, and they produce a confident verdict
from thin evidence.

Your job is to attack this document. Assume it is too optimistic until proven otherwise.

## The initiative
{state['brief']}

## Repository context
{state['repo_context'][:12000]}

## The document under review
{state['exploration_draft']}

## Attack it on these axes

1. **Missed touchpoints** - what would need to change that this document does not mention?
2. **Underestimated effort** - which estimates are wrong, and what specifically was not counted?
3. **Hand-waved hard parts** - where does the document say something is straightforward that is not?
4. **Unjustified verdict** - is the verdict supported by the evidence presented, or is it a leap?
5. **False unknowns** - what is listed as an Open Question that is actually answerable from the code above?
6. **Missing risks** - what could go wrong that is not in the Risks section?
7. **Approach blind spot** - is there a materially different approach the document did not consider?

## Output format

For each finding:
- 🔴 **Critical** - <section of the document> - what is wrong - what it should say instead
- 🟡 **Warning** - <section> - what is wrong - what it should say instead
- 🟢 **Note** - <section> - minor gap or improvement

Rules:
- Ground every challenge in the repository context above. "You might have missed something" is not a finding.
- If a section is genuinely sound, do not invent a problem with it.
- Be specific about what the document should say instead - a challenge without a correction is noise.

If the document is sound on every axis, output exactly:
✅ No material weaknesses found.

Output findings only. No preamble."""

    try:
        findings = call_agent(prompt, thinking=True, label="challenge_assumptions", role="challenge")
    except Exception as e:
        findings = f"🔴 **Critical** - challenge pass failed: {e}"
    return {"challenges": findings}


def refine_exploration(state: ExploreState) -> dict:
    findings = state.get("challenges", "")
    if "No material weaknesses found" in findings:
        print("  ▶ Refine: challenge found nothing - document unchanged", flush=True)
        return {"exploration_final": state["exploration_draft"]}

    print("  ▶ Refining document with challenge findings", flush=True)
    prompt = f"""Original feasibility document:
{state['exploration_draft']}

Challenge findings from an adversarial reviewer:
{findings}

Revise the document to address every Critical and Warning finding. Rules:
- Keep the exact same section structure and format
- If a challenge raises the effort estimate, raise it - do not argue with the reviewer
- If a challenge invalidates the verdict, change the verdict
- If a challenge names a missed touchpoint, add it to the correct table
- If you disagree with a finding on evidence, keep your position but add the reviewer's
  concern to Open Questions or Risks rather than silently dropping it

Preserve these formatting rules:
- Tables for comparisons, `###` subheadings for sections with 3+ items
- Bullets under 4 lines, `---` between major sections
- File references as inline code spans with real paths

Output only the revised document."""

    try:
        refined = call_agent(prompt, thinking=True, label="refine_exploration", role="explore_refine")
    except Exception as e:
        print(f"    ⚠ Refine failed ({e}) - keeping draft", flush=True)
        return {"exploration_final": state["exploration_draft"]}
    return {"exploration_final": refined}


def extract_workstreams(state: ExploreState) -> dict:
    if not state["want_intakes"]:
        return {"workstreams_raw": ""}

    print("  ▶ Extracting workstream intake files", flush=True)
    prompt = f"""You are reading an initiative feasibility document and producing migite intake
files - one per workstream in the recommended approach.

## Feasibility document
{state['exploration_final']}

## Task
For each workstream under "## Workstreams", produce a migite intake file.

Rules:
- The `Title:` line is required - it is used for the filename slug
- Choose `Type:` per workstream: `spike` for anything gated on an Open Question,
  `refactor` for restructuring with no behaviour change, `feature` for new behaviour
- Scope bullets must name concrete files and symbols from the document
- Acceptance criteria must be testable - name specific behaviours, not "works correctly"
- Carry forward the relevant Open Questions and Risks into "Notes for the planner"
- Each intake must be self-contained: a developer starting on just this workstream
  should know what to build without reading the full exploration

Output each intake between delimiters - nothing else outside them:

---INTAKE-START: <N>---
Title: <workstream name - same as in the document>
Type: <spike|refactor|feature>
Date: {state['today']}

## Objective
<one paragraph: what this workstream accomplishes, why it sits at this point in the
sequence, and what must be complete before starting it>

## Scope
<bullet list - concrete files, modules, and symbols this workstream touches>

## Acceptance Criteria
- [ ] <specific, testable criterion>
- [ ] <specific, testable criterion>

## Notes for the planner
<constraints, coupling risks, and open questions from the exploration that this
workstream must respect or resolve>
---INTAKE-END: <N>---

Produce one block per workstream, in sequence order. Output only the delimited blocks."""

    try:
        raw = call_agent(prompt, label="extract_workstreams", role="explore_refine")
    except Exception as e:
        print(f"    ⚠ Workstream extraction failed: {e}", flush=True)
        raw = ""
    return {"workstreams_raw": raw}


def write_outputs(state: ExploreState) -> dict:
    print("  ▶ Writing outputs", flush=True)
    out = Path(state["output_dir"])
    out.mkdir(parents=True, exist_ok=True)

    if state.get("reextract"):
        # exploration.md is the (possibly hand-edited) input, and challenges.md
        # belongs to the original run - re-extraction must never clobber either.
        print("    · exploration.md / challenges.md left untouched", flush=True)
    else:
        (out / "exploration.md").write_text(state["exploration_final"])
        print(f"    ✔ exploration.md → {out / 'exploration.md'}", flush=True)

        (out / "challenges.md").write_text(state["challenges"])
        print(f"    ✔ challenges.md", flush=True)

    raw = state.get("workstreams_raw", "")
    if raw:
        pattern = re.compile(
            r"---INTAKE-START:\s*(\d+)---(.*?)---INTAKE-END:\s*\1---",
            re.DOTALL,
        )
        matches = pattern.findall(raw)
        if matches:
            for n_str, content in matches:
                n = int(n_str)
                content = content.strip()
                tm = re.search(r"^Title:\s*(.+)$", content, re.MULTILINE)
                title = tm.group(1).strip() if tm else f"workstream-{n}"
                filename = f"intake-{n:02d}-{paths.slugify(title)}.md"
                (out / filename).write_text(content)
                print(f"    ✔ {filename}", flush=True)
        else:
            (out / "workstreams-raw.md").write_text(raw)
            print("    ⚠ Could not parse workstream blocks - raw output saved", flush=True)

    # Surface the verdict on the terminal
    verdict_line = ""
    for line in state["exploration_final"].splitlines():
        s = line.strip()
        if any(s.startswith(m) for m in ("✅ PROCEED", "🔬 SPIKE FURTHER", "⏸️ DEFER", "❌ NOT WORTH IT")):
            verdict_line = s
            break

    print(f"\n  Output directory: {out}", flush=True)
    if verdict_line:
        print(f"\n  Verdict: {verdict_line}", flush=True)
    if not state.get("reextract"):
        crit = state["challenges"].count("🔴")
        warn = state["challenges"].count("🟡")
        print(f"  Challenge pass: {crit} critical  {warn} warnings", flush=True)
    if raw:
        print("  → Run: migite <intake-file>  to plan a workstream", flush=True)
    return {}


# ── Graph ───────────────────────────────────────────────────────────────────────

def build_graph() -> StateGraph:
    g = StateGraph(ExploreState)
    g.add_node("load_context",           load_context)
    g.add_node("run_lens",               run_lens)
    g.add_node("synthesize_exploration", synthesize_exploration)
    g.add_node("challenge_assumptions",  challenge_assumptions)
    g.add_node("refine_exploration",     refine_exploration)
    g.add_node("extract_workstreams",    extract_workstreams)
    g.add_node("write_outputs",          write_outputs)

    g.add_edge(START, "load_context")
    g.add_conditional_edges("load_context", route_to_lenses, ["run_lens"])
    g.add_edge("run_lens",               "synthesize_exploration")
    g.add_edge("synthesize_exploration", "challenge_assumptions")
    g.add_edge("challenge_assumptions",  "refine_exploration")
    g.add_edge("refine_exploration",     "extract_workstreams")
    g.add_edge("extract_workstreams",    "write_outputs")
    g.add_edge("write_outputs",          END)
    return g.compile()


# ── Helpers ─────────────────────────────────────────────────────────────────────

def run_from_exploration(exploration_path: str, output_dir: str) -> None:
    """Skip the lenses, synthesis, and challenge pass. Re-extract workstream
    intakes from an existing (and possibly hand-edited) exploration.md."""
    doc = Path(exploration_path).read_text().strip()
    if not doc:
        raise RuntimeError(f"Exploration file is empty: {exploration_path}")

    m = re.search(r"^#\s+Initiative Exploration:\s*(.+)$", doc, re.MULTILINE)
    title = m.group(1).strip() if m else Path(exploration_path).stem

    if "## Workstreams" not in doc:
        print(
            "  ⚠ No '## Workstreams' section found - extraction will likely produce nothing.\n"
            "    Add one, or re-run a full exploration.",
            flush=True,
        )

    state: ExploreState = {
        "brief":        "",
        "title":        title,
        "repo_root":    "",
        "repo_name":    "",
        "org":          "",
        "today":        date.today().isoformat(),
        "output_dir":   output_dir,
        "want_intakes": True,
        "reextract":    True,
        "focus":        "",
        "knowledge":    "",
        "repo_context":      "",
        "lens_reports":      [],
        "exploration_draft": "",
        "challenges":        "",
        "exploration_final": doc,
        "workstreams_raw":   "",
    }

    print(f"  ▶ Re-extracting workstreams from: {title}", flush=True)
    state.update(extract_workstreams(state))
    write_outputs(state)


def derive_title(brief: str) -> str:
    for line in brief.splitlines():
        s = line.strip().lstrip("#").strip()
        if s:
            return s[:80]
    return "untitled initiative"


# ── CLI ─────────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description="migite-explore: initiative feasibility exploration")
    ap.add_argument("--brief-file", help="Path to the initiative brief")
    ap.add_argument("--repo-root",  help="Git repo root directory")
    ap.add_argument("--name",       default="", help="Short slug for the initiative")
    ap.add_argument("--output",     default="", help="Output directory (default: vault)")
    ap.add_argument("--knowledge",  default="", help="Path to knowledge.md (optional)")
    ap.add_argument("--focus",      default="", help="Limit to lenses matching this keyword")
    ap.add_argument("--intakes",    action="store_true", help="Also emit workstream intake files")
    ap.add_argument("--jira",       default="", help="Jira ticket key or URL — groups this exploration under the ticket's existing folder")
    ap.add_argument("--from-exploration",
                    help="Re-extract workstream intakes from an existing exploration.md")
    args = ap.parse_args()

    apply_config(args.repo_root)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")

    # ── Re-extract mode: no repo, no brief, no model-heavy phases ──────────────
    if args.from_exploration:
        output_dir = args.output
        if not output_dir and args.jira:
            if not args.repo_root:
                print("✘ --repo-root is required to resolve --jira without --output", file=sys.stderr)
                sys.exit(1)
            repo_name = Path(args.repo_root).name
            org       = paths.detect_org(args.repo_root)
            try:
                run_dir = paths.resolve_run_dir(VAULT_BASE, org, repo_name, id=args.jira)
            except paths.InvalidRunKeyError as e:
                print(f"✘ {e}", file=sys.stderr)
                sys.exit(1)
            except paths.AmbiguousRunDirError as e:
                print(f"✘ Ambiguous run folder for '{e.slug}': {', '.join(e.candidates)}", file=sys.stderr)
                print("  Pass --output explicitly to disambiguate.", file=sys.stderr)
                sys.exit(1)
            output_dir = str(run_dir / f"explore-{timestamp}")
        elif not output_dir:
            output_dir = str(Path(args.from_exploration).parent)

        print(f"\n  migite-explore | re-extracting workstreams (extract={gateway.model_for('explore_refine') or 'default'})", flush=True)
        print(f"  Source: {args.from_exploration}", flush=True)
        print(f"  Output: {output_dir}\n", flush=True)
        try:
            run_from_exploration(args.from_exploration, output_dir)
            print("\n  ✔ migite-explore re-extraction complete", flush=True)
        except Exception as e:
            print(f"\n  ✘ migite-explore failed: {e}", file=sys.stderr, flush=True)
            sys.exit(1)
        return

    # ── Full run ───────────────────────────────────────────────────────────────
    if not args.brief_file:
        print("✘ --brief-file is required for a full run", file=sys.stderr)
        sys.exit(1)
    if not args.repo_root:
        print("✘ --repo-root is required for a full run", file=sys.stderr)
        sys.exit(1)

    brief = Path(args.brief_file).read_text().strip()
    if not brief:
        print("✘ Brief is empty", file=sys.stderr)
        sys.exit(1)

    knowledge = (
        Path(args.knowledge).read_text()
        if args.knowledge and Path(args.knowledge).exists() else ""
    )

    repo_name = Path(args.repo_root).name
    org       = paths.detect_org(args.repo_root)
    today     = date.today().isoformat()
    title     = derive_title(brief)
    slug      = paths.slugify(args.name) if args.name else paths.slugify(title)

    run_dir = None
    if not args.output and (args.jira or args.name):
        try:
            run_dir = paths.resolve_run_dir(
                VAULT_BASE, org, repo_name, id=args.jira or None, name=args.name or None
            )
        except paths.InvalidRunKeyError as e:
            print(f"✘ {e}", file=sys.stderr)
            sys.exit(1)
        except paths.AmbiguousRunDirError as e:
            print(f"✘ Ambiguous run folder for '{e.slug}': {', '.join(e.candidates)}", file=sys.stderr)
            print("  Pass --output explicitly to disambiguate.", file=sys.stderr)
            sys.exit(1)

    output_dir = args.output or (
        str(run_dir / f"explore-{timestamp}") if run_dir
        else str(Path(VAULT_BASE) / org / repo_name / f"exploration-{slug}-{today}")
    )

    print(f"\n  migite-explore | lens={gateway.model_for('lens') or 'default'}  synth={gateway.model_for('explore_synth') or 'default'}  challenge={gateway.model_for('challenge') or 'default'}", flush=True)
    print(f"  Repo:       {org}/{repo_name}", flush=True)
    print(f"  Initiative: {title}", flush=True)
    print(f"  Output:     {output_dir}", flush=True)
    if knowledge:
        print(f"  Knowledge:  loaded ({len(knowledge)} chars)", flush=True)
    if args.focus:
        print(f"  Focus:      {args.focus}", flush=True)
    print("", flush=True)

    graph = build_graph()
    try:
        graph.invoke({
            "brief":         brief,
            "title":         title,
            "repo_root":     args.repo_root,
            "repo_name":     repo_name,
            "org":           org,
            "today":         today,
            "output_dir":    output_dir,
            "want_intakes":  args.intakes,
            "reextract":     False,
            "focus":         args.focus,
            "knowledge":     knowledge,
            "repo_context":       "",
            "lens_reports":       [],
            "exploration_draft":  "",
            "challenges":         "",
            "exploration_final":  "",
            "workstreams_raw":    "",
        })
        print("\n  ✔ migite-explore complete", flush=True)
    except Exception as e:
        print(f"\n  ✘ migite-explore failed: {e}", file=sys.stderr, flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
