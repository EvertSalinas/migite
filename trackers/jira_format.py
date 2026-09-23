"""trackers.jira_format - Jira's issue JSON as a migite Ticket.

Shared by every Jira source: the issue shape (`{"key", "fields": {...}}`) is the
same whether it comes from `acli jira workitem view --json` or elsewhere. The
description arrives in Atlassian Document Format (Jira Cloud) or as wiki markup
(Server / Data Center); both become markdown-ish text a planner can read.
"""

from __future__ import annotations

import re
from typing import Any

from .base import Ticket, TicketRef

AC_HEADING_RE = re.compile(r"^\W*(acceptance criteria|definition of done)\b\W*$", re.IGNORECASE)
HEADING_RE = re.compile(r"^(#{1,6}\s|h[1-6]\.\s|\*\*[^*]+\*\*\s*$)")
# The fields migite renders; request only these.
FIELDS = ("summary", "issuetype", "priority", "status", "labels", "description")


def issue_to_ticket(data: dict, ref: TicketRef, *, url: str = "", acceptance_field: str = "", source: str = "") -> Ticket:
    """One Jira issue as a Ticket. Acceptance criteria come from `acceptance_field`
    when it is set and filled, else from an 'Acceptance criteria' section of the description."""
    f = data.get("fields") or {}
    description = field_text(f.get("description"))
    acceptance = field_text(f.get(acceptance_field)) if acceptance_field else ""
    if not acceptance:
        acceptance = acceptance_from(description)
    return Ticket(
        key=str(data.get("key") or ref.key).upper(),
        title=str(f.get("summary") or ""),
        type=_name(f.get("issuetype")),
        priority=_name(f.get("priority")),
        status=_name(f.get("status")),
        description=description,
        acceptance=acceptance,
        url=url,
        labels=[str(label) for label in (f.get("labels") or [])],
        source=source,
    )


def _name(obj: Any) -> str:
    return str(obj.get("name") or "") if isinstance(obj, dict) else ""


def field_text(value: Any) -> str:
    """A field's value as text: Atlassian Document Format (API v3), wiki markup
    (API v2), a plain string, or a list of either."""
    if value is None:
        return ""
    if isinstance(value, dict) and value.get("type") == "doc":
        return adf_to_text(value).strip()
    if isinstance(value, str):
        return wiki_to_markdown(value).strip()
    if isinstance(value, list):
        return "\n".join(field_text(v) for v in value).strip()
    if isinstance(value, dict) and "value" in value:           # select-list custom fields
        return str(value["value"])
    return str(value)


def acceptance_from(description: str) -> str:
    """The section under an 'Acceptance criteria' / 'Definition of done' heading, up
    to the next heading, or "" when the description has none."""
    lines = description.splitlines()
    for i, line in enumerate(lines):
        bare = re.sub(r"^(#{1,6}\s*|h[1-6]\.\s*)", "", line).strip().strip("*").strip()
        if AC_HEADING_RE.match(bare):
            out = []
            for nxt in lines[i + 1:]:
                if HEADING_RE.match(nxt.strip()):
                    break
                out.append(nxt)
            return "\n".join(out).strip()
    return ""


# ── Atlassian Document Format → markdown-ish text ─────────────────────────────

def adf_to_text(node: Any) -> str:
    return _adf(node, depth=0).strip() + "\n"


def _children(node: dict, depth: int) -> str:
    return "".join(_adf(c, depth) for c in node.get("content") or [])


def _adf(node: Any, depth: int) -> str:
    if not isinstance(node, dict):
        return ""
    kind = node.get("type")
    attrs = node.get("attrs") or {}
    if kind == "text":
        return str(node.get("text") or "")
    if kind == "hardBreak":
        return "\n"
    if kind in ("mention", "emoji", "status", "date"):
        return str(attrs.get("text") or attrs.get("shortName") or attrs.get("timestamp") or "")
    if kind in ("inlineCard", "blockCard", "embedCard"):
        return str(attrs.get("url") or "")
    if kind == "paragraph":
        return _children(node, depth) + "\n\n"
    if kind == "heading":
        return "#" * int(attrs.get("level") or 2) + " " + _children(node, depth).strip() + "\n\n"
    if kind in ("bulletList", "orderedList"):
        out = []
        for n, item in enumerate(node.get("content") or [], start=1):
            marker = f"{n}." if kind == "orderedList" else "-"
            body = _children(item, depth + 1).strip()
            out.append("  " * depth + f"{marker} {body}")
        return "\n".join(out) + ("\n\n" if depth == 0 else "\n")
    if kind == "codeBlock":
        return "```" + str(attrs.get("language") or "") + "\n" + _children(node, depth).rstrip("\n") + "\n```\n\n"
    if kind == "blockquote":
        inner = _children(node, depth).strip()
        return "\n".join("> " + line for line in inner.splitlines()) + "\n\n"
    if kind == "rule":
        return "---\n\n"
    if kind == "table":
        rows = []
        for row in node.get("content") or []:
            cells = [_children(cell, depth).strip().replace("\n", " ") for cell in row.get("content") or []]
            rows.append("| " + " | ".join(cells) + " |")
        return "\n".join(rows) + "\n\n"
    if kind in ("mediaSingle", "mediaGroup", "media"):
        return "[attachment]\n\n"
    return _children(node, depth)          # doc, panel, expand, listItem, and anything unknown


def wiki_to_markdown(text: str) -> str:
    """Just enough Jira wiki markup (API v2) conversion for a planner to read."""
    out = []
    for line in text.splitlines():
        m = re.match(r"^h([1-6])\.\s+(.*)$", line)
        if m:
            out.append("#" * int(m.group(1)) + " " + m.group(2))
        elif re.match(r"^\{(code|noformat)(:[^}]*)?\}\s*$", line.strip()):
            out.append("```")
        else:
            out.append(line)
    return "\n".join(out)
