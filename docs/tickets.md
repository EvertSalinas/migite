# Tickets

`migite --jira BB-1234` does two things with a ticket. The **key** names the task: the scratchpad
and vault folder, the amend target, the PR title suffix. The **content** (title, type, priority,
status, description, acceptance criteria) is fetched once before planning, cached as
`jira-context.md`, and given to the planner as the authoritative statement of scope.

Both live in one sub-tool, `migite-ticket` (`migite_ticket.py`), so no phase knows how a ticket is
parsed or retrieved. Where the content comes from is a config choice.

## Where the content comes from

| Source | How | Cost | Works on |
|---|---|---|---|
| `jira-acli` | Atlassian's own CLI, `acli`, logged in once through your browser | free, a second or two, exact fields | every agent |
| `jira-agent` | one headless call through the agent's Atlassian MCP tools, restricted to the `jira.read` scope | one model call, 10 to 30 seconds, the model's rendering of the fields | agents whose adapter maps `jira.read` (today: Claude Code, through claude.ai's Atlassian connector) |

```yaml
tracker:
  provider: auto        # auto | jira-acli | jira-agent | none   (MIGITE_TRACKER)
```

With `auto` (the default), migite uses `jira-acli` when `acli` is installed and logged in, else
`jira-agent` when the agent can run a `jira.read`-scoped call, else nothing. A source that fails
falls through to the next one, with a warning naming why. When no source can run, planning
proceeds without the ticket body, and the key still names the task. `none` never fetches.

`migite doctor` and `migite-ticket sources` show which source would be used here, and why:

```text
$ migite-ticket sources
tracker.provider: auto
  ✔ jira-acli   acli logged in to acme.atlassian.net
  ✔ jira-agent  Claude Code with its Atlassian MCP tools (one model call)
  → jira-acli would be used
```

<a id="setup"></a>
## Setting up acli

`acli` handles authentication itself, through your browser (OAuth). There is no token to create,
store, or rotate, and nothing to put in your shell profile or in a config file.

1. **Install it.** On macOS with Homebrew:

   ```bash
   brew install atlassian/acli/acli
   ```

   For Linux and Windows, follow Atlassian's
   [install guide](https://developer.atlassian.com/cloud/acli/guides/how-to-get-started/).

2. **Log in once.** A browser window opens; pick your Jira site and approve:

   ```bash
   acli jira auth login --web
   ```

3. **Check it.** Both should succeed:

   ```bash
   acli jira auth status                 # ✓ Authenticated, and the site
   migite-ticket sources                 # → jira-acli would be used
   migite-ticket BB-1234                 # a real key: the ticket as markdown
   ```

The login is stored by `acli`, not by migite, and applies to every terminal until you run
`acli jira auth logout`. To switch sites or accounts, use `acli jira auth switch`.

`acli` supports Jira Cloud (`*.atlassian.net`). Some organisations require an admin to allow the
Atlassian CLI first; if the browser login is refused, ask whoever manages your Atlassian site.

**If `acli` is not on your PATH** (another install location, or a wrapper), point migite at it:

```yaml
tracker:
  jira:
    acli: /opt/homebrew/bin/acli   # or MIGITE_ACLI=... for one run
```

**Acceptance criteria.** Many Jira sites keep them in a custom field. Set
`tracker.jira.acceptance_field` to its id and migite requests it directly:

```yaml
tracker:
  jira:
    acceptance_field: customfield_10035
```

Without it, migite takes the section under an "Acceptance criteria" or "Definition of done"
heading in the description. With neither, the ticket says "None specified in the ticket" and the
planner works from the description.

Only the fields migite renders are requested: summary, type, priority, status, labels,
description, and the acceptance field when set. `acli` is started with an argument list, never a
shell, and the ticket key is validated before it is passed. The account email `acli` reports is
never printed or written.

## The command

```bash
migite-ticket BB-1234                                  # fetch; markdown on stdout
migite-ticket https://acme.atlassian.net/browse/BB-1234 --out ticket.md
migite-ticket fetch BB-1234 --source jira-acli         # one source only, no fallback
migite-ticket sources [BB-1234]                        # which source would be used, and why
migite-ticket parse <key-or-url>                       # {"key", "url", "base_url"}
```

Inside a git repo it applies that repo's `.migite.yml`. Exit codes: `0` fetched, `1` every
available source failed (or the input isn't a ticket), `2` no source can run here. `--out` is
written only on success.

The key parser is the same one migite and every standalone tool use: a key (`bb-1234`, project keys
may contain digits) or any `https://…/browse/KEY` URL. When you pass a URL, it becomes the ticket's
link; with a bare key, the link is built from the site `acli` is logged in to.

## What the planner receives

Every source produces the same markdown, so the planner never knows where it came from:

```markdown
## Jira ticket: BB-1234
**Title:** Export invoices as PDF
**Type:** Story
**Priority:** High
**Status:** In Progress
**Labels:** billing
**Link:** https://acme.atlassian.net/browse/BB-1234
**Description:**
Admins need to download an invoice as a PDF from the invoice page.

**Acceptance criteria:**
- The PDF matches the on-screen invoice
- Only the invoice's owner can download it
```

## Why acli first

Fetching a ticket is data retrieval, not reasoning. Through the agent it costs a model call
(mostly the CLI's own system context), takes seconds, returns the model's paraphrase of the
fields, and signals failure with a marker string in prose. Through `acli` it is free, exact, the
same on every agent, and the login is Atlassian's own OAuth flow, so migite never touches a
credential. The agent source stays as the fallback for machines without `acli`.

Interactive sessions are unaffected: if your agent has Jira MCP tools configured, it can still use
them while implementing.

## Adding another tracker

Write `trackers/<name>.py` with one `Tracker` subclass (`available(ref)` and `fetch(ref)`, which
returns `trackers.render(Ticket(...))`; Jira-shaped JSON can go through
`trackers.jira_format.issue_to_ticket`), add it to `SOURCES` in `migite_ticket.py` and to
`TRACKER_PROVIDERS` in `migite_config.py`, and add tests with a stubbed transport like the fake
`acli` runner in `tests/test_migite_ticket.py`. No phase changes.
