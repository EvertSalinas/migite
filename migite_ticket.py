#!/usr/bin/env python3
"""migite_ticket - ticket references and ticket content, for every migite tool.

One parser for ticket keys and browse URLs, and one fetch that picks a ticket
source from the config (tracker.provider) so no phase knows how a ticket is
retrieved. The sources live in trackers/.

  tracker.provider  auto        jira-acli when acli is installed and logged in, else
                                jira-agent when the agent can run a jira.read-scoped
                                call, else none. A source that fails falls through to
                                the next, with a warning.
                    jira-acli   only Atlassian's CLI (acli jira auth login --web)
                    jira-agent  only the agent's Atlassian MCP tools
                    none        never fetch; the key still names the task

CLI (used by migite, and by hand as `migite-ticket`):
  migite_ticket.py parse <key-or-url> [--shell]
        {"key", "url", "base_url"} as JSON, or TICKET_KEY=/TICKET_URL= for bash; exit 1 if invalid
  migite_ticket.py fetch <key-or-url> [--out FILE] [--source NAME]
        the ticket as markdown on stdout or in FILE (written only on success).
        Exit 0 fetched, 1 every available source failed, 2 no source available
  migite_ticket.py sources [<key-or-url>]
        each source, whether it can run here, and why; which one auto would use
--repo-root DIR (before or after the subcommand) selects whose .migite.yml applies.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

import migite_config
from trackers import InvalidTicketRef, TicketError, TicketRef, Tracker, parse_ref
from trackers.jira_acli import JiraAcliTracker
from trackers.jira_agent import JiraAgentTracker

SOURCES = ("jira-acli", "jira-agent")
PROVIDERS = ("auto",) + SOURCES + ("none",)


class NoSource(Exception):
    """No configured ticket source can run here. .reasons says why, per source."""

    def __init__(self, reasons: list[tuple[str, str]]):
        self.reasons = reasons
        super().__init__("; ".join(f"{name}: {why}" for name, why in reasons) or "tracker.provider is none")


def build(name: str, cfg: migite_config.Config, *, runner: Callable[..., Any] | None = None,
          which: Callable[[str], str | None] | None = None, gateway: Any = None) -> Tracker:
    if name == "jira-acli":
        return JiraAcliTracker(command=cfg.get("tracker.jira.acli") or "acli",
                               acceptance_field=cfg.get("tracker.jira.acceptance_field") or "",
                               runner=runner, which=which)
    if name == "jira-agent":
        return JiraAgentTracker(gateway=gateway)
    raise ValueError(f"unknown ticket source {name!r}; known: {', '.join(SOURCES)}")


def candidates(cfg: migite_config.Config, *, source: str | None = None, **kw) -> list[Tracker]:
    provider = source or cfg.get("tracker.provider") or "auto"
    if provider == "none":
        return []
    names = SOURCES if provider == "auto" else (provider,)
    return [build(n, cfg, **kw) for n in names]


def fetch(ref: TicketRef, cfg: migite_config.Config, *, source: str | None = None, **kw) -> tuple[str, str]:
    """(markdown, source name) from the first source that can run and succeeds."""
    reasons: list[tuple[str, str]] = []
    last_error: TicketError | None = None
    for tracker in candidates(cfg, source=source, **kw):
        ok, why = tracker.available(ref)
        if not ok:
            reasons.append((tracker.name, why))
            continue
        try:
            return tracker.fetch(ref), tracker.name
        except TicketError as e:
            print(f"      ⚠ {tracker.name}: {e}", file=sys.stderr, flush=True)
            last_error = e
            reasons.append((tracker.name, str(e)))
    if last_error is not None:
        raise last_error
    raise NoSource(reasons)


# ── CLI ───────────────────────────────────────────────────────────────────────

def _load(args: argparse.Namespace) -> migite_config.Config:
    return migite_config.load(args.repo_root or os.getcwd())


def _gateway(cfg: migite_config.Config):
    import migite_call   # lazy: `parse` must work without an agent
    migite_call.configure_from(cfg)
    return migite_call


def _cmd_parse(args: argparse.Namespace) -> int:
    try:
        ref = parse_ref(args.ref)
    except InvalidTicketRef:
        print(f"✘ not a ticket key or ticket URL: {args.ref!r}", file=sys.stderr)
        return 1
    if args.shell:
        print(f"TICKET_KEY={shlex.quote(ref.key)}")
        print(f"TICKET_URL={shlex.quote(ref.url)}")
    else:
        print(json.dumps({"key": ref.key, "url": ref.url, "base_url": ref.base_url}))
    return 0


def _write_atomic(path: str, text: str) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".ticket-")
    with os.fdopen(fd, "w") as fh:
        fh.write(text)
    os.replace(tmp, target)


def _cmd_fetch(args: argparse.Namespace) -> int:
    try:
        ref = parse_ref(args.ref)
    except InvalidTicketRef:
        print(f"✘ not a ticket key or ticket URL: {args.ref!r}", file=sys.stderr)
        return 1
    cfg = _load(args)
    try:
        text, used = fetch(ref, cfg, source=args.source, gateway=_gateway(cfg))
    except NoSource as e:
        print(f"✘ no ticket source available for {ref.key}: {e}", file=sys.stderr)
        return 2
    except TicketError as e:
        print(f"✘ could not fetch {ref.key}: {e}", file=sys.stderr)
        return 1
    if args.out:
        _write_atomic(args.out, text)
    else:
        sys.stdout.write(text)
    print(f"✔ fetched {ref.key} via {used}", file=sys.stderr)
    return 0


def _cmd_sources(args: argparse.Namespace) -> int:
    ref = None
    if args.ref:
        try:
            ref = parse_ref(args.ref)
        except InvalidTicketRef:
            print(f"✘ not a ticket key or ticket URL: {args.ref!r}", file=sys.stderr)
            return 1
    cfg = _load(args)
    provider = cfg.get("tracker.provider") or "auto"
    print(f"tracker.provider: {provider}")
    chosen = None
    for tracker in candidates(cfg, gateway=_gateway(cfg)):
        ok, why = tracker.available(ref)
        mark = "✔" if ok else "·"
        if ok and chosen is None:
            chosen = tracker.name
        print(f"  {mark} {tracker.name:<11} {why}")
    print(f"  → {chosen or 'none'} would be used" + ("" if ref else " (pass a ticket URL to use its site)"))
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(prog="migite-ticket", description="ticket references and ticket content")
    ap.add_argument("--repo-root", default=None)
    sub = ap.add_subparsers(dest="command", required=True)
    parsers = {
        "parse": sub.add_parser("parse", help="a ticket key or browse URL as key, URL, and site"),
        "fetch": sub.add_parser("fetch", help="the ticket's content as markdown"),
        "sources": sub.add_parser("sources", help="which ticket source would be used here, and why"),
    }
    for p in parsers.values():
        p.add_argument("--repo-root", dest="repo_root", default=argparse.SUPPRESS)
    parsers["parse"].add_argument("ref")
    parsers["parse"].add_argument("--shell", action="store_true", help="TICKET_KEY= / TICKET_URL= for bash to eval")
    parsers["fetch"].add_argument("ref")
    parsers["fetch"].add_argument("--out", default=None, help="write here instead of stdout (only on success)")
    parsers["fetch"].add_argument("--source", choices=SOURCES, default=None, help="use only this source")
    parsers["sources"].add_argument("ref", nargs="?", default=None)
    args = ap.parse_args()
    handler = {"parse": _cmd_parse, "fetch": _cmd_fetch, "sources": _cmd_sources}[args.command]
    try:
        sys.exit(handler(args))
    except migite_config.ConfigError as e:
        print(f"✘ config error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
