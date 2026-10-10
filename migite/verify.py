#!/usr/bin/env python3
"""migite.verify - check reviewer findings before they reach the verdict.

A reviewer can be confidently wrong about code it did read (a class that "inherits from the
wrong base" because the name was not resolved the way Ruby resolves it). Two checks sit between
the reviewers and the synthesis, both shared by migite-review and migite-pr-review:

1. Evidence gate (deterministic): a Critical or Warning must quote the line it rests on in an
   **Evidence:** line. check_evidence() confirms the quote exists where the finding says it does.
2. Refuter (a second agent, fresh context, read-only tools): it tries to disprove every Critical,
   and every Warning whose evidence did not check out. CONFIRMED findings pass with a Verified
   line, REFUTED ones are dropped (and listed in an appendix, so a human can audit the call),
   UNVERIFIABLE ones are demoted to a Note. A refuter that fails leaves the finding as it was,
   marked as unchecked: the gate never depends on this step working.

verify_blocks() takes the per-dimension findings blocks ("### <dim>\\n<findings>") and returns the
same shape, so the synthesis does not change.
"""

import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

REFUTE_ROLE = "refute"
MAX_REFUTATIONS = 12                          # per run; the rest stay as reported, marked not checked
EVIDENCE_WINDOW = 6                           # lines either side of the cited line
SKIP_DIMENSIONS = ("testing_plan", "frontend")  # their findings rest on a document or a browser report, not on code

# Shared by both reviewers' prompts.
EVIDENCE_LINE = ("**Evidence:** `path/file.rb:N` `the exact line of code, copied verbatim`  "
                 "(one line, each part in single backticks)")
GROUNDING = """\
You have read-only tools (Read, Grep, Glob) on the repository. The text above is truncated and shows only
part of each file, so open the file instead of guessing. A finding rests on code, not on a name: before you
report that something is missing, misnamed, inherited from the wrong class, or unscoped, find its definition
and resolve it the way Ruby and Rails do (constant lookup through the enclosing modules, inheritance, concerns
and includes, default scopes, callbacks, Pundit and Rails conventions). Don't invent methods, files, or gems.

Evidence is required on every Critical and Warning: quote the line your claim rests on. A claim you have not
traced to code is not a Critical. A second agent will try to disprove each Critical, so state it precisely."""

HEAD_RE = re.compile(
    r"^(?P<lead>\s*(?:[-*]\s*)?)(?P<mark>[🔴🟡🟢])\s*\*\*(?P<sev>Critical|Warning|Note)\*\*"
    r"(?P<sep>\s*[·:–\u2014-]*\s*)(?P<rest>.*)$")
FIELD_RE = re.compile(r"^\s*(?:[-*]\s*)?\*\*(?P<name>[A-Za-z ]+):\*\*\s*(?P<val>.*)$")
SPAN_RE = re.compile(r"`([^`\n]+)`")
LOC_RE = re.compile(r"([^\s:`]+?)(?::(\d+)(?:-\d+)?)?")
SYNTHETIC_RE = re.compile(r"reviewer failed|stopped at its budget cap", re.I)
VERDICT_RE = re.compile(r"^\s*VERDICT:\s*(CONFIRMED|REFUTED|UNVERIFIABLE)\b", re.I | re.M)
EVIDENCE_REPLY_RE = re.compile(r"^\s*EVIDENCE:\s*(.*)$", re.M)
REASON_RE = re.compile(r"^\s*REASON:\s*(.*)\Z", re.M | re.S)

MARK = {"critical": "🔴", "warning": "🟡", "note": "🟢"}


@dataclass
class Finding:
    start: int           # index of its header line within the dimension's output
    lines: list          # the finding's block: its header line, then everything up to the next header
    lead: str
    severity: str        # critical | warning | note
    sep: str
    rest: str            # the header after the severity word: location and title

    @property
    def title(self) -> str:
        text = re.sub(r"^\s*`[^`]+`\s*[\u00b7\u2013\u2014:-]*\s*", "", self.rest) or self.rest
        return SPAN_RE.sub(lambda m: m.group(1), text).strip()

    def field(self, name: str) -> str:
        """The text of a `**Name:**` field, continuation lines included."""
        out, grabbing = [], False
        for line in self.lines[1:]:
            m = FIELD_RE.match(line)
            if m:
                if grabbing:
                    break
                if m["name"].strip().lower() == name.lower():
                    grabbing = True
                    out.append(m["val"])
                continue
            if grabbing:
                if not line.strip():
                    break
                out.append(line.strip())
        return " ".join(out).strip()

    @property
    def problem(self) -> str:
        return self.field("Problem")

    @property
    def evidence(self) -> str:
        return self.field("Evidence")

    @property
    def synthetic(self) -> bool:
        return bool(SYNTHETIC_RE.search(self.lines[0]))


