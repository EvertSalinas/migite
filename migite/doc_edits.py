#!/usr/bin/env python3
"""doc_edits - change a long document with exact edits instead of rewriting it.

Rewriting a 35-70 KB plan or testing plan to change a few passages costs tens of
thousands of output tokens and several minutes, and a rewrite can quietly drop
a section. Here the agent returns only find/replace edits. An edit lands when
its `find` text appears exactly once in the document; anything else is dropped
and reported. The output stays a few hundred tokens and nothing untouched can
change.

Used by migite.plan_fold (the end-of-run plan update), migite.tools.plan
(refining the plan with the critic's findings), and the CLI below, which bash
calls for plan-gate feedback and testing-plan updates:

  python -m migite.doc_edits update --doc F --out O --report R --role ROLE --label L
         --name "testing-plan.md" --task "what to change and why" [--context "Heading=path"]...

Exit 0: --out holds the updated document (unchanged when no edit was needed).
Exit 2: no usable edit came back; the caller falls back to a full rewrite.
Exit 1: the agent call failed.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

from migite import config
from migite import gateway

EDIT_SCHEMA = {
    "type": "object",
    "properties": {
        "revision": {"type": "string", "description": "one sentence: what changed in the document"},
        "edits": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "find": {"type": "string", "description": "a passage copied verbatim from the document, unique in it"},
                    "replace": {"type": "string", "description": "the corrected passage"},
                    "why": {"type": "string"},
                },
                "required": ["find", "replace"],
            },
        },
    },
    "required": ["revision", "edits"],
}

# Triage mode (the plan refiner): the context holds findings, and the editor may reject one it can
# show is wrong from the text it was given, instead of being forced to address every finding.
TRIAGE_PROPERTY = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "finding": {"type": "string", "description": "the rejected finding, quoted or paraphrased briefly"},
            "reason": {"type": "string", "description": "one line: why it is wrong or already handled"},
            "evidence": {"type": "string", "description": "a passage copied verbatim from the document or the context that shows it"},
        },
        "required": ["finding", "reason", "evidence"],
    },
}
TRIAGE_SCHEMA = {**EDIT_SCHEMA, "properties": {**EDIT_SCHEMA["properties"], "rejected_findings": TRIAGE_PROPERTY}}

TRIAGE_RULES = """- For each finding in the context, either edit the document so it addresses the finding, or REJECT it by
  listing it in `rejected_findings`. Reject only when the document or the rest of the context shows the
  finding is wrong or already handled: for example the document already specifies the index, guard or lock
  it asks for, or it assumes something the explorer reports contradict. `evidence` is a passage copied
  VERBATIM from the document or the context that shows this.
- You have no tools and cannot read the codebase. When you cannot show from the text you were given that a
  finding is wrong, address it. Never reject a finding because it is costly or inconvenient to fix."""

EDIT_RULES = """- Each edit's `find` is a passage copied VERBATIM from the document (whitespace included), long enough
  to appear exactly once in it. `replace` is that passage corrected. Edits that don't match exactly once
  are dropped. To add new text, `find` the passage it goes after and `replace` it with that passage plus
  the new text.
- Find every passage the change affects before answering: explanations and notes as well as commands,
  code, values and expected results. A stale sentence misleads as much as a stale command.
- Change only what the task requires. Keep everything else verbatim, including headings and structure.
  Don't reword for style.
- Write each `replace` as the document should now read. Don't mention the change, the run, or what the
  text used to say.
- If nothing needs to change, return an empty `edits` list."""


# ── Pure helpers ─────────────────────────────────────────────────────────────

def apply_edits(text: str, edits: list) -> tuple[str, list[dict], list[dict]]:
    """Apply each edit in order. An edit applies only when its `find` text appears
    exactly once in the text as it stands at that point. Returns (text, applied, rejected)."""
    applied, rejected = [], []
    for e in edits:
        find = e.get("find") if isinstance(e, dict) else None
        replace = e.get("replace") if isinstance(e, dict) else None
        entry = {"find": (find or "")[:120], "why": (e.get("why") or "") if isinstance(e, dict) else ""}
        if not isinstance(find, str) or not isinstance(replace, str) or not find.strip():
            rejected.append({**entry, "reason": "malformed edit"})
            continue
        count = text.count(find)
        if count == 0:
            rejected.append({**entry, "reason": "text not found in the document"})
        elif count > 1:
            rejected.append({**entry, "reason": f"text appears {count} times in the document"})
        elif find == replace:
            rejected.append({**entry, "reason": "no change"})
        else:
            text = text.replace(find, replace, 1)
            applied.append(entry)
    return text, applied, rejected


def parse_reply(text: str, structured: object = None) -> dict | None:
    """An edit reply as {"revision": str, "edits": list}, from structured output when
    the backend honoured the schema, else the first JSON object in the text (code
    fences allowed). None when there is nothing usable."""
    obj = structured if isinstance(structured, dict) else None
    if obj is None and text:
        stripped = re.sub(r"^```(?:json)?\s*|\s*```\s*$", "", text.strip())
        start, end = stripped.find("{"), stripped.rfind("}")
        if start != -1 and end > start:
            try:
                obj = json.loads(stripped[start:end + 1])
            except json.JSONDecodeError:
                obj = None
    if not isinstance(obj, dict) or not isinstance(obj.get("edits", []), list):
        return None
    parsed = {"revision": str(obj.get("revision") or ""), "edits": obj.get("edits") or []}
    if isinstance(obj.get("rejected_findings"), list):      # only a triage reply carries it
        parsed["rejected_findings"] = [r for r in obj["rejected_findings"] if isinstance(r, dict)]
    return parsed


def build_update_prompt(doc: str, *, name: str, task: str, context: list[tuple[str, str]], triage: bool = False,
                        doc_above: bool = False) -> str:
    """The edit prompt. With `doc_above` the call continues the session whose last reply is the
    document, so the prompt points at that reply instead of carrying the document again."""
    blocks = "\n".join(f"\n## {heading}\n{body}" for heading, body in context if body.strip())
    triage_rules = f"\n{TRIAGE_RULES}" if triage else ""
    shape = ('{"revision": "...", "edits": [{"find": "...", "replace": "...", "why": "..."}], '
             '"rejected_findings": [{"finding": "...", "reason": "...", "evidence": "..."}]}') if triage else \
            '{"revision": "...", "edits": [{"find": "...", "replace": "...", "why": "..."}]}'
    document = (f"{name} is your previous reply in this conversation, exactly as you wrote it. "
                f"Copy each `find` passage from that reply." if doc_above else doc)
    return f"""You are editing {name}, a document for a software task. {task}
{blocks}

