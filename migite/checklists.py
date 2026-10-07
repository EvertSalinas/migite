"""checklists - what migite-review (Phase 3), migite-pr-review and migite-audit check, per stack.

A checklist is a markdown file, shipped in prompts/checklists/<stack>.md: rails.md for the
rails stack, generic.md for generic and for any stacks.<name> profile without a file of its
own. `prompts.dir` overrides it like any other prompt: <prompts.dir>/checklists/<stack>.md
replaces the sections it contains and keeps the rest, so a repo can change one dimension
without copying the file, and a profile (node.md) gets its own checks on top of generic.md.

    # Title (ignored)
    Expertise: Rails                    -> "a senior Rails engineer", "a senior Rails architect"

    ## review: <dimension>              migite-review's criteria for one reviewer
    ## pr_review: <dimension>           migite-pr-review's checks for one reviewer
    ## audit: <area>                    one migite-audit area; its first line is
    Files: app/models/**/*.rb ...       the globs (from the repo root) the auditor reads
    ## refute                           the refuter's "How to work" block (migite/verify.py)

review and pr_review dimensions are fixed (each is a model role, pr_review_<dimension>
and review_<dimension>), so a file can change their text but not add one. Audit areas are
open. Text before the first section, other than the Expertise line, is ignored.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

REVIEW_DIMENSIONS = ("correctness", "security", "test_coverage", "testing_plan", "frontend")
REVIEW_REQUIRED = ("correctness", "security", "test_coverage", "testing_plan")  # frontend: rails only
PR_REVIEW_DIMENSIONS = ("correctness", "security", "test_coverage", "conventions_and_migrations")
SECTION_RE = re.compile(r"^##\s+(\S+?)(?::\s*(.*?))?\s*$")
EXPERTISE_RE = re.compile(r"^Expertise:\s*(.+?)\s*$")
FILES_RE = re.compile(r"^Files:\s*(.*?)\s*$")


class ChecklistError(Exception):
    pass


@dataclass
class AuditArea:
    name: str
    globs: list[str]
    checks: str


@dataclass
class Checklist:
    stack: str
    expertise: str
    review: dict[str, str]
    pr_review: dict[str, str]
    audit: list[AuditArea]
    refute: str | None = None        # None = verify.py's own HOW_TO_WORK
    files: list[str] = field(default_factory=list)

    def review_dimensions(self) -> list[tuple[str, str]]:
        """(dimension, criteria) for the reviewers that always run, in order."""
        return [(d, self.review[d]) for d in REVIEW_REQUIRED]

    def pr_review_dimensions(self) -> list[tuple[str, str]]:
        return [(d, self.pr_review[d]) for d in PR_REVIEW_DIMENSIONS]


def parse(text: str, source: str) -> tuple[str | None, dict[tuple[str, str], str]]:
    """(expertise, {(tool, name): body}) from one checklist file, in file order."""
    expertise = None
    sections: dict[tuple[str, str], list[str]] = {}
    current: list[str] | None = None
    for n, line in enumerate(text.splitlines(), 1):
        m = SECTION_RE.match(line)
        if m:
            key = _section_key(m.group(1), (m.group(2) or "").strip(), source, n)
            if key in sections:
                raise ChecklistError(f"{source}:{n}: '## {line[3:].strip()}' appears twice")
            current = sections[key] = []
        elif current is not None:
            current.append(line)
        elif (e := EXPERTISE_RE.match(line)):
            expertise = e.group(1)
    return expertise, {k: "\n".join(v).strip("\n") for k, v in sections.items()}


def _section_key(tool: str, name: str, source: str, n: int) -> tuple[str, str]:
    where = f"{source}:{n}"
    if tool == "refute":
        if name:
            raise ChecklistError(f"{where}: '## refute' takes no name")
        return tool, ""
    known = {"review": REVIEW_DIMENSIONS, "pr_review": PR_REVIEW_DIMENSIONS, "audit": None}
    if tool not in known:
        raise ChecklistError(f"{where}: unknown section '## {tool}'; use review, pr_review, audit or refute")
    if not name:
        raise ChecklistError(f"{where}: '## {tool}' needs a name, e.g. '## {tool}: security'")
    if known[tool] is not None and name not in known[tool]:
        raise ChecklistError(f"{where}: '{name}' is not a {tool} dimension (each one is a model role, so the "
                             f"set is fixed): {', '.join(known[tool])}")
    return tool, name


def _audit_area(name: str, body: str, source: str) -> AuditArea:
    first, _, checks = body.partition("\n")
    m = FILES_RE.match(first)
    if not m or not m.group(1):
        raise ChecklistError(f"{source}: '## audit: {name}' must start with a 'Files: <globs>' line")
    return AuditArea(name, m.group(1).split(), checks.strip("\n"))


def shipped_path(stack: str, migite_home: str | Path) -> Path:
    """prompts/checklists/<stack>.md when migite ships one, else generic.md."""
    base = Path(migite_home) / "prompts" / "checklists"
    own = base / f"{stack}.md"
    return own if own.is_file() else base / "generic.md"


def load(stack: str, migite_home: str | Path, override_dir: str | Path | None = None) -> Checklist:
    """The checklist for <stack>: the shipped file, with <override_dir>/checklists/<stack>.md's
    sections on top. override_dir is the resolved prompts.dir (Config.override_dir)."""
    layers = [shipped_path(stack, migite_home)]
    if override_dir:
        own = Path(override_dir) / "checklists" / f"{stack}.md"
        if own.is_file():
            layers.append(own)
    expertise = None
    merged: dict[tuple[str, str], tuple[str, str]] = {}   # key -> (body, file it came from)
    for path in layers:
        if not path.is_file():
            raise ChecklistError(f"checklist missing: {path} - is the migite checkout intact?")
        exp, sections = parse(path.read_text(encoding="utf-8"), str(path))
        expertise = exp or expertise
        for key, body in sections.items():
            merged[key] = (body, str(path))

    def text_of(tool: str, required: tuple[str, ...]) -> dict[str, str]:
        out = {name: body for (t, name), (body, _) in merged.items() if t == tool}
        missing = [d for d in required if not out.get(d)]
        if missing:
            raise ChecklistError(f"the {stack} checklist has no {tool} section for: {', '.join(missing)} "
                                 f"(looked in {', '.join(str(p) for p in layers)})")
        return out

    refute = merged.get(("refute", ""))
    return Checklist(
        stack=stack,
        expertise=expertise or "software",
        review=text_of("review", REVIEW_REQUIRED),
        pr_review=text_of("pr_review", PR_REVIEW_DIMENSIONS),
        audit=[_audit_area(name, body, src) for (t, name), (body, src) in merged.items() if t == "audit"],
        refute=refute[0] if refute else None,
        files=[str(p) for p in layers],
    )


def log_labels(stack: str) -> tuple[str, str]:
    """What the two tooling logs are called in a prompt. migite passes rails' rubocop and
    rspec output, or a stack profile's lint and test output, in the same two places."""
    return ("Rubocop", "RSpec") if stack == "rails" else ("Lint", "Test")


# Code-fence language for a file excerpt in a prompt, by extension.
FENCE_LANG = {".rb": "ruby", ".erb": "erb", ".py": "python", ".js": "javascript", ".mjs": "javascript",
              ".jsx": "jsx", ".ts": "typescript", ".tsx": "tsx", ".go": "go", ".java": "java",
              ".kt": "kotlin", ".rs": "rust", ".php": "php", ".cs": "csharp", ".swift": "swift",
              ".sh": "bash", ".json": "json", ".yml": "yaml", ".yaml": "yaml", ".sql": "sql"}


def fence(stack: str, path: str) -> str:
    """rails keeps the `ruby` fence it always used; other stacks get the file's own language."""
    return "ruby" if stack == "rails" else FENCE_LANG.get(Path(path).suffix.lower(), "")