def parse_findings(body: str) -> list[Finding]:
    """The findings in one dimension's output, in order. Each runs from its header
    (`- 🔴 **Critical** · path:N · title`) to the next one; text before the first is not part of any."""
    lines = body.splitlines()
    heads = [(i, m) for i, line in enumerate(lines) if (m := HEAD_RE.match(line))]
    found = []
    for n, (start, m) in enumerate(heads):
        end = heads[n + 1][0] if n + 1 < len(heads) else len(lines)
        found.append(Finding(start, lines[start:end], m["lead"], m["sev"].lower(), m["sep"], m["rest"]))
    return found


def _as_location(span: str) -> tuple[str, int | None] | None:
    """(path, line) when `span` is a `path:line` reference. It counts as one when it has a line
    number or a slash, so a quoted `user.name` is not mistaken for a path."""
    m = LOC_RE.fullmatch(span.strip())
    if m and (m.group(2) or "/" in m.group(1)):
        return m.group(1), int(m.group(2)) if m.group(2) else None
    return None


def _location(text: str) -> tuple[str | None, int | None]:
    """The first `path:line` span in `text`, or (None, None)."""
    for span in SPAN_RE.findall(text):
        loc = _as_location(span)
        if loc:
            return loc
    return None, None


def _quotes(evidence: str) -> list[str]:
    """The quoted code in an Evidence line: every backticked span that is not the location."""
    return [s.strip() for s in SPAN_RE.findall(evidence) if s.strip() and _as_location(s) is None]


def _norm(text: str) -> str:
    return " ".join(text.split())


def location_of(f: Finding) -> tuple[str | None, int | None]:
    path, line = _location(f.evidence)
    return (path, line) if path else _location(f.rest)


def check_evidence(f: Finding, read_file: Callable[[str], str | None], extra: str = "") -> tuple[str, str]:
    """(status, detail): `ok` when every quoted span is found near the cited line of the cited file
    (or anywhere in `extra`: the diff, the plan), `missing` when there is no usable Evidence line,
    `mismatch` when a quote is not there."""
    quotes = _quotes(f.evidence)
    if not quotes:
        return "missing", "the finding has no Evidence line quoting the code it rests on"
    path, line = location_of(f)
    window = None
    text = read_file(path) if path else None
    if text is not None:
        rows = text.splitlines()
        window = _norm(" ".join(rows[max(0, line - 1 - EVIDENCE_WINDOW):line + EVIDENCE_WINDOW])) if line else _norm(text)
    extra_norm = _norm(extra)
    for q in quotes:
        nq = _norm(q)
        if not ((window is not None and nq in window) or (nq in extra_norm if extra_norm else False)):
            where = f"{path}:{line}" if path and line else (path or "the cited file")
            return "mismatch", f"the quoted text `{q[:80]}` is not at {where}"
    return "ok", ""


def needs_refutation(f: Finding, status: str) -> bool:
    """Every Critical; a Warning only when its evidence did not check out. Notes are never refuted."""
    return f.severity == "critical" or (f.severity == "warning" and status != "ok")


def make_reader(repo_root: str, ref: str | None = None) -> Callable[[str], str | None]:
    """A reader for check_evidence: a file's content at `ref` (git show) when given and found,
    else from the working tree. Never leaves the repository: an absolute or `..` path reads nothing."""
    root = Path(repo_root).resolve()

    def read(rel: str) -> str | None:
        if not rel or rel.startswith("/") or ".." in Path(rel).parts:
            return None
        if ref:
            r = subprocess.run(["git", "show", f"{ref}:{rel}"], capture_output=True, text=True,
                               errors="replace", cwd=str(root))
            if r.returncode == 0:
                return r.stdout
        path = (root / rel).resolve()
        if root not in path.parents or not path.is_file():
            return None
        return path.read_text(errors="replace")
    return read


