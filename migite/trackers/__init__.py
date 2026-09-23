"""trackers - where migite gets a ticket's content from, behind one interface.

Each source lives in its own module and turns a ticket reference into the same
markdown (trackers.base.render). migite/tickets.py picks the source from the
config (tracker.provider) and is the only thing the rest of migite calls.

  jira-acli    Atlassian's CLI, logged in once through the browser. No model call,
               no token for migite to handle, works on every agent. Preferred.
  jira-agent   One headless call through the agent's Atlassian MCP tools, inside
               the jira.read scope. Only on agents that map that scope.

Adding a source: write migite/trackers/<name>.py with one Tracker subclass, add it to
SOURCES in migite/tickets.py and to the tracker.provider values in
migite/config.py, and add tests with a stubbed transport (see the fake acli runner in the tests).
"""

from .base import (BROWSE_RE, TICKET_KEY_RE, InvalidTicketRef, Ticket, TicketError, TicketRef, Tracker,
                   parse_ref, render)

__all__ = ["BROWSE_RE", "TICKET_KEY_RE", "InvalidTicketRef", "Ticket", "TicketError", "TicketRef", "Tracker",
           "parse_ref", "render"]
