#!/usr/bin/env python3
"""migite_agent — the agent-CLI backends migite can drive.

migite needs three things from an agent CLI:

  1. a HEADLESS call: prompt in, final text out, ideally with usage — the planner,
     reviewers, knowledge capture, amendments, and the standalone tools;
  2. an INTERACTIVE session opened with an initial prompt in the repo, that returns
     when the human exits — implement, gate fixes, PR description;
  3. optional extras only some CLIs have: schema-validated structured output, an
     --effort level, and a scoped tool allowlist (the Jira fetch).

Each Backend below maps those onto one CLI's flags and parses its output into one
normalised shape. Everything else in migite talks to `migite_claude.call_claude`
(Python) or `claude_print` / `run_phase` (bash), which pick the backend from the
config (`agent.backend`, env `MIGITE_AGENT`) and never see the flags.

Capabilities a backend lacks degrade explicitly: no structured output → the
reviewer parses markdown; no tool allowlist → the Jira fetch is skipped with a
warning; no effort → the flag is dropped; no usage → the ledger records the call
with zero tokens and cost.

Verified against: Claude Code CLI 2.1.x (`--output-format json` envelope, tested
live), Cursor CLI 2026.03 (`cursor-agent --help` and cursor.com/docs/cli), OpenCode
docs for `run --format json` event shapes. Model ids for Cursor and OpenCode are
NOT pinned by default: `--model` is omitted so each CLI uses its own default until
you pin tiers in `.migite.yml` (`cursor-agent --list-models`, `opencode models`).

CLI (used by bash):
  migite_agent.py binary                 → the backend's executable name
  migite_agent.py interactive --permission-mode M --prompt-file F [--model X]
                                         → one shell-quoted command line to eval
  migite_agent.py capabilities           → JSON
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import migite_config

# Permission vocabulary is Claude Code's (the config enum); each backend maps it.
AUTO_APPROVE_MODES = ("bypassPermissions", "acceptEdits")


@dataclass
class Parsed:
    """Normalised result of one headless call."""
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
    is_envelope: bool = True   # False when stdout wasn't the expected machine format


class Backend:
    name = ""
    default_binary = ""
    # what migite can rely on
    structured_output = False
    effort = False
    tool_allowlist = False
    usage = False
    prompt_via = "stdin"   # "stdin" | "arg"

    def __init__(self, binary: str | None = None):
        self.binary = binary or self.default_binary

    # ── headless ─────────────────────────────────────────────────────────────
    def headless_argv(self, *, model: str | None, effort: str | None, schema: dict | None,
                      permission_mode: str | None, allowed_tools: list[str] | None,
                      prompt: str) -> tuple[list[str], str | None]:
        """(argv, stdin_payload). stdin_payload is None when the prompt travels as an argument."""
        raise NotImplementedError

    def parse(self, stdout: str, returncode: int, *, requested_model: str) -> Parsed:
        raise NotImplementedError

    # ── interactive ──────────────────────────────────────────────────────────
    def interactive_argv(self, *, prompt: str, permission_mode: str | None, model: str | None) -> list[str]:
        raise NotImplementedError

    def env(self) -> dict[str, str]:
        return dict(os.environ)

    def capabilities(self) -> dict:
        return {"name": self.name, "binary": self.binary, "structured_output": self.structured_output,
                "effort": self.effort, "tool_allowlist": self.tool_allowlist, "usage": self.usage,
                "prompt_via": self.prompt_via}


# ── Claude Code ───────────────────────────────────────────────────────────────

class ClaudeBackend(Backend):
    name = "claude"
    default_binary = "claude"
    structured_output = True
    effort = True
    tool_allowlist = True
    usage = True
    prompt_via = "stdin"

    def env(self) -> dict[str, str]:
        # Claude Code sets CLAUDECODE in its own sessions and a nested `claude` refuses
        # to start while it's set.
        return {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}

    def headless_argv(self, *, model, effort, schema, permission_mode, allowed_tools, prompt):
        argv = [self.binary, "--print", "--output-format", "json"]
        if model:
            argv += ["--model", model]
        if effort and effort != "none" and "haiku" not in (model or ""):
            argv += ["--effort", effort]
        if schema is not None:
            argv += ["--json-schema", json.dumps(schema)]
        if permission_mode and permission_mode != "none":
            argv += ["--permission-mode", permission_mode]
        if allowed_tools:
            argv += ["--allowedTools", " ".join(allowed_tools)]
        return argv, prompt

    def parse(self, stdout, returncode, *, requested_model):
        try:
            d = json.loads(stdout)
        except (ValueError, TypeError):
            d = None
        if not isinstance(d, dict) or "result" not in d:
            return Parsed(text=stdout.strip(), ok=returncode == 0, model=requested_model,
                          is_envelope=False, error="" if returncode == 0 else stdout[-400:])
        usage = d.get("usage") or {}
        mu = d.get("modelUsage") or {}
        model = requested_model or (next(iter(mu)) if len(mu) == 1 else "")
        ok = returncode == 0 and not d.get("is_error", False)
        p = Parsed(
            text=str(d.get("result") or "").strip(), structured=d.get("structured_output"), ok=ok,
            error="" if ok else str(d.get("result") or "")[:400], model=model,
            duration_ms=int(d.get("duration_ms") or 0),
            input_tokens=int(usage.get("input_tokens") or 0), output_tokens=int(usage.get("output_tokens") or 0),
            cache_read_input_tokens=int(usage.get("cache_read_input_tokens") or 0),
            cache_creation_input_tokens=int(usage.get("cache_creation_input_tokens") or 0),
            cost_usd=float(d.get("total_cost_usd") or 0.0), raw=d,
        )
        return p

    def interactive_argv(self, *, prompt, permission_mode, model):
        argv = [self.binary]
        if permission_mode and permission_mode != "none":
            argv += ["--permission-mode", permission_mode]
        if model:
            argv += ["--model", model]
        argv += ["--", prompt]
        return argv


# ── Cursor CLI ────────────────────────────────────────────────────────────────

class CursorBackend(Backend):
    """cursor-agent (alias `agent`). Headless: `-p --output-format json`, prompt as an
    argument, `--trust` to skip the workspace-trust prompt, `--force` to actually apply
    changes (without it headless mode only proposes them). No usage/cost in the JSON."""
    name = "cursor"
    default_binary = "cursor-agent"
    structured_output = False
    effort = False
    tool_allowlist = False
    usage = False
    prompt_via = "arg"

    def _perm(self, permission_mode, *, headless: bool) -> list[str]:
        out = []
        if permission_mode in AUTO_APPROVE_MODES:
            out.append("--force")
        elif permission_mode == "plan":
            out += ["--mode", "plan"]
        if headless:
            out.append("--trust")
        return out

    def headless_argv(self, *, model, effort, schema, permission_mode, allowed_tools, prompt):
        argv = [self.binary, "-p", "--output-format", "json"] + self._perm(permission_mode, headless=True)
        if model:
            argv += ["--model", model]
        argv.append(prompt)
        return argv, None

    def parse(self, stdout, returncode, *, requested_model):
        d = _last_json_object(stdout)
        if not isinstance(d, dict) or "result" not in d:
            return Parsed(text=stdout.strip(), ok=returncode == 0, model=requested_model,
                          is_envelope=False, error="" if returncode == 0 else stdout[-400:])
        ok = returncode == 0 and not d.get("is_error", False)
        return Parsed(text=str(d.get("result") or "").strip(), ok=ok,
                      error="" if ok else str(d.get("result") or "")[:400], model=requested_model,
                      duration_ms=int(d.get("duration_ms") or 0), raw=d)

    def interactive_argv(self, *, prompt, permission_mode, model):
        argv = [self.binary] + self._perm(permission_mode, headless=False)
        if model:
            argv += ["--model", model]
        argv.append(prompt)
        return argv


# ── OpenCode ──────────────────────────────────────────────────────────────────

class OpenCodeBackend(Backend):
    """opencode. Headless: `run --format json <message>` → JSONL events; the final text
    is the concatenation of `text` events' part.text, cost and tokens are summed from
    `step_finish` events, an `error` event marks failure. `--auto` auto-approves.
    Models are `provider/model`. Interactive: `opencode --prompt "<prompt>"`."""
    name = "opencode"
    default_binary = "opencode"
    structured_output = False
    effort = False
    tool_allowlist = False
    usage = True
    prompt_via = "arg"

    def _perm(self, permission_mode) -> list[str]:
        return ["--auto"] if permission_mode in AUTO_APPROVE_MODES else []

    def headless_argv(self, *, model, effort, schema, permission_mode, allowed_tools, prompt):
        argv = [self.binary, "run", "--format", "json"] + self._perm(permission_mode)
        if model:
            argv += ["--model", model]
        argv.append(prompt)
        return argv, None

    def parse(self, stdout, returncode, *, requested_model):
        texts: list[str] = []
        p = Parsed(model=requested_model)
        saw_event = False
        for line in stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if not isinstance(ev, dict) or "type" not in ev:
                continue
            saw_event = True
            part = ev.get("part") or {}
            t = ev.get("type")
            if t == "text":
                texts.append(str(part.get("text") or ""))
            elif t == "step_finish":
                tok = part.get("tokens") or {}
                cache = tok.get("cache") or {}
                p.cost_usd += float(part.get("cost") or 0.0)
                p.input_tokens += int(tok.get("input") or 0)
                p.output_tokens += int(tok.get("output") or 0) + int(tok.get("reasoning") or 0)
                p.cache_read_input_tokens += int(cache.get("read") or 0)
                p.cache_creation_input_tokens += int(cache.get("write") or 0)
            elif t == "error":
                err = ev.get("error") or {}
                msg = (err.get("data") or {}).get("message") or err.get("name") or "opencode error"
                p.ok = False
                p.error = str(msg)[:400]
        if not saw_event:
            return Parsed(text=stdout.strip(), ok=returncode == 0, model=requested_model,
                          is_envelope=False, error="" if returncode == 0 else stdout[-400:])
        p.text = "\n".join(t for t in texts if t).strip()
        if returncode != 0:
            p.ok = False
            p.error = p.error or f"opencode exited {returncode}"
        p.raw = {"events": saw_event}
        return p

    def interactive_argv(self, *, prompt, permission_mode, model):
        argv = [self.binary, "--prompt", prompt] + self._perm(permission_mode)
        if model:
            argv += ["--model", model]
        return argv


BACKENDS: dict[str, type[Backend]] = {
    "claude": ClaudeBackend,
    "cursor": CursorBackend,
    "opencode": OpenCodeBackend,
}


def _last_json_object(stdout: str) -> Any:
    """The whole stdout as JSON, else the last line that parses as a JSON object."""
    try:
        return json.loads(stdout)
    except (ValueError, TypeError):
        pass
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except ValueError:
                continue
    return None


def get_backend(cfg=None, name: str | None = None) -> Backend:
    """Backend from an explicit name, else the config (agent.backend / $MIGITE_AGENT), else claude."""
    if cfg is None and name is None:
        cfg = migite_config.load(os.getcwd())
    chosen = name or (cfg.get("agent.backend") if cfg else None) or "claude"
    if chosen not in BACKENDS:
        raise ValueError(f"unknown agent backend {chosen!r}; known: {', '.join(BACKENDS)}")
    binary = cfg.get("agent.command") if cfg else None
    return BACKENDS[chosen](binary=binary or None)


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description="migite_agent: agent-CLI backends")
    ap.add_argument("--repo-root", default=None)
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("binary", help="print the backend executable name")
    sub.add_parser("capabilities", help="print the backend's capability table as JSON")
    p_i = sub.add_parser("interactive", help="print one shell-quoted command line that opens an interactive session")
    p_i.add_argument("--permission-mode", default="none")
    p_i.add_argument("--prompt-file", required=True, help="file whose contents become the initial prompt")
    p_i.add_argument("--model", default="")
    args = ap.parse_args()

    try:
        cfg = migite_config.load(args.repo_root or os.getcwd())
        backend = get_backend(cfg)
    except (migite_config.ConfigError, ValueError) as e:
        print(f"✘ {e}", file=sys.stderr)
        sys.exit(1)

    if args.command == "binary":
        print(backend.binary)
    elif args.command == "capabilities":
        print(json.dumps(backend.capabilities(), indent=2))
    elif args.command == "interactive":
        prompt = Path(args.prompt_file).read_text()
        argv = backend.interactive_argv(prompt=prompt, permission_mode=args.permission_mode, model=args.model or None)
        prefix = "env -u CLAUDECODE " if backend.name == "claude" else ""
        print(prefix + " ".join(shlex.quote(a) for a in argv))


if __name__ == "__main__":
    main()