HOW_TO_WORK = """How to work:
1. Open the cited file and read the code around the cited line. Do not trust the claim's description of the code.
2. Trace the claim to the code that would make it true. Resolve names the way Ruby and Rails do: constant
   lookup through the enclosing modules (a class inside `module A::B` finds `A::B::Foo` before a top-level
   `Foo`), inheritance and ancestors, concerns and includes, default scopes, callbacks, Pundit and Rails conventions.
3. Look for what contradicts the claim: a definition the reviewer missed, a base class or concern that already
   provides it, a guard clause elsewhere, a spec that covers it."""


def refute_prompt(f: Finding, status: str, detail: str, context: str = "",
                  expertise: str = "Rails", how_to_work: str | None = None) -> str:
    """The refuter's prompt. expertise and how_to_work come from the stack's checklist
    (migite/checklists.py); the defaults are the rails ones, HOW_TO_WORK included."""
    path, line = location_of(f)
    where = f"`{path}:{line}`" if path and line else (f"`{path}`" if path else "(none given)")
    note = (f"\nA mechanical check found a problem with the reviewer's evidence: {detail}.\n"
            if status != "ok" else "")
    return f"""You are a skeptical senior {expertise} engineer. A code reviewer reported the finding below.
Your job is to try to DISPROVE it. Assume it may be wrong.
{context}
## Finding ({f.severity})
Location: {where}
Title: {f.title}
Claim: {f.problem or f.title}
{note}
{how_to_work or HOW_TO_WORK}

Verdicts:
- CONFIRMED: you traced the problem to specific code and can quote the line(s) that prove it.
- REFUTED: the code shows the claim is false; quote the line(s) that contradict it.
- UNVERIFIABLE: it depends on runtime data, configuration, or code you cannot read here.
When you are unsure, answer REFUTED or UNVERIFIABLE: a claim you cannot trace to code must not block a merge.

Reply in exactly this format and nothing else:
VERDICT: CONFIRMED | REFUTED | UNVERIFIABLE
EVIDENCE: `path/file.rb:N` `the exact line of code`
REASON: <one or two sentences>"""


def parse_verdict(text: str) -> tuple[str, str, str] | None:
    """(verdict, evidence, reason) from a refuter's reply, or None when it does not follow the format."""
    m = VERDICT_RE.search(text or "")
    if not m:
        return None
    ev = EVIDENCE_REPLY_RE.search(text)
    why = REASON_RE.search(text)
    return (m.group(1).upper(), ev.group(1).strip() if ev else "", _norm(why.group(1))[:600] if why else "")


def _refute(f: Finding, status: str, detail: str, ask: Callable[[str, str], str], context: str, label: str,
            expertise: str = "Rails", how_to_work: str | None = None) -> dict:
    try:
        parsed = parse_verdict(ask(refute_prompt(f, status, detail, context, expertise, how_to_work), label))
    except Exception as e:
        return {"verdict": "failed", "reason": str(e)[:160]}
    if parsed is None:
        return {"verdict": "failed", "reason": "the refuter's reply did not follow the required format"}
    return {"verdict": parsed[0].lower(), "evidence": parsed[1], "reason": parsed[2]}


def _bullet(f: Finding) -> str:
    return "  - " if f.lead.strip() else ""


def _apply(f: Finding, outcome: dict | None) -> list[str]:
    """The finding's lines after verification: unchanged, annotated, demoted, or [] when refuted."""
    lines = list(f.lines)
    while lines and not lines[-1].strip():
        lines.pop()
    tail = [""] * (len(f.lines) - len(lines))
    if outcome is None:
        return f.lines
    verdict, bullet = outcome["verdict"], _bullet(f)
    if verdict == "refuted":
        return []
    if verdict == "confirmed":
        proof = outcome.get("evidence") or outcome.get("reason") or "traced to the code"
        return lines + [f"{bullet}**Verified:** {proof}"] + tail
    if verdict == "unverifiable":
        lines[0] = f"{f.lead}{MARK['note']} **Note**{f.sep}{f.rest}"
        why = outcome.get("reason") or "it depends on something that could not be read"
        return lines + [f"{bullet}**Verification:** could not be confirmed, so this is a Note instead of a "
                        f"{f.severity.capitalize()}: {why}"] + tail
    return lines + [f"{bullet}**Verification:** not checked ({outcome.get('reason') or 'the refuter did not run'})"] + tail


