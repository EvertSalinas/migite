# Read a ticket

`migite-ticket` fetches a Jira ticket and prints it exactly as migite's planner will see it: title,
type, priority, status, description, acceptance criteria, link. It is what `migite --jira` runs
before planning, available on its own for checking a ticket, scripting, or feeding another tool.

```text
BB-1234 or https://…/browse/BB-1234
   │
   ├─ parse ──────── the key and the site
   ├─ source ─────── acli when it is logged in, else the agent's Atlassian MCP tools
   └─ markdown ───── the same shape whichever source ran
```

## One-time setup

Install Atlassian's CLI and log in through your browser. There is no token to create or store.

```bash
brew install atlassian/acli/acli
acli jira auth login --web           # pick your site in the browser
migite-ticket sources                # → jira-acli would be used
```

Linux and Windows installs: [tickets.md](../tickets.md#setup).

## Walkthrough: check a ticket before planning

```bash
migite-ticket BB-1234
```

```markdown
## Jira ticket: BB-1234
**Title:** Export invoices as PDF
**Type:** Story
**Priority:** High
**Status:** In Progress
**Link:** https://acme.atlassian.net/browse/BB-1234
**Description:**
Admins need to download an invoice as a PDF from the invoice page.

**Acceptance criteria:**
- The PDF matches the on-screen invoice
- Only the invoice's owner can download it
```

If the acceptance criteria say "None specified in the ticket", add them to the intake when migite
opens it, or to the ticket itself: the planner treats them as the definition of done.

## Variants

### From a URL

```bash
migite-ticket https://acme.atlassian.net/browse/BB-1234 --out ./bb-1234.md
```

A URL also sets the ticket's link. `--out` writes a file, only when the fetch succeeds.

### See which source would run, and why

```bash
migite-ticket sources
```

```text
tracker.provider: auto
  ✔ jira-acli   acli logged in to acme.atlassian.net
  ✔ jira-agent  Claude Code with its Atlassian MCP tools (one model call)
  → jira-acli would be used
```

`migite doctor` prints the same.

### Force one source

```bash
migite-ticket fetch BB-1234 --source jira-acli      # fail rather than fall back
migite-ticket fetch BB-1234 --source jira-agent     # compare with the agent's rendering
```

### Use it in a script

Exit codes: `0` fetched, `1` failed (or not a ticket), `2` no source can run.

```bash
if migite-ticket fetch "$KEY" --out "/tmp/$KEY.md"; then
  grep -A20 '^\*\*Acceptance criteria' "/tmp/$KEY.md"
else
  case $? in
    2) echo "set up acli: acli jira auth login --web" ;;
    *) echo "could not fetch $KEY" ;;
  esac
fi
```

### Parse a reference without fetching

```bash
migite-ticket parse https://acme.atlassian.net/browse/bb-1234
# {"key": "BB-1234", "url": "https://acme.atlassian.net/browse/bb-1234", "base_url": "https://acme.atlassian.net"}

eval "$(migite-ticket parse --shell "$1")"   # TICKET_KEY=BB-1234, TICKET_URL=...
```

The same parser `migite --jira` and the standalone tools use: keys (project keys may contain digits)
or any `https://…/browse/KEY` URL.

### Acceptance criteria in a custom field

If your Jira keeps acceptance criteria in a custom field, name it once:

```bash
migite config --edit --user
```

```yaml
tracker:
  jira:
    acceptance_field: customfield_10035
```

Without it, migite reads the section under an "Acceptance criteria" or "Definition of done"
heading in the description.

### Without acli

With `acli` missing or logged out, `auto` falls back to one agent call through the Atlassian MCP
tools, on agents that can restrict a call to them (Claude Code). It costs a model call and returns
the model's rendering of the fields. To never fetch at all, set `tracker.provider: none`; the key
still names the task.

## When something goes wrong

| Message | Fix |
|---|---|
| `acli is not installed` | install it, or point `tracker.jira.acli` at it |
| `acli is not logged in to Jira` | `acli jira auth login --web` |
| `Issue does not exist or you do not have permission to see it` | check the key; `acli jira auth status` shows which site you are logged in to |

More in [troubleshooting.md](../troubleshooting.md#jira).

## See also

- [tickets.md](../tickets.md): sources, setup, and adding another tracker
- [Build a task](./build-a-task.md): where the ticket is used
