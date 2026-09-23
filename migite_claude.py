#!/usr/bin/env python3
"""migite_claude — the one place migite shells out to `claude --print`.

Every headless model call in the pipeline goes through here (the Python agents
import it; bash goes through `claude_print` in helpers.sh, which pipes the CLI's
JSON through the `extract` subcommand). Doing it in one place buys three things
the six copy-pasted `call_claude` functions never had:

  * machine-readable results — `claude --print --output-format json` returns an
    envelope with `result`, `usage`, `total_cost_usd`, `duration_ms`, and, with
    `--json-schema`, a schema-validated `structured_output`;
  * a usage ledger — one JSONL line per call, appended to $MIGITE_USAGE_LEDGER
    when set, so a run can print what it actually cost;
  * one behaviour for CLAUDECODE stripping, timeouts, and error handling.

Envelope shape (verified against the CLI, 2026-09-22): see `_usage_from`.

CLI (used by bash):
  migite_claude.py extract --tool T --label L [--exit-code N]   stdin: CLI JSON → stdout: result text
  migite_claude.py summary --ledger FILE [--json OUT]           print a usage table, optionally write JSON
  migite_claude.py field FILE KEY[.SUBKEY...]                   print one value from a JSON file
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import migite_agent

LEDGER_ENV = "MIGITE_USAGE_LEDGER"
DEFAULT_TIMEOUT = 600
THINKING_TIMEOUT = 900
DEFAULT_PERMISSION_MODE: str | None = None   # None/"none" = don't pass --permission-mode
# The agent backend every call goes through. configure_from(cfg) sets it from
# agent.backend; until then the default backend (claude) is used.
BACKEND: migite_agent.Backend = migite_agent.ClaudeBackend()
# Prompts longer than this are handed to arg-only backends (Cursor, OpenCode) as a
# pointer to a temp file instead of inline — Linux caps one argv string at 128 KB.
ARG_PROMPT_MAX = 100_000
# role -> --effort level, filled by configure_from(cfg). call_claude(role=...) looks it up.
ROLE_EFFORT: dict[str, str | None] = {}
SCHEMA_VERSION = 1


def configure(*, timeout: int | None = None, thinking_timeout: int | None = None,
              permission_mode: str | None = None) -> None:
    """Apply config values (models.timeout_seconds, models.thinking_timeout_seconds,
    permissions.headless) for every subsequent call_claude in this process. Each
    tool calls this once from main() after migite_config.load()."""
    global DEFAULT_TIMEOUT, THINKING_TIMEOUT, DEFAULT_PERMISSION_MODE
    if timeout:
        DEFAULT_TIMEOUT = int(timeout)
    if thinking_timeout:
        THINKING_TIMEOUT = int(thinking_timeout)
    if permission_mode is not None:
        DEFAULT_PERMISSION_MODE = None if permission_mode == "none" else permission_mode


def supports(capability: str) -> bool:
    """Does the active backend have `structured_output`, `effort`, `tool_allowlist`, or `usage`?"""
    return bool(getattr(BACKEND, capability, False))


def configure_from(cfg) -> None:
    """configure() straight from a migite_config.Config, plus the per-role effort table
    and the agent backend (agent.backend / agent.command)."""
    global ROLE_EFFORT, BACKEND
    BACKEND = migite_agent.get_backend(cfg)
    configure(
        timeout=cfg.get("models.timeout_seconds"),
        thinking_timeout=cfg.get("models.thinking_timeout_seconds"),
        permission_mode=os.environ.get("MIGITE_PERMISSION_MODE") or cfg.get("permissions.headless") or "none",
    )
    ROLE_EFFORT = dict(cfg.efforts_by_role())


def effort_flag(model: str, effort: str | None) -> list[str]:
    """`--effort <level>` for this call, or [] when unset/"none" — and always [] for a
    Haiku model, which rejects the flag."""
    if not effort or effort == "none" or "haiku" in model:
        return []
    return ["--effort", effort]


class ClaudeError(RuntimeError):
    """`claude --print` failed, timed out, or reported is_error."""


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


@dataclass
class CallResult:
    text: str
    structured: Any          # parsed `structured_output` when a schema was given, else None
    usage: UsageRecord
    raw: dict                # the full envelope, for anything not modelled above


# ── Envelope parsing ──────────────────────────────────────────────────────────

def parse_envelope(stdout: str) -> dict | None:
    """The CLI's `--output-format json` envelope, or None if stdout isn't one
    (older CLI, plain-text error, empty output)."""
    try:
        data = json.loads(stdout)
    except (ValueError, TypeError):
        return None
    if isinstance(data, dict) and "result" in data:
        return data
    return None


def _usage_from(envelope: dict | None, *, tool: str, label: str, model: str,
                elapsed_ms: int, ok: bool) -> UsageRecord:
    rec = UsageRecord(
        ts=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        tool=tool, label=label, model=model, duration_ms=elapsed_ms, ok=ok,
    )
    if not envelope:
        return rec
    usage = envelope.get("usage") or {}
    rec.input_tokens = int(usage.get("input_tokens") or 0)
    rec.output_tokens = int(usage.get("output_tokens") or 0)
    rec.cache_read_input_tokens = int(usage.get("cache_read_input_tokens") or 0)
    rec.cache_creation_input_tokens = int(usage.get("cache_creation_input_tokens") or 0)
    rec.cost_usd = float(envelope.get("total_cost_usd") or 0.0)
    rec.duration_ms = int(envelope.get("duration_ms") or elapsed_ms)
    # The CLI reports the model it actually used under modelUsage; prefer that
    # over what we asked for (an alias such as "opus" resolves to a concrete id).
    model_usage = envelope.get("modelUsage") or {}
    if not model and len(model_usage) == 1:
        rec.model = next(iter(model_usage))
    return rec


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


# ── The call ──────────────────────────────────────────────────────────────────

def call_claude(prompt: str, model: str, *, thinking: bool = False, timeout: int | None = None,
                label: str = "", tool: str = "", schema: dict | None = None,
                permission_mode: str | None = None, allowed_tools: list[str] | None = None,
                ledger: str | None = None, role: str = "", effort: str | None = None) -> CallResult:
    """Run `claude --print` headlessly and return text + structured output + usage.

    `effort` (or the configured effort for `role`, see configure_from) becomes
    `--effort <level>`; never sent for Haiku, which rejects it.
    Raises ClaudeError on non-zero exit, timeout, or an envelope with is_error.
    Falls back to treating stdout as plain text if the CLI didn't return the
    JSON envelope, so an older CLI still works (with empty usage)."""
    backend = BACKEND
    effective_effort = effort if effort is not None else ROLE_EFFORT.get(role)
    effective_permission = permission_mode if permission_mode is not None else DEFAULT_PERMISSION_MODE
    if schema is not None and not backend.structured_output:
        schema = None            # caller falls back to text parsing (see supports())
    if allowed_tools and not backend.tool_allowlist:
        allowed_tools = None
    timeout = timeout or (THINKING_TIMEOUT if thinking else DEFAULT_TIMEOUT)

    # Arg-only backends (Cursor, OpenCode) can't take a huge prompt on the command
    # line; hand them a pointer to a temp file instead.
    pointer_file = None
    argv_prompt = prompt
    if backend.prompt_via == "arg" and len(prompt) > ARG_PROMPT_MAX:
        fd, pointer_file = tempfile.mkstemp(prefix="migite-prompt-", suffix=".md")
        with os.fdopen(fd, "w") as fh:
            fh.write(prompt)
        argv_prompt = (f"Your task brief is in the file {pointer_file} ({len(prompt)} bytes). Read it IN FULL "
                       f"before doing anything else, then follow its instructions exactly.")

    cmd, stdin_payload = backend.headless_argv(model=model or None, effort=effective_effort, schema=schema,
                                               permission_mode=effective_permission, allowed_tools=allowed_tools,
                                               prompt=argv_prompt)
    env = backend.env()
    shown = [c if len(c) < 60 else c[:57] + "..." for c in cmd]
    print(f"      {backend.name} cmd: {' '.join(shown)}", file=sys.stderr, flush=True)
    t0 = time.monotonic()
    try:
        proc = subprocess.run(cmd, input=stdin_payload, capture_output=True, text=True, env=env, timeout=timeout)
    except subprocess.TimeoutExpired:
        elapsed = int((time.monotonic() - t0) * 1000)
        record(_usage_from(None, tool=tool, label=label, model=model, elapsed_ms=elapsed, ok=False), ledger)
        raise ClaudeError(f"{backend.name} timed out after {elapsed // 1000}s (limit={timeout}s, model={model})")
    finally:
        if pointer_file:
            try:
                os.unlink(pointer_file)
            except OSError:
                pass
    elapsed = int((time.monotonic() - t0) * 1000)

    parsed = backend.parse(proc.stdout, proc.returncode, requested_model=model)
    usage = UsageRecord(
        ts=datetime.now(timezone.utc).isoformat(timespec="seconds"), tool=tool, label=label,
        model=parsed.model or model, input_tokens=parsed.input_tokens, output_tokens=parsed.output_tokens,
        cache_read_input_tokens=parsed.cache_read_input_tokens,
        cache_creation_input_tokens=parsed.cache_creation_input_tokens,
        cost_usd=parsed.cost_usd, duration_ms=parsed.duration_ms or elapsed, ok=parsed.ok,
    )
    record(usage, ledger)
    print(f"      ✔ {backend.name} returned in {elapsed / 1000:.1f}s (exit {proc.returncode}"
          + (f", ${usage.cost_usd:.3f}" if usage.cost_usd else "") + ")", file=sys.stderr, flush=True)

    if not parsed.ok:
        detail = parsed.error or proc.stderr
        raise ClaudeError(f"{backend.name} failed (exit {proc.returncode}, {elapsed / 1000:.1f}s): {str(detail)[:400]}")

    structured = parsed.structured if schema is not None else None
    if schema is not None and structured is None and parsed.text:
        # Some CLI versions only echo the JSON in the text; parse it ourselves.
        try:
            structured = json.loads(parsed.text)
        except ValueError:
            structured = None
    return CallResult(text=parsed.text, structured=structured, usage=usage, raw=parsed.raw)


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
            "note": "Headless `claude --print` calls only; interactive sessions (implement, fix, PR description) are not metered."}


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
                     f"(each headless call re-sends Claude Code's system context)")
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

def _cmd_extract(args: argparse.Namespace) -> int:
    raw = sys.stdin.read()
    envelope = parse_envelope(raw)
    if envelope is None:
        # Not the JSON envelope (older CLI, or an error printed as text) — pass
        # through untouched, but still record the call (zero usage) so the
        # ledger counts every attempt, same as the Python call_claude path.
        record(_usage_from(None, tool=args.tool, label=args.label, model=args.model or "",
                           elapsed_ms=0, ok=args.exit_code == 0), args.ledger)
        sys.stdout.write(raw)
        return args.exit_code
    ok = args.exit_code == 0 and not envelope.get("is_error", False)
    usage = _usage_from(envelope, tool=args.tool, label=args.label, model=args.model or "",
                        elapsed_ms=int(envelope.get("duration_ms") or 0), ok=ok)
    record(usage, args.ledger)
    result = envelope.get("result")
    if envelope.get("structured_output") is not None and args.structured:
        sys.stdout.write(json.dumps(envelope["structured_output"]))
    else:
        sys.stdout.write(str(result or ""))
    return 0 if ok else (args.exit_code or 1)


def _cmd_run(args: argparse.Namespace) -> int:
    """One headless call for bash: prompt on stdin, result text on stdout, usage to the
    ledger. Picks the backend from the config so bash never builds agent argv itself."""
    import migite_config
    try:
        cfg = migite_config.load(args.repo_root or os.getcwd())
        configure_from(cfg)
    except migite_config.ConfigError as e:
        print(f"✘ config error: {e}", file=sys.stderr)
        return 1
    prompt = sys.stdin.read()
    allowed = args.allowed_tools.split() if args.allowed_tools else None
    if allowed and not supports("tool_allowlist"):
        print(f"✘ the {BACKEND.name} backend has no tool allowlist; refusing a tool-enabled call", file=sys.stderr)
        return 3
    try:
        res = call_claude(prompt, args.model or "", label=args.label, tool=args.tool, effort=args.effort or None,
                          permission_mode=args.permission_mode, timeout=args.timeout or None)
    except ClaudeError as e:
        print(f"✘ {e}", file=sys.stderr)
        return 1
    sys.stdout.write(res.text)
    return 0


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
    ap = argparse.ArgumentParser(description="migite_claude: shared claude --print wrapper + usage ledger")
    sub = ap.add_subparsers(dest="command", required=True)

    p_ex = sub.add_parser("extract", help="stdin: CLI JSON envelope → stdout: result text; records usage")
    p_ex.add_argument("--tool", default="migite")
    p_ex.add_argument("--label", default="")
    p_ex.add_argument("--model", default="")
    p_ex.add_argument("--exit-code", type=int, default=0, help="the claude process's exit code")
    p_ex.add_argument("--ledger", default=None, help=f"override ${LEDGER_ENV}")
    p_ex.add_argument("--structured", action="store_true", help="print structured_output JSON instead of result text")

    p_run = sub.add_parser("run", help="stdin: prompt → stdout: result text; one headless call on the configured backend")
    p_run.add_argument("--tool", default="migite")
    p_run.add_argument("--label", default="")
    p_run.add_argument("--model", default="")
    p_run.add_argument("--effort", default="")
    p_run.add_argument("--permission-mode", default=None)
    p_run.add_argument("--allowedTools", "--allowed-tools", dest="allowed_tools", default="")
    p_run.add_argument("--timeout", type=int, default=0)
    p_run.add_argument("--repo-root", default=None)

    p_sum = sub.add_parser("summary", help="summarise a usage ledger")
    p_sum.add_argument("--ledger", required=True)
    p_sum.add_argument("--json", default=None, help="also write the summary as JSON here")

    p_field = sub.add_parser("field", help="print one (dotted) key from a JSON file; exit 1 if absent")
    p_field.add_argument("file")
    p_field.add_argument("key")

    args = ap.parse_args()
    if args.command == "extract":
        sys.exit(_cmd_extract(args))
    if args.command == "run":
        sys.exit(_cmd_run(args))
    if args.command == "summary":
        sys.exit(_cmd_summary(args))
    if args.command == "field":
        sys.exit(_cmd_field(args))


if __name__ == "__main__":
    main()