def verify_blocks(blocks: list[str], ask: Callable[[str, str], str], read_file: Callable[[str], str | None],
                  *, extra: str = "", context: str = "", skip: tuple = SKIP_DIMENSIONS,
                  expertise: str = "Rails", how_to_work: str | None = None,
                  log: Callable[[str], None] = print) -> tuple[list[str], list[dict]]:
    """Run the evidence gate and the refuter over per-dimension blocks ("### <dim>\\n<findings>").
    `ask(prompt, label)` returns the refuter's reply text or raises. `expertise` and
    `how_to_work` come from the stack's checklist (refute_prompt). Returns the blocks with
    verified findings annotated, demoted or removed, and a record per refuted finding."""
    parsed = []           # (dimension, head, body, findings)
    jobs = []             # (block index, finding index, status, detail)
    for bi, block in enumerate(blocks):
        head, _, body = block.partition("\n")
        dim = head.lstrip("# ").strip()
        findings = parse_findings(body)
        parsed.append((dim, head, body, findings))
        for fi, f in enumerate(findings):
            if f.synthetic or dim in skip:
                continue
            status, detail = check_evidence(f, read_file, extra)
            if needs_refutation(f, status):
                jobs.append((bi, fi, status, detail))
    jobs.sort(key=lambda j: parsed[j[0]][3][j[1]].severity != "critical")   # Criticals first when capped
    run, capped = jobs[:MAX_REFUTATIONS], jobs[MAX_REFUTATIONS:]

    outcomes: dict[tuple[int, int], dict] = {}
    if run:
        log(f"  ▶ Verifying {len(run)} finding(s) with a second agent ({REFUTE_ROLE})")
        with ThreadPoolExecutor(max_workers=min(4, len(run))) as pool:
            futures = {
                (bi, fi): pool.submit(_refute, parsed[bi][3][fi], st, dt, ask, context,
                                      f"refute:{parsed[bi][0]}:{fi + 1}", expertise, how_to_work)
                for bi, fi, st, dt in run
            }
            outcomes = {key: fut.result() for key, fut in futures.items()}
    for bi, fi, _, _ in capped:
        outcomes[(bi, fi)] = {"verdict": "failed", "reason": f"over the limit of {MAX_REFUTATIONS} checks per run"}

    out_blocks, refuted = [], []
    for bi, (dim, head, body, findings) in enumerate(parsed):
        if not any((bi, fi) in outcomes for fi in range(len(findings))):
            out_blocks.append(blocks[bi])
            continue
        rebuilt, kept = body.splitlines()[:findings[0].start], 0
        for fi, f in enumerate(findings):
            outcome = outcomes.get((bi, fi))
            if outcome and outcome["verdict"] == "refuted":
                path, line = location_of(f)
                refuted.append({"dimension": dim, "severity": f.severity, "title": f.title,
                                "location": f"{path}:{line}" if path and line else (path or ""),
                                "reason": outcome.get("reason", ""), "evidence": outcome.get("evidence", "")})
                continue
            kept += 1
            rebuilt.extend(_apply(f, outcome))
        if kept == 0:
            rebuilt = [l for l in rebuilt if l.strip()] + ["✅ No issues remain in this dimension after verification."]
        out_blocks.append(f"{head}\n" + "\n".join(rebuilt))
    return out_blocks, refuted


def appendix(refuted: list[dict]) -> str:
    """The report section listing what verification disproved. Plain text: no severity marks, so
    nothing that counts them picks these up as live findings."""
    if not refuted:
        return ""
    rows = []
    for r in refuted:
        where = f", `{r['location']}`" if r.get("location") else ""
        proof = f" Evidence: {r['evidence']}" if r.get("evidence") else ""
        rows.append(f"- **{r['title']}** (was {r['severity'].capitalize()}{where}, {r['dimension']}): {r['reason']}{proof}")
    return ("## Refuted by verification\n"
            "A second agent traced each finding below to the code and found it false, so it is not in the "
            "findings above. If you disagree, reproduce the reviewer's claim.\n" + "\n".join(rows) + "\n")


def append_refuted(doc: str, refuted: list[dict]) -> str:
    """`doc` with the refuted-findings appendix after it; unchanged when nothing was refuted."""
    extra = appendix(refuted)
    return f"{doc.rstrip()}\n\n{extra}" if extra else doc
