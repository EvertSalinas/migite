# Standalone tools

Reference for the four tools that run independently of `migite`, plus the small
`migite-ticket` helper. See the [README](../README.md) for the "which tool for which situation" table.
Every tool prints its options with `--help`, outside a repo and before any dependency check.

All four read the same layered configuration as `migite`
([docs/configuration.md](./configuration.md)): `vault.base` / `vault.org` for output paths,
`models` for every call (roles `lens`, `explore_synth`, `challenge`, `explore_refine`, `analyst`,
`blueprint_synth`, `extract`, `audit_area`, `audit_synth`, `pr_review_<dimension>`, `pr_verdict`),
`models.effort` / `models.roles_effort` for `--effort`, `models.timeout_seconds`, and
`permissions.headless`. The "Models used" notes below describe the original defaults; the current
default tiering (Opus 5.5 for the strong tier, Sonnet for audit areas and explore refine on the
strong tier) is in [docs/configuration.md](./configuration.md#models). Every call is metered in
the usage ledger when `MIGITE_USAGE_LEDGER` is set.

## Contents

- [`migite-blueprint`](#migite-blueprint)
- [`migite-explore`](#migite-explore)
  - [Re-extracting intakes (`--from-exploration`)](#re-extracting-intakes)
- [`migite-audit`](#migite-audit)
- [`migite-pr-review`](#migite-pr-review)
- [`migite-ticket`](#migite-ticket)

---

<a id="migite-blueprint"></a>
### `migite-blueprint`

Defines a new project from a brief before any code exists. Runs 5 parallel specialist analysts
(domain model, user roles & flows, API surface, tech conventions, risks), synthesises a complete
blueprint with Opus 5.5, then extracts sequenced milestone intake files and a knowledge.md seed.

**Stack-agnostic:** a new project has no code yet for the tool to read, so it determines a
stack up front — either taken verbatim from `--stack`, or inferred from the brief (its own
Sonnet 5 call) — and threads it directly through every analyst prompt and the synthesis prompt.
None of those prompts hardcode Rails; they ask for whatever's idiomatic to the chosen stack (a
Rails model, a Prisma relation, a Django `ForeignKey`, `npm run lint` vs `bundle exec rubocop`,
etc.). Milestone/knowledge extraction don't receive the stack directly — they work from the
already-stack-aware `blueprint.md` text instead.

```bash
# One-liner brief — prompts for project name, stack inferred from the brief
migite-blueprint "build a billing API for a SaaS product"

# No args at all — fully interactive, prompts for both the brief and the project name
migite-blueprint

# Brief from file
migite-blueprint --brief path/to/brief.md --name billing-api

# State the stack explicitly instead of letting it be inferred
migite-blueprint --brief brief.md --name billing-api --stack "Next.js + TypeScript + Postgres"

# Write to a specific directory
migite-blueprint --brief brief.md --name billing-api --output ./billing-api/

# Re-extract milestone intakes AND knowledge.md from an already-generated (and edited) blueprint
migite-blueprint --from-blueprint ./output/blueprint.md
```

**What it produces** (all in the output directory):

| File | Contents |
|------|----------|
| `blueprint.md` | Domain model, user flows, API surface, tech decisions, MVP scope, risks, milestones |
| `knowledge.md` | Seed conventions and decisions — drop into `dev-log/<org>/<repo>/` before the first migite run |
| `intake-01-<slug>.md` | Migite intake for milestone 1 (Foundation) |
| `intake-02-<slug>.md` | Migite intake for milestone 2 |
| `intake-NN-<slug>.md` | … one file per milestone, ready to pass to `migite` |
| `milestones-raw.md` | Only written if the extraction output couldn't be parsed into milestone blocks — the raw text lands here instead of being silently dropped |

**Recommended workflow:**

```
1. migite-blueprint "your brief"     → blueprint.md + intake-NN-*.md
2. Edit blueprint.md to your taste
3. Create a fresh repo for your chosen stack (e.g. `rails new <project>` for Rails)
4. cp knowledge.md dev-log/<org>/<repo>/knowledge.md
5. migite --intake intake-01-foundation.md   → implement milestone 1
6. migite --intake intake-02-auth.md         → implement milestone 2
   ...
```

If you edited `blueprint.md` and want to regenerate the intakes AND `knowledge.md` from the updated version — this rewrites `blueprint.md` too (unchanged content), and defaults `--output` to the blueprint file's own directory when not given:
```bash
migite-blueprint --from-blueprint ./output/blueprint.md
```

**Models used:** `claude-sonnet-5` for stack inference (when `--stack` isn't given), the 5 parallel
analysts, milestone extraction, and knowledge-seed extraction; `claude-opus-5-5` for blueprint
synthesis (the most consequential call — shapes everything downstream).

**Default output:** `~/dev-log/Personal/<project-name>/blueprint/` (or `~/dev-log/$MIGITE_ORG/<project-name>/blueprint/` if `MIGITE_ORG` is set), where
`<project-name>` is the sanitized project name (lowercased, slugified), not necessarily what you
typed verbatim.

---

<a id="migite-explore"></a>
### `migite-explore`

Scopes a large initiative against an existing codebase **without implementing anything**. Use it
when you want to know what a big change would actually take before committing to it - the output
is a decision artefact, not a plan.

Like `migite-blueprint`, `migite-explore` is **language-agnostic** - but it gets there differently.
It discovers files with `git ls-files` and ranks them by keyword relevance to your brief, so it
works on any repo - Rails, Python, a shell toolchain, anything under git - because there's an
existing codebase to read. `migite-blueprint` has no code to read yet, so it determines a stack
up front instead (see above) rather than discovering one.

```bash
# Inline brief
migite-explore "make migite agent-agnostic across claude code and opencode"

# Brief from a file, with a short slug for the output directory
migite-explore --brief ./initiative.md --name agent-agnostic

# Group this exploration under an existing ticket's vault folder
migite-explore --brief ./initiative.md --jira <jira-ticket-id>

# Fold a file's content into the brief as reference material (repeatable) —
# every lens sees it, since it's just text folded into the brief before any
# Claude call happens
migite-explore --brief ./initiative.md --attach ./data-map.csv

# Also emit one migite intake per workstream, ready to feed back into migite
migite-explore "extract billing into a service" --intakes

# Run a single lens instead of all six
migite-explore "add multi-tenancy" --focus approaches

# Interactive - prompts for the brief
migite-explore

# Re-extract intakes from an existing (or hand-edited) exploration - 1 call, not 9
migite-explore --from-exploration ~/dev-log/Work/my-repo/exploration-billing-2026-08-18/exploration.md
```

**The graph:**

```
load_context   (git ls-files → keyword rank → shared repo context)
    │
    ├── lens: current_state       (how the repo does this today)
    ├── lens: touchpoints         (every file that would change, classified)
    ├── lens: coupling_and_risk   (what breaks, what has no test cover)
    ├── lens: existing_seams      (abstractions already there to build on)
    ├── lens: constraints         (contracts, compat, tooling, docs)
    └── lens: approaches          (2-3 distinct options, no winner picked)
         │
    synthesize_exploration   (Opus 5.5 — the feasibility document)
         │
    challenge_assumptions    (Opus 5.5 — adversarial: attacks the document)
         │
    refine_exploration       (Opus 5.5 — incorporates the challenges; the reviser is no weaker than the challenger)
         │
    extract_workstreams      (Sonnet 5 — only with --intakes)
         │
    write_outputs   → exploration.md + challenges.md [+ intake-NN-*.md]
```

All six lenses share one repo context but apply a different question to it — an initiative cuts
across the whole repo, so splitting by directory (the `migite-plan` approach) would fragment the
analysis. Lenses are instructed to output `UNKNOWN: <what I'd need to look at>` rather than guess;
every unknown must surface in the final Open Questions section. `--focus` matches lens names by
case-insensitive substring — an unmatched value warns and falls back to running all six rather
than erroring. If a lens, the challenge pass, or the refine pass fails outright, migite-explore
degrades that step to a placeholder/unrefined-draft instead of crashing the whole run. Repo
context is capped (~22,000 chars total, ~4,000/file, top 30 ranked files; the challenge pass sees
a further-truncated 12,000-char slice) with no warning surfaced when a large repo/initiative gets
trimmed — a known limitation, not a bug, but worth knowing on a big repo.

**Referencing external material.** Every agent call here is headless with no tool access by
default (`MIGITE_PERMISSION_MODE` only ever changes the permission word, never a working
directory to read from) — a path merely mentioned in the brief can never be opened by the model
itself. `--attach <file>` (repeatable) reads a file's raw content and folds it directly into the
brief text before any Claude call happens, so it reaches all six lenses plus synthesis, challenge,
and refine reliably. Each attachment is capped at 50,000 chars (truncated with a warning beyond
that), since it's repeated in full on every one of those ~9 calls, not read once.

The **challenge pass** is what separates this from a plan. Feasibility documents fail in
predictable ways — missed touchpoints, underestimated effort, hand-waved hard parts, a confident
verdict from thin evidence. A second Opus 5.5 pass attacks the draft on seven axes and the document
is revised against those findings.

**What it produces:**

| File | Contents |
|------|----------|
| `exploration.md` | Verdict, current state, what-would-change inventory, approach options, workstreams, open questions, decision points, risks, effort summary |
| `challenges.md` | The adversarial review — what the first draft got wrong |
| `intake-NN-<slug>.md` | One migite intake per workstream (only with `--intakes`) |
| `workstreams-raw.md` | Only written if workstream extraction couldn't be parsed into intake blocks — the raw text lands here instead of being silently dropped |

`--output`, when given, always wins over `--jira`/`--name` — no resolver lookup happens at all in that
case. Reusing an existing `--name` creates a *new* `<slug>/explore-<timestamp>/` folder alongside
any older `exploration-<slug>-<date>/` folder from before this change — the two aren't merged
automatically.

**The verdict line** is one of four, and is greppable:

| Verdict | Meaning |
|---------|---------|
| `✅ PROCEED` | The path is clear enough to plan and implement |
| `🔬 SPIKE FURTHER` | Unknowns must be resolved before committing |
| `⏸️ DEFER` | Feasible, but the cost outweighs current value |
| `❌ NOT WORTH IT` | Coupling or cost makes this a bad investment |

**Effort scale:** `S` under a day · `M` 1-3 days · `L` 1-2 weeks · `XL` 2+ weeks or unbounded until a spike lands.

**Recommended workflow:**

```
1. migite-explore "the initiative"              → exploration.md + challenges.md
2. Read exploration.md, check challenges.md for what the first draft missed
3. Resolve the Open Questions and Decision Points yourself - edit exploration.md directly
4. migite-explore --from-exploration <path>     → workstream intakes from your edited doc
5. If verdict is ✅ PROCEED:
     migite --intake intake-01-<slug>.md        → plan + implement workstream 1
     migite --intake intake-02-<slug>.md        → …then the next workstream
   If verdict is 🔬 SPIKE FURTHER:
     migite "<the spike>" --type spike          → resolve the unknown, then re-explore
```

Pass `--blueprint`-style context too if the exploration should inform planning beyond the intake:

```bash
migite --intake intake-01-extract-provider-adapter.md \
       --blueprint ./exploration-agent-agnostic-2026-08-18/exploration.md
```

<a id="re-extracting-intakes"></a>
### Re-extracting intakes (`--from-exploration`)

A full run is ~9 model calls (10 with `--intakes`), two of them Opus 5.5 with extended thinking.
Re-extraction is **one Sonnet 5 call** — it reads an existing `exploration.md` and regenerates
only the workstream intakes.

Use it when you triaged without `--intakes` and now want them, or when you edited the workstreams
by hand and want the intakes to match:

```bash
# Edit the workstreams first, then re-extract against your edits
$EDITOR ~/…/exploration-billing-2026-08-18/exploration.md
migite-explore --from-exploration ~/…/exploration-billing-2026-08-18/exploration.md
```

| Behaviour | Detail |
|-----------|--------|
| Reads | `## Workstreams` section of the exploration document |
| Writes | `intake-NN-<slug>.md` only |
| Never touches | `exploration.md` (your input) and `challenges.md` (the original run's output) |
| Output dir | Defaults to the directory containing the exploration file; overridden by `--output`; if `--jira` is passed instead, goes to that ticket's `explore-<timestamp>/` folder regardless of where the source file lives |
| Git repo | Not required when working directly on a file in the vault — **required if `--jira` is also passed**, to resolve org/repo for the ticket folder |

Warns if the document has no `## Workstreams` section rather than silently producing nothing.

If a repo `knowledge.md` exists in the vault, it is injected into every lens automatically on full runs.

**Models used:** `claude-sonnet-5` for the 6 lenses (exploration quality is the whole product —
there is no implementation phase downstream to catch a shallow read), `claude-opus-5-5` for both
synthesis and the adversarial challenge, `claude-sonnet-5` for refinement and workstream extraction.

**Default output:** no `--jira`/`--name` — `~/dev-log/<org>/<repo>/exploration-<slug>-<date>/`.
With `--jira <jira-ticket-id>` or `--name <slug>` — `~/dev-log/<org>/<repo>/<slug>/explore-<timestamp>/`,
grouped alongside any other run already using that folder (only `--jira` prefix-matches an existing
sibling; `--name` is exact-match-only).

---

<a id="migite-audit"></a>
### `migite-audit`

Audits an existing codebase for architectural problems. Runs 7 parallel specialist auditors
(models, controllers, services, serializers, jobs, migrations, `schema_indexes`), synthesises
findings ranked by severity, and writes an audit report to the vault.

```bash
# Full audit — writes to ~/dev-log/<org>/<repo>/audit-<date>.md
migite-audit

# Limit to a specific layer
migite-audit --focus jobs
migite-audit --focus controllers

# Group this audit under an existing ticket's vault folder
migite-audit --jira <jira-ticket-id>

# Write to a specific file
migite-audit --output ./audit.md
```

`--focus` matches area names by case-insensitive substring — `--focus schema` or `--focus indexes`
both hit `schema_indexes`, but `--focus "schema indexes"` (with a space) won't. An unmatched value
warns and falls back to auditing everything rather than erroring.

After the audit, feed the report directly into migite planning:
```bash
migite --audit ~/dev-log/<org>/<repo>/audit-<date>.md
```

`--output`, when given, always wins over `--jira` — no resolver lookup happens at all in that case.
When a run finds any critical finding, it prints a `migite --type refactor` suggestion alongside
the `N critical / N warnings / N notes` summary.

**Models used:** `claude-haiku-4-5-20251001` for per-area analysis, `claude-sonnet-5` for synthesis.

**Default output:** no `--jira` — `~/dev-log/<org>/<repo>/audit-<date>.md`. With
`--jira <jira-ticket-id>` — `~/dev-log/<org>/<repo>/<jira-ticket-id>/audit-<timestamp>.md`, grouped
alongside any other run already using that ticket folder.

---

<a id="migite-pr-review"></a>
### `migite-pr-review`

Reviews a teammate's PR using a local branch. Diffs the branch against the merge-base with a base
branch (`git diff base...branch` — only the branch's own commits, not everything on base since
divergence), runs rubocop and rspec on changed files (optional), then fans out 4 parallel
specialist reviewers (correctness, security, test coverage, conventions & migrations — the fourth
dimension's checklist is migration-heavy: missing `NOT NULL` defaults, reversibility, missing
`add_index`, non-idempotent jobs). Produces a review with a clear APPROVED / APPROVED WITH
COMMENTS / NEEDS CHANGES verdict. The branch must be checked out locally — it errors if the diff
comes back empty, which is the common failure mode when reviewing someone else's unfetched
branch. A single reviewer dimension failing doesn't crash the run; it's downgraded to a synthetic
Critical finding so the other three still complete. Diff and file-content context are truncated
(diff capped at 16,000 chars, file contents at ~12,000 total/2,500 per file, with further
per-reviewer truncation) with no warning surfaced — a known limitation on very large PRs.

```bash
# Review branch against the repo's default branch (auto-detected: origin/HEAD, then main/master/develop)
migite-pr-review --branch feature/<jira-ticket-id>-add-pdf-export

# With Jira ticket for reference (key or full URL — both work)
migite-pr-review --branch feature/<jira-ticket-id> --jira <jira-ticket-id>
migite-pr-review --branch feature/<jira-ticket-id> --jira https://yourcompany.atlassian.net/browse/<jira-ticket-id>

# Different base branch
migite-pr-review --branch alex/fix-n-plus-one --base develop

# Skip rubocop + rspec (use when reviewing unfamiliar code or flaky test suite)
migite-pr-review --branch feature/big-refactor --skip-tests

# Write to a specific file
migite-pr-review --branch feature/<jira-ticket-id> --output ./review.md
```

**Default output:** with `--jira <jira-ticket-id>` —
`~/dev-log/<org>/<repo>/<jira-ticket-id>/pr-review-<branch>-<timestamp>.md`, grouped alongside any
other run already using that ticket folder (created if it doesn't exist yet). Without `--jira` (or
with one that doesn't parse as a ticket key/URL), migite looks for a ticket key embedded in the
branch name itself (e.g. `feature/bb-3136-add-pdf-export` → `bb-3136`) — if `~/dev-log/<org>/<repo>/<that-key>/`
already exists (from an earlier `migite` run on this branch), the review is written there as
`pr-review-<date>.md`; unlike the `--jira` case, this folder is never created on demand, so an ad
hoc review of an arbitrary branch doesn't seed a new vault directory. Otherwise, output falls back
to `~/dev-log/<org>/<repo>/pr-review-<branch>-<date>.md`, with `/` in the branch name replaced by
`-`. `--output`, when given, always wins over all of the above — no resolver lookup happens at all
in that case. `--jira` only affects the output path when it parses as a ticket key or Atlassian
URL — a free-text value is still passed into the review as reference context, just without
grouping the output under a ticket folder.

**Every finding comes with a fix.** Each reviewer reports a finding as a problem, a **Fix** concrete
enough to apply (which file, which method, what to change, with a short code block when the change
is small), and one or two **Alternatives** with their trade-off when they genuinely exist. The
synthesis numbers the findings across severities so they can be quoted in PR comments, keeps every
fix in full, and merges duplicate reports by keeping the more concrete fix and listing the other as
an alternative. If the synthesized review leaves any finding without a fix, it is asked once more,
and the draft with fewer gaps is kept:

~~~markdown
## Findings
### Critical
#### 🔴 1. Any user can delete any item · `app/controllers/items_controller.rb:12`
**Problem:** `destroy` looks the item up by id alone, so a signed-in user can delete another user's item.
**Fix:** scope the lookup to the current user, which also turns a foreign id into a 404:
```ruby
@item = current_user.items.find(params[:id])
```
**Alternative:** an `ItemPolicy#destroy?` check, if the app already authorizes with Pundit elsewhere.

### Notes
#### 🟢 2. Unexplained retention period · `app/models/item.rb:4`
**Problem:** `30.days` has no name, and the same value appears in the purge job.
**Fix:** extract `RETENTION_PERIOD = 30.days` on `Item` and use it in both places.
~~~

**Models used:** `claude-sonnet-5` for the 4 parallel specialist reviewers, `claude-opus-5-5` for the final verdict. This tool is read-only: it never commits anything, and there's no gate to approve.

---

<a id="migite-ticket"></a>
### `migite-ticket`

Parses a ticket reference and fetches the ticket's content: the same code `migite --jira` uses.
No model call when Atlassian's `acli` is installed and logged in; otherwise the agent's Atlassian MCP tools, when the
agent supports the `jira.read` scope.

```bash
migite-ticket BB-1234                          # the ticket as markdown on stdout
migite-ticket <ticket-url> --out ticket.md     # the site comes from the URL
migite-ticket sources <ticket-url>             # which source would be used here, and why
migite-ticket parse <key-or-url>               # key, URL, and site as JSON
```

Exit codes: `0` fetched, `1` failed or not a ticket, `2` no source can run. Setup, sources, and the
output shape: [tickets.md](./tickets.md).
