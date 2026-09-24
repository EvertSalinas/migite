# Blueprint a project

For a project that doesn't exist yet. `migite-blueprint` turns a brief into a complete project
definition (domain model, user roles and flows, API surface, technical conventions, risks, MVP
scope), then cuts it into sequenced milestones, each written as a migite intake, plus a seed
`knowledge.md` so the very first migite run in the new repo already knows the conventions.

```text
brief ("a billing API for a SaaS product")
   │
   ├─ stack ────────── from --stack, or inferred from the brief
   ├─ 5 analysts ───── domain model · roles and flows · API surface · tech conventions · risks
   ├─ synthesis ────── blueprint.md
   └─ extraction ───── intake-01..NN (one per milestone) + knowledge.md
```

**Use it when** there is no code to read yet. For a change to an existing codebase, use
[Explore an initiative](./explore-an-initiative.md) instead; it reads the code.

## Walkthrough: from a brief to the first milestone

### 1. Write the blueprint

```bash
migite-blueprint --brief ./billing-brief.md --name billing-api
```

The brief can be one sentence or several pages; more is better. Say who uses it, what it must do on
day one, and any constraint you already know (a provider, a compliance rule, a stack).

```text
  migite-blueprint | …
  Project: billing-api
  Output:  …/dev-log/Personal/billing-api/blueprint
  …
```

### 2. Edit it

Open `blueprint.md` and make it yours: rename entities, cut MVP scope, reorder milestones. It is the
most consequential document of the project, and a few minutes here saves hours later. If you
changed the milestones, regenerate the intakes and the knowledge seed from your edited version:

```bash
migite-blueprint --from-blueprint …/dev-log/Personal/billing-api/blueprint/blueprint.md
```

### 3. Create the repo and seed its memory

```bash
mkdir -p ~/Code/Personal && cd ~/Code/Personal
rails new billing-api --api --database=postgresql      # whatever the blueprint's stack is
cd billing-api
git add -A && git commit -m "chore: new app"             # rails new already ran git init
```

Copy the knowledge seed to where migite reads this repo's memory: `<vault>/<org>/<repo>/`, where the
org is the folder containing the repo (`Personal` here) and `<vault>` is `vault.base` from
`migite config`:

```bash
cp …/blueprint/knowledge.md <vault>/Personal/billing-api/knowledge.md
```

### 4. Build milestone by milestone

```bash
git switch -c feature/foundation
migite …/blueprint/intake-01-foundation.md --blueprint …/blueprint/blueprint.md
```

`--blueprint` makes the blueprint's decisions constraints for the planner rather than open
questions. Commit, then move to `intake-02-...` on a new branch. See
[Start from an intake](./start-from-an-intake.md#work-through-a-series-of-intakes).

## Variants

### A one-line brief

```bash
migite-blueprint "build a billing API for a SaaS product"
```

Migite asks for the project name.

### Fully interactive

```bash
migite-blueprint
```

Prompts for the brief (finish with an empty line) and the name.

### State the stack

```bash
migite-blueprint --brief ./billing-brief.md --name billing-api --stack "Next.js + TypeScript + Postgres"
```

Without `--stack`, one call infers it from the brief. State it when the brief doesn't, or when the
inferred one is wrong. Every analyst writes in that stack's idioms.

### Write it somewhere specific

```bash
migite-blueprint --brief ./billing-brief.md --name billing-api --output ./planning/billing-api
```

The default is `<vault>/<org>/<name>/blueprint`, with `Personal` as the org unless `vault.org` or
`MIGITE_ORG` says otherwise.

## What you get

| File | Contents |
|---|---|
| `blueprint.md` | domain model, user flows, API surface, tech decisions, MVP scope, risks, milestones |
| `knowledge.md` | seed conventions and decisions for the new repo's memory |
| `intake-NN-<slug>.md` | one migite intake per milestone, in order |
| `milestones-raw.md` | only when the milestones couldn't be parsed; the raw text, so nothing is lost |

## When something goes wrong

| Symptom | Fix |
|---|---|
| no `intake-*.md`, but a `milestones-raw.md` | fix the milestone headings in `blueprint.md`, then `--from-blueprint` |
| the analysts assume the wrong stack | pass `--stack` |
| the first migite run ignores the conventions | `knowledge.md` isn't where migite looks; check `<vault>/<org>/<repo>/` with `migite config` |

## See also

- [standalone-tools.md](../standalone-tools.md#migite-blueprint): every option
- [Start from an intake](./start-from-an-intake.md): building the milestones
