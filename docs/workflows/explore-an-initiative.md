# Explore an initiative

Before committing to a big change (extracting a service, adding multi-tenancy, swapping a
provider), find out what it would actually take. `migite-explore` reads the codebase through six
analytical lenses, writes a feasibility document with a verdict, has a second pass attack that
document, and revises it. It never writes code. The output is a decision, and, if you go ahead, a
set of intakes that feed straight into migite.

```text
"extract billing into a service"
   │
   ├─ index ──────── git ls-files, ranked by the brief's keywords
   ├─ 6 lenses ───── current state · touchpoints · coupling and risk · existing seams · constraints · approaches
   ├─ synthesis ──── exploration.md, with a verdict and effort
   ├─ challenge ──── an adversarial pass: missed touchpoints, underestimated effort, hand-waving
   ├─ refine ─────── the document revised against the challenges
   └─ (intakes) ──── one migite intake per workstream, with --intakes
```

**Use it when** the question is "should we, and how big is it?", not "build this". It works on any
repo under git, Rails or not.

## Walkthrough: decide, then build

### 1. Explore

```bash
cd ~/Code/Acme/invoices-api
migite-explore "extract billing into a service behind a provider adapter" --name billing-service
```

```text
  migite-explore | lens=claude-sonnet-5  synth=claude-opus-5-5  challenge=claude-opus-5-5
  Repo:       Acme/invoices-api
  ▶ Indexing invoices-api
  ▶ Fanning out 6 analytical lenses in parallel
    ◦ current_state
    ◦ touchpoints
    …
  ▶ Synthesising feasibility document from 6 lenses
  ▶ Adversarial challenge pass
  ▶ Refining document with challenge findings
  ▶ Writing outputs
    ✔ exploration.md → …/dev-log/Acme/invoices-api/billing-service/explore-20260920-101500/exploration.md
    ✔ challenges.md

  Verdict: 🔬 SPIKE FURTHER
  Challenge pass: 1 critical  3 warnings
```

About nine model calls, a few minutes. Nothing in the repo changes.

### 2. Read it

Open `exploration.md`. Read the verdict and effort first, then Open Questions and Decision Points:
those are the parts only you can settle. Then read `challenges.md` for what the first draft got
wrong; the refined document should address each point.

| Verdict | Means | Next |
|---|---|---|
| `✅ PROCEED` | the path is clear enough to plan | step 3 |
| `🔬 SPIKE FURTHER` | an unknown must be resolved first | run the spike (below), then explore again |
| `⏸️ DEFER` | feasible, but not worth it now | record the decision, stop |
| `❌ NOT WORTH IT` | coupling or cost make it a bad investment | stop |

Effort is `S` (under a day), `M` (1 to 3 days), `L` (1 to 2 weeks), or `XL` (more, or unbounded
until a spike lands).

### 3. Settle the open questions, then extract intakes

Edit `exploration.md` directly: answer the open questions, pick an approach, adjust the Workstreams
section. Then generate intakes from your edited version, one model call:

```bash
migite-explore --from-exploration ~/dev-log/Acme/invoices-api/billing-service/explore-20260920-101500/exploration.md
```

```text
  ▶ Re-extracting workstreams from: Extract billing into a service
    ✔ intake-01-extract-provider-adapter.md
    ✔ intake-02-move-invoice-generation.md
    ✔ intake-03-retire-legacy-billing-jobs.md
  ✔ migite-explore re-extraction complete
```

`exploration.md` and `challenges.md` are left untouched.

### 4. Build each workstream

```bash
git switch -c refactor/billing-provider-adapter
migite …/intake-01-extract-provider-adapter.md \
       --blueprint …/explore-20260920-101500/exploration.md
```

`--blueprint` hands the planner the exploration's decisions as settled. See
[Start from an intake](./start-from-an-intake.md) for the rest.

## Variants

### Brief from a file

```bash
migite-explore --brief ./initiatives/billing-service.md --name billing-service
```

For a brief longer than a sentence: the motivation, constraints, what "done" means.

### Get intakes in the same run

```bash
migite-explore "extract billing into a service" --intakes
```

One extra call. Skip it when you expect to edit the workstreams first; `--from-exploration` does the
same afterwards, against your edits.

### One lens only

```bash
migite-explore "add multi-tenancy" --focus approaches
```

Lenses: `current_state`, `touchpoints`, `coupling_and_risk`, `existing_seams`, `constraints`,
`approaches`. Matching is by substring, so `--focus risk` works. An unmatched value runs all six.

### Include reference material

```bash
migite-explore --brief ./initiative.md --attach ./provider-api.md --attach ./data-map.csv
```

The model calls can't open files, so a path mentioned in the brief is never read. `--attach` folds
each file's content into the brief (up to 50,000 characters each), where every lens sees it.

### File it under a ticket

```bash
migite-explore --brief ./initiative.md --jira BB-1300
```

The output goes into the ticket's vault folder, next to any other runs for it.

### Resolve a spike first

When the verdict is `🔬 SPIKE FURTHER`, the document names the unknown. Answer it with a spike, then
explore again with the answer attached:

```bash
migite "can the provider SDK run inside a background job?" --type spike
migite-explore --brief ./initiative.md --attach ./scratchpad/<spike-slug>/plan.md
```

### Write somewhere else

```bash
migite-explore "…" --output ./explorations/billing
```

`--output` always wins over `--jira` and `--name`.

## What you get

| File | Contents |
|---|---|
| `exploration.md` | verdict, effort, current state, what would change, approach options, workstreams, open questions, decision points, risks |
| `challenges.md` | what the adversarial pass found wrong with the first draft |
| `intake-NN-<slug>.md` | one migite intake per workstream (with `--intakes` or `--from-exploration`) |

## When something goes wrong

| Symptom | Fix |
|---|---|
| the document misses a part of the codebase you know matters | name the files or classes in the brief; the index ranks by the brief's keywords |
| `--from-exploration` writes nothing | the document has no `## Workstreams` section; add one |
| a very large repo gives a shallow read | repo context is capped (about 30 files); narrow the brief, or `--focus` one lens at a time |

## See also

- [standalone-tools.md](../standalone-tools.md#migite-explore): every option and limit
- [Start from an intake](./start-from-an-intake.md): building the workstreams
