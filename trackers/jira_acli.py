"""trackers.jira_acli - fetch a Jira ticket with Atlassian's own CLI, `acli`.

`acli jira auth login --web` logs in once through the browser (OAuth), so
migite never handles a token. `acli jira workitem view KEY --fields ... --json`
returns the issue as Jira's JSON; trackers.jira_format turns it into a Ticket.
No model call and no agent involved: it works the same on every agent.

Jira Cloud only (acli's scope). Verified against acli 1.3.39.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from typing import Any, Callable

from .base import TICKET_KEY_RE, TicketError, TicketRef, Tracker, render
from .jira_format import FIELDS, issue_to_ticket

STATUS_TIMEOUT = 15
VIEW_TIMEOUT = 30
LOGIN_HINT = "run: acli jira auth login --web"


class JiraAcliTracker(Tracker):
    name = "jira-acli"

    def __init__(self, *, command: str = "acli", acceptance_field: str = "",
                 runner: Callable[..., Any] | None = None, which: Callable[[str], str | None] | None = None):
        self.command = command or "acli"
        self.acceptance_field = acceptance_field or ""
        self.runner = runner or subprocess.run
        self.which = which or shutil.which
        self._status: tuple[bool, str, str] | None = None   # (logged in, site, why)

    def _run(self, args: list[str], timeout: int):
        return self.runner([self.command, *args], capture_output=True, text=True, timeout=timeout,
                           stdin=subprocess.DEVNULL)

    # ── availability ──────────────────────────────────────────────────────────
    def status(self) -> tuple[bool, str, str]:
        """(logged in, site, why) from `acli jira auth status`, asked once per run."""
        if self._status is not None:
            return self._status
        if self.which(self.command) is None:
            self._status = (False, "", f"acli is not installed ({self.command!r} not found); see docs/tickets.md")
            return self._status
        try:
            proc = self._run(["jira", "auth", "status"], STATUS_TIMEOUT)
        except (OSError, subprocess.TimeoutExpired) as e:
            self._status = (False, "", f"acli did not answer: {e}")
            return self._status
        out = (proc.stdout or "") + (proc.stderr or "")
        m = re.search(r"^\s*Site:\s*(\S+)", out, re.MULTILINE)
        site = m.group(1).strip() if m else ""
        if proc.returncode == 0 and "Authenticated" in out:
            self._status = (True, site, f"acli logged in to {site or 'Jira'}")
        else:
            self._status = (False, "", f"acli is not logged in to Jira; {LOGIN_HINT}")
        return self._status

    def available(self, ref: TicketRef | None = None) -> tuple[bool, str]:
        ok, _, why = self.status()
        return ok, why

    # ── fetch ─────────────────────────────────────────────────────────────────
    def fetch(self, ref: TicketRef) -> str:
        if not TICKET_KEY_RE.match(ref.key):          # never pass an unvalidated key to a process
            raise TicketError(f"not a ticket key: {ref.key!r}")
        ok, site, why = self.status()
        if not ok:
            raise TicketError(why)
        fields = list(FIELDS) + ([self.acceptance_field] if self.acceptance_field else [])
        try:
            proc = self._run(["jira", "workitem", "view", ref.key, "--fields", ",".join(fields), "--json"], VIEW_TIMEOUT)
        except subprocess.TimeoutExpired as e:
            raise TicketError(f"acli timed out after {VIEW_TIMEOUT}s") from e
        except OSError as e:
            raise TicketError(f"acli could not be started: {e}") from e
        if proc.returncode != 0:
            raise TicketError(_acli_error(proc.stderr or proc.stdout) or f"acli exited {proc.returncode}")
        try:
            data = json.loads(proc.stdout)
        except ValueError as e:
            raise TicketError("acli did not return JSON") from e
        if not isinstance(data, dict) or "fields" not in data:
            raise TicketError("acli returned an unexpected response (no fields)")
        url = ref.url or (f"https://{site}/browse/{ref.key}" if site else "")
        return render(issue_to_ticket(data, ref, url=url, acceptance_field=self.acceptance_field, source=self.name))


def _acli_error(text: str) -> str:
    """acli's own message, without its '✗ Error:' prefix: 'Issue does not exist or ...'."""
    for line in (text or "").splitlines():
        line = re.sub(r"^\W*(Error:)?\s*", "", line.strip())
        if line:
            return line[:300]
    return ""
