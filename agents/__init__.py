"""agents - every agent CLI migite can drive, behind one interface.

Everything specific to one CLI lives in its own module here: flags, output
parsing, model ids, permission mapping, tool scopes, the variables it must not
inherit, its exit command, and the files it reads for project rules. The rest of
migite talks to the gateway (migite_call.py), which talks to this package.

Adding an agent: write agents/<name>.py with one Agent subclass, add it to
AGENTS below, add a fake CLI under tests/ for it, and add it to the table in
tests/test_agents_contract.py. The contract tests then run against it.

This package imports nothing else from migite, so the config module can read
model ids and agent names from it without a cycle.
"""

from __future__ import annotations

from typing import Any

from .base import (PERMISSION_ALIASES, PERMISSIONS, SCOPES, TIERS, Agent, AgentInfo, AskRequest,
                   AskResult, Launch, SessionRequest, normalize_permission)
from .claude import ClaudeAgent
from .cursor import CursorAgent
from .opencode import OpenCodeAgent

AGENTS: dict[str, type[Agent]] = {
    "claude": ClaudeAgent,
    "cursor": CursorAgent,
    "opencode": OpenCodeAgent,
}

DEFAULT = "claude"

__all__ = ["AGENTS", "DEFAULT", "PERMISSIONS", "PERMISSION_ALIASES", "SCOPES", "TIERS", "Agent", "AgentInfo",
           "AskRequest", "AskResult", "Launch", "SessionRequest", "names", "info", "get", "from_config",
           "normalize_permission"]


def names() -> tuple[str, ...]:
    return tuple(AGENTS)


def info(name: str) -> AgentInfo:
    return _class(name).info


def get(name: str | None = None, binary: str | None = None) -> Agent:
    return _class(name or DEFAULT)(binary=binary or None)


def from_config(cfg: Any) -> Agent:
    """The agent a loaded config selects: agent.backend, with agent.command as the binary."""
    return get(cfg.get("agent.backend") or DEFAULT, cfg.get("agent.command") or None)


def _class(name: str) -> type[Agent]:
    if name not in AGENTS:
        raise ValueError(f"unknown agent backend {name!r}; known: {', '.join(AGENTS)}")
    return AGENTS[name]
