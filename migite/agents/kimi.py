"""agents.kimi - Kimi Code CLI (`kimi`).

Headless: `kimi -m <model> -p <prompt> --output-format stream-json`, one JSON
object per line on stdout. An assistant reply is
`{"role":"assistant","content":"..."}`; a tool call adds a `tool_calls` array to
that message and is followed by a `{"role":"tool",...}` result; `{"role":"meta",
...}` lines carry the version and a resume hint. The final answer is the last
assistant message without tool calls (earlier ones are narration); thinking and
tool progress go to stderr, not the JSONL. Verified against the Kimi Code source
(apps/kimi-code/src/cli/prompt-render.ts); not yet run against a live login.

`-p` is non-interactive: it rejects `--yolo`/`--auto`/`--plan` and always runs
with Kimi's own auto approval policy, so migite's permission words cannot be
honoured there (permission_flags=False). The interactive TUI accepts no initial
prompt, so a phase runs headless via `-p` rather than as a seeded session
(session_mode="headless"). Kimi reports no token or cost data in `-p`, so the
ledger records zeros. Model ids are Kimi aliases (`kimi-code/...` on the managed
service); none is pinned, so `kimi` uses the model from its own config.toml until
tiers are set in migite (`kimi --help`, `kimi provider list`).
"""

from __future__ import annotations

import json

from .base import Agent, AgentInfo, AskRequest, AskResult, Launch, SessionRequest, plain_text_result


class KimiAgent(Agent):
    info = AgentInfo(
        name="kimi",
        display_name="Kimi Code",
        default_binary="kimi",
        models={"fast": None, "standard": None, "strong": None},
        prompt_via="arg",
        permission_flags=False,
        session_mode="headless",
        exit_hint="/exit",
        instruction_files="AGENTS.md",
    )

    def _base(self, req: AskRequest | SessionRequest) -> list[str]:
        argv = [self.binary]
        if req.model:
            argv += ["--model", req.model]
        return argv

    def ask_launch(self, req: AskRequest) -> Launch:
        # `-p` cannot be combined with --yolo/--auto/--plan; it runs auto regardless.
        return Launch(argv=self._base(req) + ["-p", req.prompt, "--output-format", "stream-json"])

    def parse(self, stdout: str, returncode: int, req: AskRequest) -> AskResult:
        answers: list[str] = []
        narration: list[str] = []
        saw_envelope = False
        for line in stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if not isinstance(ev, dict) or "role" not in ev:
                continue
            saw_envelope = True
            if ev.get("role") != "assistant":
                continue
            content = ev.get("content")
            if not isinstance(content, str) or not content:
                continue
            (narration if ev.get("tool_calls") else answers).append(content)
        if not saw_envelope:
            return plain_text_result(stdout, returncode, req.model or "")
        # The final answer is the last assistant turn without tool calls; if the
        # CLI only emitted tool-calling turns, fall back to whatever it said.
        return AskResult(text="\n".join(answers or narration).strip(), ok=returncode == 0,
                         model=req.model or "", raw={"events": True})

    def session_launch(self, req: SessionRequest) -> Launch:
        # No seeded interactive session exists: run the phase headless with `-p`.
        return Launch(argv=self._base(req) + ["-p", req.prompt])
