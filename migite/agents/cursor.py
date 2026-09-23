"""agents.cursor - Cursor CLI (`cursor-agent`, alias `agent`).

Headless: `cursor-agent -p --output-format json <prompt>`, one result object
back with no token or cost data. `--trust` skips the workspace-trust prompt and
`--force` lets it apply changes (without it, headless mode only proposes them).
Built from `cursor-agent --help` and cursor.com/docs/cli; not yet verified live.
Models are not pinned: no --model is passed until tiers are set in the config
(`cursor-agent --list-models`).
"""

from __future__ import annotations

import json
from typing import Any

from .base import Agent, AgentInfo, AskRequest, AskResult, Launch, SessionRequest, plain_text_result


class CursorAgent(Agent):
    info = AgentInfo(
        name="cursor",
        display_name="Cursor",
        default_binary="cursor-agent",
        models={"fast": None, "standard": None, "strong": None},
        prompt_via="arg",
        exit_hint="/quit",
        instruction_files="AGENTS.md and .cursor/rules",
    )

    def _permission(self, word: str) -> list[str]:
        if word in ("auto", "edits"):
            return ["--force"]
        if word == "plan":
            return ["--mode", "plan"]
        return []

    def ask_launch(self, req: AskRequest) -> Launch:
        argv = [self.binary, "-p", "--output-format", "json"] + self._permission(req.permission) + ["--trust"]
        if req.model:
            argv += ["--model", req.model]
        argv.append(req.prompt)
        return Launch(argv=argv)

    def parse(self, stdout: str, returncode: int, req: AskRequest) -> AskResult:
        d = _last_json_object(stdout)
        if not isinstance(d, dict) or "result" not in d:
            return plain_text_result(stdout, returncode, req.model or "")
        ok = returncode == 0 and not d.get("is_error", False)
        return AskResult(text=str(d.get("result") or "").strip(), ok=ok,
                         error="" if ok else str(d.get("result") or "")[:400], model=req.model or "",
                         duration_ms=int(d.get("duration_ms") or 0), raw=d)

    def session_launch(self, req: SessionRequest) -> Launch:
        argv = [self.binary] + self._permission(req.permission)
        if req.model:
            argv += ["--model", req.model]
        argv.append(req.prompt)
        return Launch(argv=argv)


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
