"""trackers.jira_agent - fetch a Jira ticket through the configured agent's MCP tools.

The fallback when acli isn't installed or logged in: one headless call on the `jira` role,
restricted to the `jira.read` tool scope. Only agents whose adapter maps that
scope can run it (Claude Code, through claude.ai's Atlassian connector); on any
other agent it reports itself unavailable instead of running with every tool.
Costs one model call and returns what the model writes, so jira-acli is
preferred whenever it is logged in.
"""

from __future__ import annotations

from .base import TicketError, TicketRef, Tracker

FAILED_MARKER = "JIRA_FETCH_FAILED"


def fetch_prompt(ref: TicketRef) -> str:
    link = f" ({ref.url})" if ref.url else ""
    return f"""Fetch the Jira ticket {ref.key}{link} using the Atlassian MCP tools. If a URL is given, try its hostname as the cloudId first; otherwise use getAccessibleAtlassianResources to find it.

Output ONLY this structure, no preamble:

## Jira ticket: {ref.key}
**Title:** <ticket summary>
**Type:** <issue type>
**Priority:** <priority>
**Status:** <status>
**Description:**
<full description, plain text, Jira markup stripped>

**Acceptance criteria:**
<any acceptance-criteria / definition-of-done content found in the description, or "None specified in the ticket">

If the ticket cannot be fetched (no MCP access, not authenticated, wrong key, no permission), output exactly:
{FAILED_MARKER}: <short reason>"""


class JiraAgentTracker(Tracker):
    name = "jira-agent"

    def __init__(self, gateway=None):
        # The gateway is gateway, already configured for the repo; passed in so
        # this package imports nothing from migite at module load.
        self.gateway = gateway

    def available(self, ref: TicketRef | None = None) -> tuple[bool, str]:
        if self.gateway is None:
            return False, "no agent gateway configured"
        agent = self.gateway.AGENT
        if not self.gateway.supports("scope:jira.read"):
            return False, f"the {agent.info.display_name} agent can't restrict a call to the jira.read scope"
        return True, f"{agent.info.display_name} with its Atlassian MCP tools (one model call)"

    def fetch(self, ref: TicketRef) -> str:
        ok, why = self.available(ref)
        if not ok:
            raise TicketError(why)
        try:
            res = self.gateway.call_agent(fetch_prompt(ref), "jira", label="jira-fetch", tool="migite",
                                          scopes=("jira.read",), permission="auto")
        except self.gateway.AgentError as e:
            raise TicketError(str(e)) from e
        text = res.text.strip()
        if not text:
            raise TicketError("the agent returned nothing")
        if text.startswith(FAILED_MARKER) or f"\n{FAILED_MARKER}" in text:
            reason = text.split(FAILED_MARKER, 1)[1].lstrip(": ").strip()
            raise TicketError(reason or "the agent could not fetch the ticket")
        return text + "\n"
