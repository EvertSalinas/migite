"""agents.base - the interface between migite and an agent CLI.

migite never builds a CLI flag or parses CLI output. It describes what it wants
in its own vocabulary and hands that to the Agent for the configured backend:

  AskRequest      one headless call: prompt, model id, effort, JSON schema,
                  a neutral permission word, and named tool scopes
  SessionRequest  one interactive session: first prompt, permission, model
  Launch          what to run: argv, stdin, variables to set or unset
  AskResult       one headless call's output, in one shape for every CLI
  AgentInfo       everything else migite needs to know about a CLI

Adapters are pure translation. They never start a process and never read the
config; the gateway (migite/gateway.py) does both, the same way for every agent.
"""

from __future__ import annotations

import os
import shlex
from dataclasses import dataclass, field
from typing import Any, Mapping

# ── migite's own vocabulary ───────────────────────────────────────────────────

# Permission words. Each adapter maps them onto its CLI's flags.
#   auto   approve every tool use without asking
#   edits  approve file edits, ask for anything else
#   plan   read-only planning mode
#   ask    the CLI's own default: ask before acting
#   none   pass no permission flag at all
PERMISSIONS: tuple[str, ...] = ("auto", "edits", "plan", "ask", "none")

# Claude Code's names, accepted everywhere a permission word is (configs written
# before the neutral words existed keep working).
PERMISSION_ALIASES: Mapping[str, str] = {
    "bypassPermissions": "auto",
    "acceptEdits": "edits",
    "default": "ask",
}

# Named tool scopes a headless call can ask for. An agent that can restrict a
# call to exactly these tools maps the name to its own tool ids; one that
# cannot leaves it out of AgentInfo.scopes, and the gateway refuses the call.
SCOPES: tuple[str, ...] = ("jira.read",)

TIERS: tuple[str, ...] = ("fast", "standard", "strong")


def normalize_permission(value: str | None) -> str:
    """A permission word or alias as the neutral word; empty means none."""
    if value is None or str(value) == "":
        return "none"
    word = PERMISSION_ALIASES.get(str(value), str(value))
    if word not in PERMISSIONS:
        known = ", ".join(PERMISSIONS + tuple(PERMISSION_ALIASES))
        raise ValueError(f"unknown permission {value!r}; known: {known}")
    return word


# ── requests, launches, results ───────────────────────────────────────────────

@dataclass
class AskRequest:
    prompt: str
    model: str | None = None          # None or "" = the CLI's own default
    effort: str | None = None         # None = no effort flag
    schema: dict | None = None        # only sent when the agent has structured_output
    permission: str = "none"
    scopes: tuple[str, ...] = ()      # only sent when the agent maps every one


@dataclass
class SessionRequest:
    prompt: str
    permission: str = "none"
    model: str | None = None


@dataclass
class Launch:
    argv: list[str]
    stdin: str | None = None
    env_set: dict[str, str] = field(default_factory=dict)
    env_unset: tuple[str, ...] = ()

    def environ(self, base: Mapping[str, str] | None = None) -> dict[str, str]:
        env = {k: v for k, v in (os.environ if base is None else base).items() if k not in self.env_unset}
        env.update(self.env_set)
        return env

    def shell(self) -> str:
        """One shell command line for bash to run: `env -u X K=V cmd args...`."""
        prefix: list[str] = []
        if self.env_unset or self.env_set:
            prefix.append("env")
            for name in self.env_unset:
                prefix += ["-u", name]
            prefix += [f"{k}={v}" for k, v in self.env_set.items()]
        return " ".join(shlex.quote(a) for a in prefix + self.argv)


@dataclass
class AskResult:
    text: str = ""
    structured: Any = None
    ok: bool = True
    error: str = ""
    model: str = ""
    duration_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cost_usd: float = 0.0
    raw: dict = field(default_factory=dict)
    is_envelope: bool = True          # False when stdout wasn't the CLI's machine format


def plain_text_result(stdout: str, returncode: int, model: str) -> AskResult:
    """The fallback every parser uses when stdout isn't the expected format."""
    return AskResult(text=stdout.strip(), ok=returncode == 0, model=model, is_envelope=False,
                     error="" if returncode == 0 else stdout[-400:])


# ── the interface ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class AgentInfo:
    name: str                          # config value: agent.backend
    display_name: str                  # shown in messages: "Claude Code is working"
    default_binary: str
    models: Mapping[str, str | None]   # tier -> model id; None = don't pass a model
    structured_output: bool = False    # validates a reply against a JSON schema
    effort: bool = False               # takes an effort level
    usage: bool = False                # reports tokens and cost
    scopes: Mapping[str, tuple[str, ...]] = field(default_factory=dict)   # scope -> tool ids
    prompt_via: str = "stdin"          # how a headless prompt travels: "stdin" | "arg"
    max_arg_bytes: int = 100_000       # Linux caps one argv string at 128 KB
    env_unset: tuple[str, ...] = ()    # variables that must not reach the CLI
    exit_hint: str = "exit the session"
    instruction_files: str = "its instruction files"   # what the CLI reads for project rules


class Agent:
    """One agent CLI. Subclasses set `info` and implement the three translations."""

    info: AgentInfo

    def __init__(self, binary: str | None = None):
        self.binary = binary or self.info.default_binary

    @property
    def name(self) -> str:
        return self.info.name

    def supports(self, capability: str) -> bool:
        """structured_output | effort | usage | scope:<name>"""
        if capability.startswith("scope:"):
            return capability[len("scope:"):] in self.info.scopes
        return bool(getattr(self.info, capability, False))

    def tools_for(self, scopes: tuple[str, ...]) -> list[str]:
        tools: list[str] = []
        for scope in scopes:
            tools += list(self.info.scopes[scope])
        return tools

    def ask_launch(self, req: AskRequest) -> Launch:
        raise NotImplementedError

    def parse(self, stdout: str, returncode: int, req: AskRequest) -> AskResult:
        raise NotImplementedError

    def session_launch(self, req: SessionRequest) -> Launch:
        raise NotImplementedError

    def version_argv(self) -> list[str]:
        return [self.binary, "--version"]

    def describe(self) -> dict:
        i = self.info
        return {
            "name": i.name, "display_name": i.display_name, "binary": self.binary,
            "structured_output": i.structured_output, "effort": i.effort, "usage": i.usage,
            "scopes": sorted(i.scopes), "prompt_via": i.prompt_via, "max_arg_bytes": i.max_arg_bytes,
            "env_unset": list(i.env_unset), "exit_hint": i.exit_hint,
            "instruction_files": i.instruction_files, "models": dict(i.models),
        }
