"""trackers.base - the interface between migite and a ticket source.

migite needs one thing from an issue tracker: given a ticket reference, the
ticket's content as markdown the planner can read. Each source (Jira through acli,
Jira through the agent's MCP tools, later others) lives in its own module and
turns a TicketRef into a Ticket; render() turns every Ticket into the same
markdown, so the planner never knows where it came from.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

TICKET_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]*-\d+$")
BROWSE_RE = re.compile(r"/browse/([^/?#]+)", re.IGNORECASE)


class TicketError(Exception):
    """A source was tried and could not return the ticket."""


class InvalidTicketRef(ValueError):
    """The input is not a ticket key or a ticket URL."""


@dataclass(frozen=True)
class TicketRef:
    key: str                 # "BB-1234", always upper case
    url: str = ""            # the browse URL when one was given
    base_url: str = ""       # scheme://host of that URL, e.g. https://acme.atlassian.net


def parse_ref(raw: str) -> TicketRef:
    """A ticket key ("bb-1234") or a browse URL (".../browse/BB-1234") as a TicketRef.
    The key is validated before it is ever put into a URL or a path."""
    s = (raw or "").strip()
    url = base = ""
    m = BROWSE_RE.search(s)
    if m:
        parsed = urlparse(s)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise InvalidTicketRef(raw)
        url, base = s, f"{parsed.scheme}://{parsed.netloc}"
        s = m.group(1)
    if not TICKET_KEY_RE.match(s):
        raise InvalidTicketRef(raw)
    return TicketRef(key=s.upper(), url=url, base_url=base)


@dataclass
class Ticket:
    key: str
    title: str = ""
    type: str = ""
    priority: str = ""
    status: str = ""
    description: str = ""
    acceptance: str = ""      # "" = none found
    url: str = ""
    labels: list[str] = field(default_factory=list)
    source: str = ""          # which tracker produced it


def render(ticket: Ticket) -> str:
    """The markdown every source produces and the planner reads."""
    lines = [
        f"## Jira ticket: {ticket.key}",
        f"**Title:** {ticket.title or '(no title)'}",
        f"**Type:** {ticket.type or 'unknown'}",
        f"**Priority:** {ticket.priority or 'unknown'}",
        f"**Status:** {ticket.status or 'unknown'}",
    ]
    if ticket.labels:
        lines.append(f"**Labels:** {', '.join(ticket.labels)}")
    if ticket.url:
        lines.append(f"**Link:** {ticket.url}")
    lines += ["**Description:**", ticket.description.strip() or "(empty)", "",
              "**Acceptance criteria:**", ticket.acceptance.strip() or "None specified in the ticket"]
    return "\n".join(lines) + "\n"


class Tracker:
    """One ticket source. `available()` says whether it can run here, and why not."""

    name = ""

    def available(self) -> tuple[bool, str]:
        raise NotImplementedError

    def fetch(self, ref: TicketRef) -> str:
        """The ticket as markdown (see render). Raises TicketError."""
        raise NotImplementedError
