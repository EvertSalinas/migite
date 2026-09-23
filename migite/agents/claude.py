"""agents.claude - Claude Code (`claude`).

Headless: `claude --print --output-format json`, prompt on stdin, one JSON
envelope back with the result, token usage, cost, and, with --json-schema, a
schema-validated `structured_output`. Verified against Claude Code CLI 2.1.x.
"""

from __future__ import annotations

import json

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
    )

    def _permission(self, word: str) -> list[str]:
        flag = PERMISSION_FLAGS.get(word)
        return ["--permission-mode", flag] if flag else []

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
        return Launch(argv=argv, stdin=req.prompt, env_unset=self.info.env_unset)

    def parse(self, stdout: str, returncode: int, req: AskRequest) -> AskResult:
        try:
            d = json.loads(stdout)
        except (ValueError, TypeError):
            d = None
        if not isinstance(d, dict) or "result" not in d:
            return plain_text_result(stdout, returncode, req.model or "")
        usage = d.get("usage") or {}
        model_usage = d.get("modelUsage") or {}
        # Prefer what was asked for; an alias or an unset model resolves to the id the CLI reports.
        model = req.model or (next(iter(model_usage)) if len(model_usage) == 1 else "")
        ok = returncode == 0 and not d.get("is_error", False)
        return AskResult(
            text=str(d.get("result") or "").strip(), structured=d.get("structured_output"), ok=ok,
            error="" if ok else str(d.get("result") or "")[:400], model=model,
            duration_ms=int(d.get("duration_ms") or 0),
            input_tokens=int(usage.get("input_tokens") or 0),
            output_tokens=int(usage.get("output_tokens") or 0),
            cache_read_input_tokens=int(usage.get("cache_read_input_tokens") or 0),
            cache_creation_input_tokens=int(usage.get("cache_creation_input_tokens") or 0),
            cost_usd=float(d.get("total_cost_usd") or 0.0), raw=d,
        )

    def session_launch(self, req: SessionRequest) -> Launch:
        argv = [self.binary] + self._permission(req.permission)
        if req.model:
            argv += ["--model", req.model]
        argv += ["--", req.prompt]
        return Launch(argv=argv, env_unset=self.info.env_unset)
