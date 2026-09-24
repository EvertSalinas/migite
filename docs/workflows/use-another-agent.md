# Use another agent

Every workflow in this section runs on Claude Code by default. Migite can drive Cursor CLI or
OpenCode instead: the phases, gates, prompts, and files are the same; only the CLI that plans,
implements, and reviews changes. This workflow covers switching, pinning models, and what behaves
differently.

## Walkthrough: switch to Cursor for one run

### 1. Log in to the CLI

```bash
cursor-agent login          # or: opencode auth login
```

### 2. Run with the agent chosen

```bash
MIGITE_AGENT=cursor migite --jira BB-1234
```

Env beats files, so this changes nothing permanently. Migite checks the CLI is installed before any
work starts, and names it in its messages:

```text
▶ Agent: Cursor (cursor-agent)
…
  Starting interactive Cursor session: Implementing
  Type /quit when done to return here
```

### 3. Make it the default

```bash
migite config --edit --user
```

```yaml
agent:
  backend: cursor            # claude | cursor | opencode
```

## Variants

### Pin models for the agent

Model ids differ per CLI, and migite doesn't guess them: until you pin the tiers, no model flag is
passed and each CLI uses its own default.

```yaml
agent:
  backend: cursor
models:
  fast: sonnet-4                     # cursor-agent --list-models
  standard: sonnet-4-thinking
  strong: gpt-5
```

```yaml
agent:
  backend: opencode
models:
  standard: anthropic/claude-sonnet-4-5     # opencode models: provider/model
  strong: anthropic/claude-opus-4-5
```

`migite config` shows the model each role now resolves to.

### A CLI in a non-standard location

```yaml
agent:
  backend: opencode
  command: /opt/opencode/bin/opencode
```

### Compare two agents on the same task

```bash
migite --jira BB-1234                             # Claude Code
cp scratchpad/bb-1234/usage.json /tmp/usage-claude.json
MIGITE_AGENT=opencode migite --jira BB-1234       # answer r at "Use existing plan or redo?"
```

`usage.json` records each agent's calls, tokens, and cost (Cursor reports none).

## What behaves differently

| Capability | Claude Code | Cursor | OpenCode |
|---|---|---|---|
| typed review verdict | yes, schema-validated | parsed from the review's markdown | parsed from the review's markdown |
| effort levels (`models.effort`) | yes | ignored | ignored |
| cost in the usage table | yes | zeros | yes |
| Jira through the agent's MCP tools | yes | no: use `acli` ([Read a ticket](./read-a-ticket.md)) | no: use `acli` |
| exit command in sessions | `/exit` | `/quit` | `/exit` |
| project rules it reads | `CLAUDE.md` | `AGENTS.md`, `.cursor/rules` | `AGENTS.md` |

Every difference is announced where it matters (the reviewer says it is parsing markdown; the
planner says when no ticket source can run), never silent. Permission words (`auto`, `edits`,
`plan`, `ask`) are mapped to each CLI's own flags.

## When something goes wrong

| Symptom | Fix |
|---|---|
| `✘ Agent CLI not found: cursor-agent` | install it, or set `agent.command` to its path |
| `Not logged in` from Cursor, `No API key configured` from OpenCode | log in with the CLI's own command |
| the Jira ticket isn't fetched on Cursor or OpenCode | set up `acli`: it works on every agent |

Cursor and OpenCode support is built from their documentation and tested against fake CLIs; watch
your first real run on each. See [agents.md](../agents.md) for the full capability matrix.

## See also

- [agents.md](../agents.md): how the adapters work, and how to add another CLI
- [Configure migite](./configure-migite.md)
