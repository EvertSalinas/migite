"""agents.claude - Claude Code (`claude`).

Headless: `claude --print --output-format json`, prompt on stdin, one JSON
envelope back with the result, token usage, cost, and, with --json-schema, a
schema-validated `structured_output`. Verified against Claude Code CLI 2.1.x.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from .base import Agent, AgentInfo, AskRequest, AskResult, Launch, SessionRequest, plain_text_result

# The only place a Claude model id is written down.
MODELS = {"fast": "claude-haiku-4-5-20251001", "standard": "claude-sonnet-5", "strong": "claude-opus-5-5"}

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
        argv += ["--", req.prompt]
        return Launch(argv=argv, env_unset=self.info.env_unset)
