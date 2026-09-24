# Workflows

Task-oriented tutorials: one file per thing you do with migite, each with a complete walkthrough
and the variants you will reach for next. The reference docs ([migite.md](../migite.md),
[phases.md](../phases.md), [configuration.md](../configuration.md)) say what every flag and key
does; these say which ones to combine, in what order, for a real piece of work.

## Pick a workflow

| You want to... | Workflow | Main command |
|---|---|---|
| Build a change that is already decided | [Build a task](./build-a-task.md) | `migite --jira BB-1234` |
| Act on PR comments or QA feedback after building | [Amend a task](./amend-a-task.md) | `migite --amend "..."` |
| Run a task someone (or a tool) already wrote up | [Start from an intake](./start-from-an-intake.md) | `migite intake-01-....md` |
| Decide whether a big initiative is worth doing | [Explore an initiative](./explore-an-initiative.md) | `migite-explore "..."` |
| Define a new project before any code exists | [Blueprint a project](./blueprint-a-project.md) | `migite-blueprint "..."` |
| Find what is wrong with a codebase, then fix it | [Audit and remediate](./audit-and-remediate.md) | `migite-audit`, then `migite --audit` |
| Review a teammate's branch | [Review a pull request](./review-a-pull-request.md) | `migite-pr-review --branch ...` |
| Read a Jira ticket the way the planner does | [Read a ticket](./read-a-ticket.md) | `migite-ticket BB-1234` |
| Set migite up for yourself or a team, and keep it healthy | [Configure migite](./configure-migite.md) | `migite config --edit`, `migite doctor` |
| Run any of the above on Cursor or OpenCode | [Use another agent](./use-another-agent.md) | `MIGITE_AGENT=cursor migite ...` |

The workflows chain. The common paths:

```text
explore an initiative ──► intakes ──► start from an intake ──► (amend) ──► commit
blueprint a project ────► intakes ──► start from an intake ──► ...
audit ──────────────────► migite --audit ──► build a task ──► ...
ticket ─────────────────► build a task ──► amend a task ──► review a pull request (teammate)
```

## Conventions in these tutorials

- **The example project** is a Rails API, `invoices-api`, in `~/Code/Acme/invoices-api`, so its
  org folder in the vault is `Acme`. The Jira ticket is `BB-1234`. Substitute your own.
- **Terminal output is trimmed.** Lines you would see are shown as they appear; `…` marks omitted
  lines, and text after `←` is commentary, not output.
- **Every command takes `--help`**, even outside a repo: `migite --help`, `migite config --help`,
  `migite-explore --help`, and so on.
- **Nothing is ever committed for you.** Every workflow that changes code stops at a gate and ends
  with the change in your working tree, ready for `git commit`.

## Before any workflow

```bash
migite doctor            # agent CLI, tools, config, prompts: fix anything marked ✘ first
migite config            # the settings in effect here, and which file each one came from
```

## Gate keys, all in one place

| Gate | When | Keys |
|---|---|---|
| Plan gate | after planning | `y` approve · `f` one line of feedback, one refine call · `e` edit `plan.md` · `n` full re-plan · `q` abort |
| Amendment gate | after an amendment is drafted | `y` approve · `f` feedback refine · `e` edit · `q` abort |
| Stage checkpoint | between `--staged` layers | `c` continue · `r` redo · `e` note for the next layer · `q` abort |
| Commit gate | after review | `y` approve · `f` the agent fixes the findings · `e` edit `review.md` · `n` you fix, then Enter · `q` abort |
| Intake confirm | when starting from an intake or audit | `y` proceed · `e` edit · `q` abort |

Aborting with `q` loses nothing: re-running the same command resumes from the files already in
the scratchpad.
