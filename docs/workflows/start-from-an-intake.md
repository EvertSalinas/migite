# Start from an intake

An intake is the written-up task migite plans from. Normally migite copies a template and you fill
it in; this workflow starts from one that already exists: a workstream from
[Explore an initiative](./explore-an-initiative.md), a milestone from
[Blueprint a project](./blueprint-a-project.md), or one you or a teammate wrote by hand. Migite
skips the type picker and the template editor, shows you the file, and plans from it.

```text
intake-01-extract-provider-adapter.md
   │
   ├─ confirm ─────── first 30 lines shown: proceed, edit, or abort
   ├─ extra details ─ optional task.md for anything the file doesn't cover
   └─ plan → PLAN GATE → implement → ... (the rest of "Build a task")
```

## Walkthrough: a workstream from an exploration

`migite-explore ... --intakes` left three intakes in the vault. Start the first:

```bash
cd ~/Code/Acme/invoices-api
git switch -c refactor/extract-provider-adapter
migite ~/dev-log/Acme/invoices-api/exploration-billing-2026-09-20/intake-01-extract-provider-adapter.md
```

An existing `.md` path as the argument is read as an intake; `--intake <file>` says the same thing
explicitly.

```text
▶ Intake file (positional): …/intake-01-extract-provider-adapter.md
  Intake loaded from …/intake-01-extract-provider-adapter.md — first 30 lines:

  Title: Extract a provider adapter from BillingService
  Type: refactor
  …
Proceed with this intake? [y/e/q] (y=proceed, e=edit, q=abort):
```

`y` to plan from it as written, `e` to adjust it in your editor first. Then:

```text
Add supplementary details in a separate task.md before planning? [y/N]:
```

Answer `y` to write anything the intake doesn't say (a constraint from a meeting, a file to follow
as an example). It is saved as `task.md` beside the intake and read as authoritative; the intake
itself stays exactly as the tool produced it. From here the run is [Build a task](./build-a-task.md)
from the plan gate on.

## Variants

### Write an intake by hand

Copy a template and fill it in, or write the minimum yourself. Put `Title:` and `Type:` as plain
lines at the top: that is what migite reads for the folder name and the task type (the bold
`**Type:**` form in the templates is not picked up when you pass a file, and falls back to the type
picker).

```markdown
Title: Add rate limiting to POST /sessions
Type: bug

## Context
Brute-force attempts on /sessions are not throttled. Rack::Attack is already configured for
/passwords in config/initializers/rack_attack.rb; follow that.

## Acceptance criteria
- 6th attempt from one IP within a minute returns 429
- a successful login resets the counter
- request spec covers 429 and the reset

## Out of scope
- throttling by account instead of IP
```

```bash
migite ./intakes/rate-limit-sessions.md
```

Valid types: `feature`, `bug`, `refactor`, `spike`, `config`. A missing `Title:` falls back to the
file name for the folder, with a warning.

### Override the type

```bash
migite --intake ./intake-02-add-webhooks.md --type spike
```

`--type` always wins over the file's `Type:` line. Useful when a workstream turns out to need a
spike before real work.

### Attach reference material

```bash
migite --intake ./intake-01-foundation.md --attach ./provider-api-docs.md
```

The attachment lands in `task.md` and reaches every planning call. See
[Build a task](./build-a-task.md#give-the-planner-reference-material).

### Plan within settled architecture

```bash
migite --intake ./intake-02-auth.md --blueprint ~/dev-log/Personal/billing-api/blueprint/blueprint.md
```

The blueprint's domain model, API surface, and technical decisions are given to the planner and
critic as constraints, not open questions. An `exploration.md` works the same way when a workstream
should follow the approach the exploration chose.

### Work through a series of intakes

Run them one at a time, committing between them, so each plan is made against the code the
previous one produced:

```bash
migite ./intake-01-foundation.md      # plan, build, review, commit
migite ./intake-02-auth.md            # the explorers now see milestone 1's code
migite ./intake-03-billing.md
```

Each intake gets its own task folder, named after its title.

## What you get

The same files as [Build a task](./build-a-task.md#what-you-get), plus `task.md` when you added
details or attachments. `intake.md` in the task folder is a copy of your file.

## See also

- [migite.md, intake mode](../migite.md#intake-mode): exactly what is read from the file
- [Explore an initiative](./explore-an-initiative.md) and [Blueprint a project](./blueprint-a-project.md): where intakes come from