## {name} (the document to edit)
{document}

## Rules
{EDIT_RULES}{triage_rules}
- `revision`: one sentence, at most 25 words, saying what changed (or "No changes." for an empty list).

Return ONLY a JSON object: {shape}"""


def update(doc: str, *, name: str, task: str, context: list[tuple[str, str]], role: str,
           label: str, tool: str = "migite", triage: bool = False, resume: str = "") -> tuple[str | None, dict]:
    """Ask for edits to `doc` and apply them. Returns (new_doc, report); new_doc is
    None when no usable edit came back, so the caller falls back to a full rewrite:
    an unparseable reply, or edits proposed where none applied. An empty edit list
    is a real answer (nothing to change) and returns the document as it was.
    With `triage`, the editor may also reject findings in the context; the report then carries
    `rejected_findings` (unchecked: the caller verifies their evidence).
    With `resume`, the call continues that session, whose last reply is `doc`: the prompt
    leaves the document out, and no schema is sent, since a schema reaches the CLI as a tool
    and a different tool set reads nothing of the session from the prompt cache. The JSON is
    read from the text. The report then carries `session_id`, for the next call to continue.
    Raises gateway.AgentError when the call itself fails."""
    resume = resume if gateway.supports("resume") else ""   # no session to point at: send the document
    prompt = build_update_prompt(doc, name=name, task=task, context=context, triage=triage, doc_above=bool(resume))
    schema = (TRIAGE_SCHEMA if triage else EDIT_SCHEMA) if gateway.supports("structured_output") and not resume else None
    res = gateway.call_agent(prompt, role, label=label, tool=tool, schema=schema, resume=resume)
    reply = parse_reply(res.text, res.structured)
    if reply is None:
        return None, {"reason": "the reply had no usable JSON edit list", "applied": [], "rejected": []}
    new_doc, applied, rejected = apply_edits(doc, reply["edits"])
    report = {"revision": reply["revision"], "applied": applied, "rejected": rejected}
    if resume:
        report["session_id"] = res.usage.session_id
    if triage:
        report["rejected_findings"] = reply.get("rejected_findings", [])
    if reply["edits"] and not applied:
        return None, {**report, "reason": "no proposed edit matched the document exactly once"}
    return new_doc, report


def print_report(report: dict) -> None:
    print(f"  ✔ {len(report.get('applied', []))} edit(s) applied, {len(report.get('rejected', []))} dropped", flush=True)
    for r in report.get("rejected", []):
        print(f"    ⚠ dropped ({r['reason']}): {r['find'][:80]!r}", flush=True)


# ── CLI ──────────────────────────────────────────────────────────────────────

def _cmd_update(args: argparse.Namespace) -> int:
    doc_path = Path(args.doc)
    doc = doc_path.read_text() if doc_path.is_file() else ""
    if not doc.strip():
        print(f"  ✘ doc_edits: nothing to edit at {args.doc}", file=sys.stderr, flush=True)
        return 2
    context = []
    for item in args.context:
        heading, _, path = item.partition("=")
        p = Path(path)
        context.append((heading, p.read_text(errors="replace") if p.is_file() else ""))
    try:
        new_doc, report = update(doc, name=args.name, task=args.task, context=context,
                                 role=args.role, label=args.label)
    except gateway.AgentError as e:
        print(f"  ✘ doc_edits: {e}", file=sys.stderr, flush=True)
        return 1
    gateway.write_json(args.report, report)
    if new_doc is None:
        print(f"  ⚠ doc_edits: {report['reason']}", flush=True)
        return 2
    Path(args.out).write_text(new_doc)
    print_report(report)
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description="doc_edits: change a document with exact edits")
    ap.add_argument("--repo-root", default=None)
    sub = ap.add_subparsers(dest="command", required=True)
    u = sub.add_parser("update", help="propose and apply edits to one document")
    u.add_argument("--doc", required=True)
    u.add_argument("--out", required=True)
    u.add_argument("--report", required=True)
    u.add_argument("--role", required=True)
    u.add_argument("--label", required=True)
    u.add_argument("--name", required=True, help="how the prompt names the document, e.g. testing-plan.md")
    u.add_argument("--task", required=True, help="what to change and why")
    u.add_argument("--context", action="append", default=[], help='"Heading=path" of a file the edits depend on; repeat')
    u.add_argument("--repo-root", dest="repo_root", default=argparse.SUPPRESS)
    args = ap.parse_args()
    try:
        cfg = config.load(args.repo_root or os.getcwd())
    except config.ConfigError as e:
        print(f"  ✘ doc_edits: config error: {e}", file=sys.stderr, flush=True)
        sys.exit(1)
    gateway.configure_from(cfg)
    sys.exit(_cmd_update(args))


if __name__ == "__main__":
    main()
