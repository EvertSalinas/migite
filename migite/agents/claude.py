"""agents.claude - Claude Code (`claude`).

Headless: `claude --print --output-format json`, prompt on stdin, one JSON
envelope back with the result, token usage, cost, and, with --json-schema, a
schema-validated `structured_output`. Verified against Claude Code CLI 2.1.x.

Interactive sessions report nothing on exit, but the CLI writes every turn's
token usage to its transcript, ~/.claude/projects/<cwd>/<session id>.jsonl.
A session started with --session-id is read back from there, and its tokens
are priced from PRICES.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import datetime
from pathlib import Path

from .base import Agent, AgentInfo, AskRequest, AskResult, Launch, SessionRequest, plain_text_result

# The only place a Claude model id is written down.
MODELS = {"fast": "claude-haiku-4-5-20251001", "standard": "claude-sonnet-5", "strong": "claude-opus-5-5"}

# $ per million tokens: input, output, cache read. Cache writes cost 1.25x input on the
# 5-minute TTL and 2x on the 1-hour one, and fast mode costs 2x. Only interactive
# sessions are priced here; headless calls keep the CLI's own total_cost_usd, which these
# rates reproduce. A dated id (claude-haiku-4-5-20251001) finds its row without the date.
# Server-tool fees (web search) are not counted.
PRICES = {
    "claude-fable-5-1": (10.00, 50.00, 0.25),
    "claude-fable-5": (10.00, 50.00, 1.00),
    "claude-opus-5-5": (4.00, 20.00, 0.20),
    "claude-opus-5": (5.00, 25.00, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00, 0.20),
    "claude-sonnet-5": (2.00, 10.00, 0.20),
    "claude-haiku-4-5": (1.00, 5.00, 0.10),
}
CACHE_WRITE_5M, CACHE_WRITE_1H, FAST_MODE = 1.25, 2.0, 2.0

PERMISSION_FLAGS = {"auto": "bypassPermissions", "edits": "acceptEdits", "plan": "plan", "ask": "default"}


class ClaudeAgent(Agent):
    info = AgentInfo(
        name="claude",
        display_name="Claude Code",
        default_binary="claude",
        models=MODELS,
        structured_output=True,
        effort=True,
        usage=True,
        # claude.ai's Atlassian connector, exposed to Claude Code as MCP tools.
        scopes={"jira.read": ("mcp__claude_ai_Atlassian__getJiraIssue",
                              "mcp__claude_ai_Atlassian__getAccessibleAtlassianResources")},
        prompt_via="stdin",
        # Claude Code sets CLAUDECODE in its own sessions, and a nested `claude`
        # refuses to start while it is set.
        env_unset=("CLAUDECODE",),
        exit_hint="/exit",
        instruction_files="CLAUDE.md",
        isolation=True,
        resume=True,
        plan_mode=True,                # --permission-mode plan
        session_usage=True,            # --session-id, then the session's transcript
    )

    def _permission(self, word: str) -> list[str]:
        flag = PERMISSION_FLAGS.get(word)
        return ["--permission-mode", flag] if flag else []

    def instructions(self, cwd: str | None = None) -> str:
        """The CLAUDE.md files a normal session would load: the user's
        ~/.claude/CLAUDE.md, then every CLAUDE.md from the git root down to the
        working directory. An isolated call passes them itself, because
        --safe-mode (which drops plugins, hooks and skills) drops these too."""
        start = Path(cwd or os.getcwd()).resolve()
        try:
            top = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=start, capture_output=True,
                                 text=True, timeout=10).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            top = ""
        root = Path(top).resolve() if top else start
        dirs = [start] + [p for p in start.parents if p == root or root in p.parents]
        files = [Path.home() / ".claude" / "CLAUDE.md"] + [d / "CLAUDE.md" for d in reversed(dirs)]
        parts, seen = [], set()
        for f in files:
            if f in seen or not f.is_file():
                continue
            seen.add(f)
            parts.append(f"Contents of {f}:\n\n{f.read_text(errors='replace').strip()}")
        return "\n\n".join(parts)

    def ask_launch(self, req: AskRequest) -> Launch:
        argv = [self.binary, "--print", "--output-format", "json"]
        if req.model:
            argv += ["--model", req.model]
        # Haiku rejects --effort.
        if req.effort and req.effort != "none" and "haiku" not in (req.model or ""):
            argv += ["--effort", req.effort]
        if req.schema is not None:
            argv += ["--json-schema", json.dumps(req.schema)]
        argv += self._permission(req.permission)
        tools = self.tools_for(req.scopes)
        if tools:
            argv += ["--allowedTools", " ".join(tools)]
        if req.resume:
            argv += ["--resume", req.resume]
        if req.isolated:
            # Only the named tools (none for a text-only call), no MCP servers, and
            # --safe-mode for no plugins, hooks, skills or CLAUDE.md, which are then
            # passed back explicitly. Nothing saved as a resumable session, unless the
            # call starts or continues a chain. Cuts a call's fixed context from ~24k
            # tokens to the instructions alone, and stops a "no tools" prompt from
            # browsing the repo turn after turn. A resumed session keeps the system
            # prompt it recorded on its first call; the text passed here is the same.
            argv += ["--tools", ",".join(req.tools), "--strict-mcp-config", "--safe-mode"]
            if not (req.resume or req.keep_session):
                argv.append("--no-session-persistence")
            argv.append("--exclude-dynamic-system-prompt-sections")
            instructions = self.instructions()
            if instructions:
                argv += ["--append-system-prompt", instructions]
            if req.max_budget_usd:
                argv += ["--max-budget-usd", f"{req.max_budget_usd:g}"]
        return Launch(argv=argv, stdin=req.prompt, env_unset=self.info.env_unset)

    def parse(self, stdout: str, returncode: int, req: AskRequest) -> AskResult:
        try:
            d = json.loads(stdout)
        except (ValueError, TypeError):
            d = None
        # A stopped call (e.g. error_max_budget_usd) is still an envelope, but it
        # has no "result": recognise it by its type, or its JSON would pass as the answer.
        if not isinstance(d, dict) or ("result" not in d and d.get("type") != "result"):
            return plain_text_result(stdout, returncode, req.model or "")
        usage = d.get("usage") or {}
        # The TTL split of the cache writes; older CLIs don't send it.
        cache_creation = usage.get("cache_creation") or {}
        model_usage = d.get("modelUsage") or {}
        # Prefer what was asked for; an alias or an unset model resolves to the id the CLI reports.
        model = req.model or (next(iter(model_usage)) if len(model_usage) == 1 else "")
        ok = returncode == 0 and not d.get("is_error", False)
        error = "" if ok else str(d.get("result") or "")[:400]
        if not ok and d.get("subtype") == "error_max_budget_usd":
            error = f"stopped at the --max-budget-usd cap (${float(d.get('total_cost_usd') or 0):.2f} spent)"
        return AskResult(
            text=str(d.get("result") or "").strip(), structured=d.get("structured_output"), ok=ok,
            error=error, model=model,
            duration_ms=int(d.get("duration_ms") or 0),
            input_tokens=int(usage.get("input_tokens") or 0),
            output_tokens=int(usage.get("output_tokens") or 0),
            cache_read_input_tokens=int(usage.get("cache_read_input_tokens") or 0),
            cache_creation_input_tokens=int(usage.get("cache_creation_input_tokens") or 0),
            cache_creation_5m_input_tokens=int(cache_creation.get("ephemeral_5m_input_tokens") or 0),
            cache_creation_1h_input_tokens=int(cache_creation.get("ephemeral_1h_input_tokens") or 0),
            cost_usd=float(d.get("total_cost_usd") or 0.0), turns=int(d.get("num_turns") or 0),
            session_id=str(d.get("session_id") or ""), raw=d,
        )

    def session_launch(self, req: SessionRequest) -> Launch:
        argv = [self.binary] + self._permission(req.permission)
        if req.model:
            argv += ["--model", req.model]
        if req.session_id:
            argv += ["--session-id", req.session_id]
        argv += ["--", req.prompt]
        return Launch(argv=argv, env_unset=self.info.env_unset, session_id=req.session_id)

    def session_usage(self, session_id: str, cwd: str, since: float) -> list[AskResult]:
        """Per model, the tokens and cost of the session in the transcript named
        `session_id`, else in the newest transcript for `cwd` written since `since`, with
        the subagents it started. Reads only each line's model, message id, timestamp and
        usage; message content is never kept."""
        paths = _transcripts(session_id, cwd, since)
        if not paths:
            return []
        totals: dict[str, dict] = {}
        seen: set[str] = set()
        for path in paths:
            for model, key, when, usage in _usage_lines(path):
                if when is not None and when < since:
                    continue
                # One API message is written as one line per content block, each
                # carrying the same usage.
                if key and key in seen:
                    continue
                try:
                    counts, dollars = _counts(usage), price(model, usage)
                except (TypeError, ValueError, AttributeError):
                    continue    # a usage block in a shape this doesn't know
                if key:
                    seen.add(key)
                t = totals.setdefault(model, {"input": 0, "output": 0, "read": 0, "write": 0, "write_5m": 0,
                                              "write_1h": 0, "cost": 0.0, "turns": 0, "first": None, "last": None})
                for name, n in counts.items():
                    t[name] += n
                t["cost"] += dollars
                t["turns"] += 1
                if when is not None:
                    t["first"] = when if t["first"] is None else min(t["first"], when)
                    t["last"] = when if t["last"] is None else max(t["last"], when)
        return [AskResult(model=model, input_tokens=t["input"], output_tokens=t["output"],
                          cache_read_input_tokens=t["read"], cache_creation_input_tokens=t["write"],
                          cache_creation_5m_input_tokens=t["write_5m"], cache_creation_1h_input_tokens=t["write_1h"],
                          cost_usd=round(t["cost"], 6), turns=t["turns"], session_id=paths[0].stem,
                          duration_ms=int(((t["last"] or 0) - (t["first"] or 0)) * 1000))
                for model, t in sorted(totals.items())]


def _counts(usage: dict) -> dict[str, int]:
    """The token counts in one turn's `usage` block, keyed as session_usage sums them."""
    cache_creation = usage.get("cache_creation") or {}
    return {"input": int(usage.get("input_tokens") or 0),
            "output": int(usage.get("output_tokens") or 0),
            "read": int(usage.get("cache_read_input_tokens") or 0),
            "write": int(usage.get("cache_creation_input_tokens") or 0),
            "write_5m": int(cache_creation.get("ephemeral_5m_input_tokens") or 0),
            "write_1h": int(cache_creation.get("ephemeral_1h_input_tokens") or 0)}


