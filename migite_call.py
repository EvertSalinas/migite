#!/usr/bin/env python3
"""migite_call - the gateway between migite and whichever agent is configured.

Everything that talks to an agent CLI goes through here. Callers say what they
want in migite's terms: a role (never a model id), a label for the ledger, an
optional JSON schema, a neutral permission word, named tool scopes. The gateway
does the same work for every agent:

  * resolves the role to a model id and an effort level from the config;
  * degrades what the agent lacks: no structured output drops the schema (the
    caller parses text), no effort drops the level, an unmapped scope refuses the
    call with ScopeUnsupported rather than run it with every tool;
  * hands a prompt that is too long for one command-line argument to the agent as
    a pointer to a file, for headless calls and interactive sessions alike;
  * starts the process, applies the timeout, and turns every failure, including a
    CLI that is not installed, into AgentError;
  * appends one usage line per headless call to $MIGITE_USAGE_LEDGER.

What a CLI's flags and output look like is the adapter's business, in
agents/<name>.py. This module never names a flag or a model.

`migite_claude.py` is a compatibility shim for this module's old name.

CLI (used by bash; headless calls and sessions go through migite_agent.py):
  migite_call.py summary --ledger FILE [--json OUT]   print a usage table, optionally write JSON
  migite_call.py field FILE KEY[.SUBKEY...]           print one value from a JSON file
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import agents
import migite_config

LEDGER_ENV = "MIGITE_USAGE_LEDGER"
SCHEMA_VERSION = 1

# Set by configure_from(cfg). Until then: the default agent, built-in timeouts,
# no permission flag, built-in model defaults, no effort.
AGENT: agents.Agent = agents.get()
CONFIG: migite_config.Config | None = None
DEFAULT_TIMEOUT = 600
THINKING_TIMEOUT = 900
DEFAULT_PERMISSION = "none"
INLINE_MAX = 100_000                     # ui.prompt_inline_max
ROLE_EFFORT: dict[str, str | None] = {}  # role -> effort level


class AgentError(RuntimeError):
    """The agent CLI failed, timed out, reported an error, or could not be started."""


class ScopeUnsupported(AgentError):
    """The call asked for a tool scope the configured agent cannot restrict itself to."""


# ── configuration ─────────────────────────────────────────────────────────────

def configure_from(cfg: migite_config.Config) -> None:
    """Point every later call at the agent, models, efforts, timeouts, and permission
    the loaded config selects. Each tool calls this once after migite_config.load()."""
    global AGENT, CONFIG, DEFAULT_TIMEOUT, THINKING_TIMEOUT, DEFAULT_PERMISSION, INLINE_MAX, ROLE_EFFORT
    AGENT = agents.from_config(cfg)
    CONFIG = cfg
    DEFAULT_TIMEOUT = int(cfg.get("models.timeout_seconds") or DEFAULT_TIMEOUT)
    THINKING_TIMEOUT = int(cfg.get("models.thinking_timeout_seconds") or THINKING_TIMEOUT)
    DEFAULT_PERMISSION = agents.normalize_permission(
        os.environ.get("MIGITE_PERMISSION_MODE") or cfg.get("permissions.headless") or "none")
    INLINE_MAX = int(cfg.get("ui.prompt_inline_max") or INLINE_MAX)
    ROLE_EFFORT = dict(cfg.efforts_by_role())


def reset() -> None:
    """Back to the built-in state: the default agent, no config, built-in timeouts,
    no permission flag, no effort. Tests call this between agents."""
    global AGENT, CONFIG, DEFAULT_TIMEOUT, THINKING_TIMEOUT, DEFAULT_PERMISSION, INLINE_MAX, ROLE_EFFORT
    AGENT, CONFIG = agents.get(), None
    DEFAULT_TIMEOUT, THINKING_TIMEOUT, DEFAULT_PERMISSION, INLINE_MAX = 600, 900, "none", 100_000
    ROLE_EFFORT = {}


def model_for(role: str) -> str:
    """The model id a role runs on for the active agent; "" means the CLI's own default."""
    if CONFIG is not None:
        return CONFIG.model(role)
    return migite_config.default_model(role, AGENT.name)


def supports(capability: str) -> bool:
    """structured_output | effort | usage | scope:<name>, for the active agent."""
    return AGENT.supports(capability)


def _missing_cli_message(agent: agents.Agent, detail: str = "") -> str:
    return (f"the {agent.name} backend's CLI '{agent.binary}' could not be started"
            + (f" ({detail})" if detail else "")
            + ". Install it, put it on PATH, or point agent.command at it in .migite.yml.")


def require_cli() -> None:
    """Raise AgentError unless the active agent's executable can be found.
    Each tool calls this once after configure_from, so a missing CLI stops the
    run before any graph node starts instead of failing every node in turn."""
    if shutil.which(AGENT.binary) is None:
        raise AgentError(_missing_cli_message(AGENT, "not found on PATH"))


