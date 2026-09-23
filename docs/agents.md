# Agent backends

Migite drives an agent CLI; it is not tied to one. `agent.backend` in the config picks which:

| Agent | `agent.backend` | Executable | Status |
|-------|-----------------|------------|--------|
| Claude Code | `claude` (default) | `claude` | Reference agent. Every capability, verified live. |
| Cursor CLI | `cursor` | `cursor-agent` (alias `agent`) | Built from `cursor-agent --help` and the Cursor docs; no live run yet in this checkout (needs `cursor-agent login`). |
| OpenCode | `opencode` | `opencode` | Built from the documented `run --format json` event shapes; not installed here, so untested live. |

```yaml
# ~/.config/migite/config.yml or <repo>/.migite.yml
agent:
  backend: cursor            # or: MIGITE_AGENT=cursor migite ...
  # command: /opt/cursor/bin/agent   # override the executable
```

## What migite needs from an agent, and what each one has

| Need | Used for | Claude Code | Cursor | OpenCode |
|---|---|---|---|---|
| Headless call, text out | planner, reviewers, knowledge, amendments, heal, standalone tools | `claude --print --output-format json`, prompt on stdin | `cursor-agent -p --output-format json --trust "prompt"` | `opencode run --format json "message"` |
| Interactive session with a first prompt | implement, gate fixes, PR description | `claude -- "prompt"` | `cursor-agent "prompt"` | `opencode --prompt "prompt"` |
| Usage and cost in the ledger | `usage.json`, gate banner | tokens, cache, cost | none (zeros recorded) | cost and tokens summed from `step_finish` events |
| Structured output | the `review.json` verdict | `--json-schema` | no → the reviewer parses markdown | no → the reviewer parses markdown |
| Effort level | `models.effort` | `--effort` (never sent to Haiku) | dropped | dropped |
| Tool scope `jira.read` | the Jira fetch's agent fallback | the two Atlassian read tools via `--allowedTools` | no → only `acli` can fetch | no → only `acli` can fetch |
| Permission words | `permissions.*` | `auto` → `bypassPermissions`, `edits` → `acceptEdits`, `plan`, `ask` → `default` | `auto`/`edits` → `--force`, `plan` → `--mode plan`; `--trust` always in headless | `auto`/`edits` → `--auto` |
| Exit command shown by `run_phase` | interactive sessions | `/exit` | `/quit` | `/exit` |
| Project rules it reads | the implement prompt names them | `CLAUDE.md` | `AGENTS.md` and `.cursor/rules` | `AGENTS.md` |

Everything degrades explicitly, never silently: the reviewer announces it is using text
synthesis, the planner warns when no ticket source can run, and the ledger records a call
with zero cost when the agent reports none. A call that asks for a scope the agent can't honour
is refused before the CLI starts; it is never run with every tool. `migite doctor` names the
agent, checks its executable resolves, and prints its version.

Permission words are migite's own: `auto`, `edits`, `plan`, `ask`, `none`. Claude Code's names
(`bypassPermissions`, `acceptEdits`, `default`) are accepted anywhere as aliases, so older configs
keep working; `migite config` shows the normalized word.

## Models per agent

Model ids are agent-specific, so the three tiers have per-agent defaults, written down only in
that agent's adapter:

| Tier | Claude Code | Cursor | OpenCode |
|---|---|---|---|
| `fast` | `claude-haiku-4-5-20251001` | unset | unset |
| `standard` | `claude-sonnet-5` | unset | unset |
| `strong` | `claude-opus-5-5` | unset | unset |

"Unset" means no model flag is passed and the CLI uses its own configured default. Cursor and
OpenCode ids could not be verified without a login in this checkout, so nothing is guessed.
Pin them once you know them:

```yaml
agent:
  backend: cursor
models:
  fast: sonnet-4            # cursor-agent --list-models
  standard: sonnet-4-thinking
  strong: gpt-5
```

```yaml
agent:
  backend: opencode
models:
  standard: anthropic/claude-sonnet-4-5     # opencode models  → provider/model
  strong: anthropic/claude-opus-4-5
```

Every call site names a role (`think`, `critic`, `review_security`, `heal`, ...), never a model.
Roles and `models.roles` pins work the same on every agent. `models.effort` only reaches agents
that take an effort level.

## Switching for one run

```bash
MIGITE_AGENT=cursor migite --jira BB-1234
MIGITE_AGENT=opencode migite-audit --focus jobs
```

