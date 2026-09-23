"""agents.opencode - OpenCode (`opencode`).

Headless: `opencode run --format json <message>`, a stream of JSON events, one
per line. The answer is the `text` events joined; tokens and cost are summed
from `step_finish` events; an `error` event fails the call. `--auto` approves
tool use. Models are `provider/model` ids and are not pinned by default
(`opencode models`). Built from the OpenCode docs; not yet verified live.
"""

from __future__ import annotations

import json

from .base import Agent, AgentInfo, AskRequest, AskResult, Launch, SessionRequest, plain_text_result


class OpenCodeAgent(Agent):
    info = AgentInfo(
        name="opencode",
        display_name="OpenCode",
        default_binary="opencode",
        models={"fast": None, "standard": None, "strong": None},
        usage=True,
        prompt_via="arg",
        exit_hint="/exit",
        instruction_files="AGENTS.md",
    )

    def _permission(self, word: str) -> list[str]:
        return ["--auto"] if word in ("auto", "edits") else []

    def ask_launch(self, req: AskRequest) -> Launch:
        argv = [self.binary, "run", "--format", "json"] + self._permission(req.permission)
        if req.model:
            argv += ["--model", req.model]
        argv.append(req.prompt)
        return Launch(argv=argv)

    def parse(self, stdout: str, returncode: int, req: AskRequest) -> AskResult:
        texts: list[str] = []
        r = AskResult(model=req.model or "")
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
            kind = ev.get("type")
            if kind == "text":
                texts.append(str(part.get("text") or ""))
            elif kind == "step_finish":
                tokens = part.get("tokens") or {}
                cache = tokens.get("cache") or {}
                r.cost_usd += float(part.get("cost") or 0.0)
                r.input_tokens += int(tokens.get("input") or 0)
                r.output_tokens += int(tokens.get("output") or 0) + int(tokens.get("reasoning") or 0)
                r.cache_read_input_tokens += int(cache.get("read") or 0)
                r.cache_creation_input_tokens += int(cache.get("write") or 0)
            elif kind == "error":
                err = ev.get("error") or {}
                r.ok = False
                r.error = str((err.get("data") or {}).get("message") or err.get("name") or "opencode error")[:400]
        if not saw_event:
            return plain_text_result(stdout, returncode, req.model or "")
        r.text = "\n".join(t for t in texts if t).strip()
        if returncode != 0:
            r.ok = False
            r.error = r.error or f"opencode exited {returncode}"
        r.raw = {"events": True}
        return r

    def session_launch(self, req: SessionRequest) -> Launch:
        argv = [self.binary, "--prompt", req.prompt] + self._permission(req.permission)
        if req.model:
            argv += ["--model", req.model]
        return Launch(argv=argv)