def check_agent() -> dict:
    """What `migite doctor` reports: the agent, where its binary is, and its version."""
    found = shutil.which(AGENT.binary)
    version = ""
    if found:
        try:
            proc = subprocess.run(AGENT.version_argv(), capture_output=True, text=True, timeout=10,
                                  stdin=subprocess.DEVNULL, env=agents.Launch([], env_unset=AGENT.info.env_unset).environ())
            version = (proc.stdout or proc.stderr).strip().splitlines()[0] if proc.returncode == 0 else ""
        except (OSError, subprocess.TimeoutExpired, IndexError):
            version = ""
    return {"name": AGENT.name, "display_name": AGENT.info.display_name, "binary": AGENT.binary,
            "path": found or "", "version": version}


# ── prompts too long for one argument ─────────────────────────────────────────

def _pointer_text(path: str, size: int) -> str:
    return (f"Your task brief is in the file {path} ({size} bytes, too large to pass inline). "
            f"Read that file IN FULL before doing anything else, then follow its instructions "
            f"exactly as if they had been given to you directly.")


def _limit() -> int:
    return min(INLINE_MAX, AGENT.info.max_arg_bytes)


def fit_prompt(prompt: str, *, prompt_file: str | None = None) -> tuple[str, str | None]:
    """(prompt to send, temp file to delete afterwards). A prompt over the argument
    limit becomes a pointer: to `prompt_file` when the caller already wrote one,
    else to a new temp file the caller must delete."""
    size = len(prompt.encode("utf-8"))
    if size <= _limit():
        return prompt, None
    if prompt_file:
        print(f"      ⚠ prompt is {size} bytes (> {_limit()}); passing it as a file pointer", file=sys.stderr, flush=True)
        return _pointer_text(prompt_file, size), None
    fd, tmp = tempfile.mkstemp(prefix="migite-prompt-", suffix=".md")
    with os.fdopen(fd, "w") as fh:
        fh.write(prompt)
    return _pointer_text(tmp, size), tmp


# ── usage records ─────────────────────────────────────────────────────────────

@dataclass
class UsageRecord:
    ts: str
    tool: str
    label: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cost_usd: float = 0.0
    duration_ms: int = 0
    ok: bool = True


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _failed_record(*, tool: str, label: str, model: str, elapsed_ms: int) -> UsageRecord:
    return UsageRecord(ts=_now(), tool=tool, label=label, model=model, duration_ms=elapsed_ms, ok=False)


@dataclass
class CallResult:
    text: str
    structured: Any          # parsed structured output when a schema was honoured, else None
    usage: UsageRecord
    raw: dict                # the agent's full parsed output, for anything not modelled above


def record(rec: UsageRecord, ledger: str | None = None) -> None:
    """Append one JSONL line to the ledger (arg, else $MIGITE_USAGE_LEDGER). No-op without one."""
    path = ledger or os.environ.get(LEDGER_ENV)
    if not path:
        return
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(asdict(rec)) + "\n")
    except OSError as e:  # a ledger failure must never fail the model call
        print(f"      ⚠ usage ledger write failed: {e}", file=sys.stderr, flush=True)


# ── the call ──────────────────────────────────────────────────────────────────

