# Audit and remediate

Find what is wrong with an existing codebase, then fix it with the same plan, review, and gate
discipline as any task. `migite-audit` runs one auditor per layer (models, controllers, services,
serializers, jobs, migrations, schema and indexes) in parallel and writes a report ranked by
severity. `migite --audit <report>` turns that report into a remediation task.

```text
migite-audit
   │
   ├─ 7 auditors ──── one per Rails layer, in parallel
   ├─ synthesis ───── audit-<date>.md, findings ranked 🔴 🟡 🟢
   │
migite --audit audit-<date>.md
   │
   ├─ intake ──────── generated from the report, type refactor
   ├─ confirm ─────── proceed, edit, or abort
   └─ plan (with every 🔴 and 🟡 finding in full) → PLAN GATE → implement → ... → COMMIT GATE
```

**Use it when** you inherit a codebase, before a big feature lands on an area you don't trust, or
periodically on a layer that keeps producing incidents. The auditors look for architectural
problems: N+1 queries, missing indexes, unscoped queries, unsafe migrations, jobs that aren't
idempotent.

## Walkthrough: audit the jobs layer, fix what matters

### 1. Audit

```bash
cd ~/Code/Acme/invoices-api
migite-audit --focus jobs
```

```text
  migite-audit | model: explore=claude-sonnet-5  think=claude-sonnet-5
  Repo: Acme/invoices-api
  Focus: jobs
  …
  ▶ Writing report
    ✔ …/dev-log/Acme/invoices-api/audit-2026-09-24.md

  Results: 2 critical  5 warnings  3 notes
  → Run: migite --type refactor  to open a refactor session

  ✔ migite-audit complete
```

Read-only: nothing in the repo changes.

### 2. Decide what to fix

Open the report. Findings are grouped by severity with file paths. Decide the scope of one
remediation task: usually the critical findings, plus warnings in the same files. Edit the report
to remove what you won't fix now; the remediation plans from what is in it.

### 3. Remediate

```bash
git switch -c refactor/jobs-audit-fixes
migite --audit ~/dev-log/Acme/invoices-api/audit-2026-09-24.md
```

```text
  …                                                     ← the generated intake is shown
Proceed with this intake? [y/e/q] (y=proceed, e=edit, q=abort):
```

`e` to narrow the intake further, then `y`. The type defaults to `refactor`, and every critical and
warning finding reaches the planner and critic in full, even when the report is long. From here it
is [Build a task](./build-a-task.md) from the plan gate on.

## Variants

### Audit everything

```bash
migite-audit
```

All seven layers. Takes longer than one layer, and the report is long; that is the point of `--focus`.

### Other layers

```bash
migite-audit --focus controllers
migite-audit --focus schema          # matches schema_indexes
```

Matching is by case-insensitive substring. An unmatched value audits everything, with a warning.

### File the audit under a ticket

```bash
migite-audit --jira BB-1400
migite --audit ~/dev-log/Acme/invoices-api/bb-1400/audit-20260924-103000.md --jira BB-1400
```

The report lands in the ticket's vault folder, and the remediation run is filed under the same
ticket.

### Split the findings into several tasks

One large remediation is hard to review. Copy the report, keep only the findings for one area or one
kind of problem in each copy, and remediate each copy on its own branch:

```bash
migite --audit ./audit-n-plus-one.md
migite --audit ./audit-unsafe-migrations.md
```

### Write the report somewhere else

```bash
migite-audit --output ./audits/jobs.md
```

## What you get

| File | Contents |
|---|---|
| `audit-<date>.md` | findings by severity with file paths, from `migite-audit` |
| the usual task files | from the remediation run: plan, testing plan, review, PR description ([Build a task](./build-a-task.md#what-you-get)) |

## When something goes wrong

| Symptom | Fix |
|---|---|
| the audit finds nothing on a generic (non-Rails) repo | the auditors are Rails layers; use [Explore an initiative](./explore-an-initiative.md) for other codebases |
| the remediation plan tries to fix everything | edit the report or the generated intake down to one scope before planning |

## See also

- [standalone-tools.md](../standalone-tools.md#migite-audit): every option
- [migite.md, audit mode](../migite.md#audit-mode): how the report becomes an intake