def price(model: str, usage: dict) -> float:
    """Dollars for one turn's usage on `model`; 0.0 when PRICES has no row for it."""
    rates = PRICES.get(re.sub(r"-\d{8}$", "", model))
    if rates is None:
        return 0.0
    per_input, per_output, per_read = rates
    c = _counts(usage)
    # Older CLIs don't split the writes by TTL: price those at the API's default, 5 minutes.
    unsplit = max(c["write"] - c["write_5m"] - c["write_1h"], 0)
    dollars = (c["input"] * per_input + c["output"] * per_output + c["read"] * per_read
               + (c["write_5m"] + unsplit) * per_input * CACHE_WRITE_5M
               + c["write_1h"] * per_input * CACHE_WRITE_1H) / 1_000_000
    return dollars * (FAST_MODE if usage.get("speed") == "fast" else 1)


def _projects_dir() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "projects"


def _project_key(cwd: str) -> str:
    """The folder Claude Code keeps a working directory's transcripts in."""
    return re.sub(r"[^A-Za-z0-9]", "-", str(Path(cwd).resolve()))


def _transcripts(session_id: str, cwd: str, since: float) -> list[Path]:
    """The session's transcript, then its subagents' transcripts; [] when none is found."""
    root = _projects_dir()
    main = None
    if re.fullmatch(r"[A-Za-z0-9-]+", session_id or ""):
        named = list(root.glob(f"*/{session_id}.jsonl"))
        main = max(named, key=lambda p: p.stat().st_mtime, default=None)
    if main is None:
        folder = root / _project_key(cwd)
        recent = [p for p in folder.glob("*.jsonl") if p.stat().st_mtime >= since] if folder.is_dir() else []
        main = max(recent, key=lambda p: p.stat().st_mtime, default=None)
    if main is None:
        return []
    return [main] + sorted((main.parent / main.stem / "subagents").rglob("*.jsonl"))


def _usage_lines(path: Path):
    """(model, message id, epoch seconds or None, usage) for each transcript line that
    carries usage. Lines without it, unreadable lines and the CLI's own <synthetic>
    messages are skipped."""
    try:
        fh = path.open(encoding="utf-8", errors="replace")
    except OSError:
        return
    with fh:
        for line in fh:
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            msg = entry.get("message") if isinstance(entry, dict) else None
            if not isinstance(msg, dict):
                continue
            usage, model = msg.get("usage"), str(msg.get("model") or "")
            if not isinstance(usage, dict) or not model or model.startswith("<"):
                continue
            key = str(msg.get("id") or entry.get("requestId") or "")
            try:
                when = datetime.fromisoformat(str(entry.get("timestamp"))).timestamp()
            except ValueError:
                when = None
            yield model, key, when, usage