def call_agent(prompt: str, role: str, *, label: str = "", tool: str = "", schema: dict | None = None,
               scopes: tuple[str, ...] | list[str] = (), permission: str | None = None,
               thinking: bool = False, timeout: int | None = None, ledger: str | None = None,
               effort: str | None = None, model: str | None = None) -> CallResult:
    """One headless call on the configured agent.

    `role` picks the model and effort (models.* in the config); `model` and `effort`
    override them for this call only. `permission` is a neutral word (auto, edits,
    plan, ask, none) or a Claude Code alias; unset means permissions.headless.
    Raises ScopeUnsupported before starting anything when a scope can't be honoured,
    and AgentError on a missing CLI, non-zero exit, timeout, or a reported error."""
    agent = AGENT
    scopes = tuple(scopes)
    missing = [s for s in scopes if not agent.supports(f"scope:{s}")]
    if missing:
        raise ScopeUnsupported(f"the {agent.name} backend cannot restrict a call to the "
                               f"{', '.join(missing)} scope; refusing to run it with every tool")
    resolved_model = model if model is not None else (model_for(role) if role else "")
    resolved_effort = effort if effort is not None else ROLE_EFFORT.get(role)
    req = agents.AskRequest(
        prompt=prompt,
        model=resolved_model or None,
        effort=resolved_effort if agent.info.effort else None,
        schema=schema if agent.info.structured_output else None,   # caller falls back to text
        permission=agents.normalize_permission(permission if permission is not None else DEFAULT_PERMISSION),
        scopes=scopes,
    )
    timeout = timeout or (THINKING_TIMEOUT if thinking else DEFAULT_TIMEOUT)

    pointer_file = None
    if agent.info.prompt_via == "arg":
        req.prompt, pointer_file = fit_prompt(prompt)

    launch = agent.ask_launch(req)
    shown = [c if len(c) < 60 else c[:57] + "..." for c in launch.argv]
    print(f"      {agent.name} cmd: {' '.join(shown)}", file=sys.stderr, flush=True)
    t0 = time.monotonic()
    try:
        proc = subprocess.run(launch.argv, input=launch.stdin, capture_output=True, text=True,
                              env=launch.environ(), timeout=timeout)
    except subprocess.TimeoutExpired:
        elapsed = int((time.monotonic() - t0) * 1000)
        record(_failed_record(tool=tool, label=label, model=resolved_model, elapsed_ms=elapsed), ledger)
        raise AgentError(f"{agent.name} timed out after {elapsed // 1000}s (limit={timeout}s, model={resolved_model or 'default'})")
    except OSError as e:
        # The binary is missing or not executable. Record the attempt, then raise the
        # error type every caller already handles instead of a raw FileNotFoundError.
        elapsed = int((time.monotonic() - t0) * 1000)
        record(_failed_record(tool=tool, label=label, model=resolved_model, elapsed_ms=elapsed), ledger)
        raise AgentError(_missing_cli_message(agent, e.strerror or str(e))) from e
    finally:
        if pointer_file:
            try:
                os.unlink(pointer_file)
            except OSError:
                pass
    elapsed = int((time.monotonic() - t0) * 1000)

    parsed = agent.parse(proc.stdout, proc.returncode, req)
    usage = UsageRecord(
        ts=_now(), tool=tool, label=label, model=parsed.model or resolved_model,
        input_tokens=parsed.input_tokens, output_tokens=parsed.output_tokens,
        cache_read_input_tokens=parsed.cache_read_input_tokens,
        cache_creation_input_tokens=parsed.cache_creation_input_tokens,
        cost_usd=parsed.cost_usd, duration_ms=parsed.duration_ms or elapsed, ok=parsed.ok,
    )
    record(usage, ledger)
    print(f"      ✔ {agent.name} returned in {elapsed / 1000:.1f}s (exit {proc.returncode}"
          + (f", ${usage.cost_usd:.3f}" if usage.cost_usd else "") + ")", file=sys.stderr, flush=True)

    if not parsed.ok:
        detail = parsed.error or proc.stderr
        raise AgentError(f"{agent.name} failed (exit {proc.returncode}, {elapsed / 1000:.1f}s): {str(detail)[:400]}")

    structured = parsed.structured if req.schema is not None else None
    if req.schema is not None and structured is None and parsed.text:
        # Some CLI versions only echo the JSON in the text; parse it ourselves.
        try:
            structured = json.loads(parsed.text)
        except ValueError:
            structured = None
    return CallResult(text=parsed.text, structured=structured, usage=usage, raw=parsed.raw)


def session_launch(prompt: str, *, permission: str | None = None, prompt_file: str | None = None) -> agents.Launch:
    """The command that opens an interactive session on the active agent with `prompt`
    as its first message. Sessions always take the prompt as an argument, so a long
    one becomes a pointer to `prompt_file`. Unset permission = permissions.interactive."""
    if permission is None:
        permission = (CONFIG.get("permissions.interactive") if CONFIG is not None else None) or "auto"
    text, tmp = fit_prompt(prompt, prompt_file=prompt_file)
    if tmp:
        # No caller-owned file to point at: keep the temp file, the session reads it later.
        print(f"      ⚠ prompt kept at {tmp} for the session to read", file=sys.stderr, flush=True)
    return AGENT.session_launch(agents.SessionRequest(prompt=text, permission=agents.normalize_permission(permission)))


# ── Envelope helpers shared by the agents ─────────────────────────────────────

SEVERITY_MARKS = (("critical", "🔴"), ("warning", "🟡"), ("note", "🟢"))


def severity_counts(text: str) -> dict[str, int]:
    """Count 🔴/🟡/🟢 marks — the one convention every migite prompt uses for findings."""
    return {name: text.count(mark) for name, mark in SEVERITY_MARKS}


def write_json(path: str | Path, obj: dict) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n")


def envelope_base(tool: str) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "tool": tool,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


# ── Ledger summary ────────────────────────────────────────────────────────────

def read_ledger(path: str | Path) -> list[dict]:
    p = Path(path)
    if not p.is_file():
        return []
    out: list[dict] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


_TOKEN_KEYS = ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")