Env beats files, so this never touches your config. The usage ledger records the model each
agent reported, or the requested one, so runs on different agents can be compared in
`usage.json`.

## How it is built

Three layers, and only the bottom one knows anything about a particular CLI:

```
migite core     bash phases (lib/phases/*.sh) and the LangGraph tools
    │           names a role, a label, a permission word, a scope; never a flag or a model
    ▼
gateway         migite_call.py, the same code for every agent
    │           role → model and effort, capability fallbacks, the long-prompt pointer,
    │           process start, timeout, AgentError, the usage ledger
    ▼
adapters        agents/base.py        the interface and shared types
                agents/claude.py      flags, output parser, model ids, permission map,
                agents/cursor.py      scopes, variables to unset, exit command,
                agents/opencode.py    instruction files, display name
```

- **Python tools** call `migite_call.call_agent(prompt, role, label=..., schema=..., scopes=...)`.
- **Bash** calls `agent_ask <label> <role>` (headless, prompt on stdin), `agent_think` (the same
  with a spinner), and `run_phase` (interactive). All three go through `migite_agent.py`, whose
  subcommands are `ask`, `session`, `info`, and `check`.
- **The agent's description** (`migite_agent.py info --shell`) is cached as `MIGITE_AGENT_*` when
  the config loads, so bash messages and capability checks read variables, not CLI knowledge.

The interface each adapter implements (`agents/base.py`):

```python
class Agent:
    info: AgentInfo   # name, display_name, default_binary, models per tier, structured_output,
                      # effort, usage, scopes, prompt_via, max_arg_bytes, env_unset,
                      # exit_hint, instruction_files
    def ask_launch(self, req: AskRequest) -> Launch          # argv, stdin, env to set or unset
    def parse(self, stdout, returncode, req) -> AskResult    # that CLI's output in one shape
    def session_launch(self, req: SessionRequest) -> Launch
    def version_argv(self) -> list[str]                      # for migite doctor
```

Adapters are pure translation: they never start a process and never read the config.

## Adding an agent

1. **Write the adapter.** Create `agents/<name>.py` with one `Agent` subclass. Start from
   `agents/cursor.py`, the smallest one:

   ```python
   from .base import Agent, AgentInfo, AskRequest, AskResult, Launch, SessionRequest, plain_text_result

   class CodexAgent(Agent):
       info = AgentInfo(name="codex", display_name="Codex", default_binary="codex",
                        models={"fast": None, "standard": None, "strong": None},
                        prompt_via="arg", exit_hint="/quit", instruction_files="AGENTS.md")

       def ask_launch(self, req: AskRequest) -> Launch: ...
       def parse(self, stdout, returncode, req) -> AskResult: ...
       def session_launch(self, req: SessionRequest) -> Launch: ...
   ```

2. **Register it** in `AGENTS` in `agents/__init__.py`. The config accepts the new
   `agent.backend` value from then on.
3. **Add a fake CLI** under `tests/` that prints the agent's real output format, and add a row
   for it to `FAKES` in `tests/test_agents_contract.py`. The contract tests then check the new
   adapter the same way as the others: the prompt travels exactly once, every permission word
   builds, capabilities match what reaches the command line, plain text falls back, and one
   round trip works through the gateway.
4. **Add adapter tests** for its flags and parser next to the others in `tests/test_agents.py`.
5. **Add a column** to the tables on this page.

No other file changes: bash, the tools, and the gateway read everything they need from the
adapter.

## Known limits

- Cursor and OpenCode have not been exercised live from this checkout. The adapters follow the
  documented flags and output shapes and are covered by unit and contract tests with fake CLIs;
  the first real run on each should be watched, and `usage.json` will show whether usage parsed.
  Cursor's `/quit` exit hint is from its docs and also unverified.
- Without structured output, the verdict comes from the markdown parser. It is anchored on the
  Verdict heading and tested against every past review, but a typed enum is stronger.
- Cursor headless mode without `--force` only proposes changes; migite maps `auto` and `edits`
  to `--force`, so the heal loop needs one of those.
- The `jira.read` scope maps to claude.ai's Atlassian connector tools. A different Atlassian MCP
  server exposes different tool names, and those are not configurable yet. With `acli` installed
  and logged in, the Jira fetch uses it instead and needs no scope at all
  ([tickets.md](./tickets.md)).