def summarize(records: list[dict]) -> dict:
    total: dict[str, Any] = {"calls": 0, "failed": 0, "cost_usd": 0.0, "duration_ms": 0}
    for k in _TOKEN_KEYS:
        total[k] = 0
    by_model: dict[str, dict] = {}
    by_tool: dict[str, dict] = {}
    for r in records:
        total["calls"] += 1
        if not r.get("ok", True):
            total["failed"] += 1
        total["cost_usd"] += float(r.get("cost_usd") or 0)
        total["duration_ms"] += int(r.get("duration_ms") or 0)
        for k in _TOKEN_KEYS:
            total[k] += int(r.get(k) or 0)
        for bucket, key in ((by_model, r.get("model") or "(unknown)"), (by_tool, r.get("tool") or "(unknown)")):
            b = bucket.setdefault(key, {"calls": 0, "cost_usd": 0.0, "input_tokens": 0, "output_tokens": 0, "duration_ms": 0})
            b["calls"] += 1
            b["cost_usd"] += float(r.get("cost_usd") or 0)
            b["input_tokens"] += int(r.get("input_tokens") or 0) + int(r.get("cache_read_input_tokens") or 0) + int(r.get("cache_creation_input_tokens") or 0)
            b["output_tokens"] += int(r.get("output_tokens") or 0)
            b["duration_ms"] += int(r.get("duration_ms") or 0)
    total["cost_usd"] = round(total["cost_usd"], 4)
    return {"total": total, "by_model": by_model, "by_tool": by_tool,
            "note": "Headless agent calls only; interactive sessions (implement, fix, PR description) are not metered."}


def format_summary(summary: dict) -> str:
    t = summary["total"]
    if t["calls"] == 0:
        return "  No headless model calls recorded."
    lines = ["  Model calls (headless only — interactive sessions not metered)", ""]
    lines.append(f"  {'model':<34} {'calls':>5} {'in+cache tok':>13} {'out tok':>8} {'time':>7} {'cost':>8}")
    for model, b in sorted(summary["by_model"].items(), key=lambda kv: -kv[1]["cost_usd"]):
        lines.append(f"  {model:<34} {b['calls']:>5} {b['input_tokens']:>13,} {b['output_tokens']:>8,} "
                     f"{b['duration_ms'] / 1000:>6.0f}s {'$' + format(b['cost_usd'], '.2f'):>8}")
    lines.append(f"  {'total':<34} {t['calls']:>5} "
                 f"{t['input_tokens'] + t['cache_read_input_tokens'] + t['cache_creation_input_tokens']:>13,} "
                 f"{t['output_tokens']:>8,} {t['duration_ms'] / 1000:>6.0f}s {'$' + format(t['cost_usd'], '.2f'):>8}")
    if t["cache_creation_input_tokens"]:
        lines.append(f"  cache-creation tokens: {t['cache_creation_input_tokens']:,} "
                     f"(each headless call re-sends the agent CLI's system context)")
    if t["failed"]:
        lines.append(f"  ⚠ {t['failed']} call(s) failed or timed out")
    return "\n".join(lines)


def get_field(obj: Any, dotted: str) -> Any:
    cur = obj
    for part in dotted.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list) and part.isdigit():
            cur = cur[int(part)] if int(part) < len(cur) else None
        else:
            return None
        if cur is None:
            return None
    return cur


# ── CLI ───────────────────────────────────────────────────────────────────────

def _cmd_summary(args: argparse.Namespace) -> int:
    records = read_ledger(args.ledger)
    summary = summarize(records)
    summary["ledger"] = str(args.ledger)
    if args.json:
        write_json(args.json, summary)
    print(format_summary(summary))
    return 0


def _cmd_field(args: argparse.Namespace) -> int:
    p = Path(args.file)
    if not p.is_file():
        return 1
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
    except ValueError:
        return 1
    val = get_field(obj, args.key)
    if val is None:
        return 1
    if isinstance(val, (dict, list)):
        print(json.dumps(val))
    elif isinstance(val, bool):
        print("true" if val else "false")
    else:
        print(val)
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description="migite_call: usage ledger and JSON envelope helpers for bash")
    sub = ap.add_subparsers(dest="command", required=True)

    p_sum = sub.add_parser("summary", help="summarise a usage ledger")
    p_sum.add_argument("--ledger", required=True)
    p_sum.add_argument("--json", default=None, help="also write the summary as JSON here")

    p_field = sub.add_parser("field", help="print one (dotted) key from a JSON file; exit 1 if absent")
    p_field.add_argument("file")
    p_field.add_argument("key")

    args = ap.parse_args()
    if args.command == "summary":
        sys.exit(_cmd_summary(args))
    if args.command == "field":
        sys.exit(_cmd_field(args))


if __name__ == "__main__":
    main()
