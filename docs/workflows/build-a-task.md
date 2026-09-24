# Build a task

The core workflow: take a decided piece of work from a ticket or a sentence to a reviewed change in
your working tree. Migite plans before touching a file, stops for you to approve the plan, opens an
agent session to implement it, fixes lint and test failures, reviews the result with four
reviewers, and stops again before you commit.

```text
ticket or sentence
   │
   ├─ plan ─────────── 7 explorers → synthesis → architecture critic → refine → testing plan
   ├─ PLAN GATE ────── you: approve, refine, edit, or redo
   ├─ (TDD specs) ──── optional
   ├─ implement ────── interactive agent session, you steer and exit
   ├─ auto-heal ────── rubocop autocorrect, failing specs fixed headlessly
   ├─ review ───────── authoritative lint + tests, 4 reviewers, typed verdict
   ├─ COMMIT GATE ──── you: approve, or have it fixed and re-reviewed
   └─ knowledge, PR description, improvement notes
```

**Use it when** the work is decided: a ticket with a clear ask, a bug with a known symptom, a
refactor you can describe in a sentence. If you are still deciding whether to do it at all, use
[Explore an initiative](./explore-an-initiative.md) first.

## Before you start

```bash
cd ~/Code/Acme/invoices-api
git switch -c feature/BB-1234-pdf-export     # the branch the change will land on
migite doctor                                 # fix anything marked ✘
```

A branch name that contains the ticket key (`BB-1234`) lets [Amend a task](./amend-a-task.md) find
this task again later without being told.

## Walkthrough: from a Jira ticket

```bash
migite --jira BB-1234 --type feature
```

### 1. Plan

Migite fetches the ticket (through `acli` when it is logged in, see
[Read a ticket](./read-a-ticket.md)), copies the `feature` template into the scratchpad, and opens
it in your editor. Fill in what the ticket doesn't say, especially acceptance criteria and what is
out of scope, then save and close.

```text
▶ Jira mode: BB-1234
▶ Phase 1/4 — Planning
▶ Fetching Jira ticket BB-1234...
✔ fetched BB-1234 via jira-acli
✔ Jira ticket fetched, added to planning context
▶ Created intake from template: feature              ← your editor opens here
  Starting LangGraph agent: Planning
  ▶ Fanning out 7 explorers in parallel (stack=rails)
  ▶ Synthesising plan from 7 exploration reports
  ▶ Architecture critic
  ▶ Refining plan with critic findings
  ▶ Generating testing plan
✔ Plan written to …/scratchpad/bb-1234/plan.md
```

### 2. The plan gate

The critic's findings print above the prompt. Read `plan.md` (it is in the scratchpad), then decide:

```text
── Architecture critic ─────────────────────────────
- 🟡 **Warning** — `InvoiceSerializer#pdf_url` is called per row on the index; eager-load the attachment
────────────────────────────────────────────────────
Proceed with plan? [y/f/e/n/q] (y=approve, f=feedback refine, e=edit directly, n=full redo, q=abort):
```

| The plan is... | Press | What happens |
|---|---|---|
| right | `y` | implementation starts |
| mostly right, one thing off | `f` | you type one line (`use ActiveStorage, not /tmp`), one model call revises the plan, a coloured diff shows what changed, the gate reopens |
| right except for a detail you can type faster than explain | `e` | `plan.md` opens in your editor, no model call |
| built on the wrong files or the wrong idea | `n` | the whole planner runs again from scratch |

`f` is the one you will use most. Every change is snapshotted in `.plan-history/` first.

### 3. Implement

An interactive session of your agent CLI opens with the plan and the repo's `knowledge.md`
preloaded. Watch it, answer its questions, redirect it when it drifts. Exit the session when done
(`/exit` on Claude Code); migite takes over again.

```text
▶ Phase 2/4 — Implementing
  Starting interactive Claude Code session: Implementing
  Permission mode: auto
  Type /exit when done to return here
```

### 4. Auto-heal and review

No input needed. Rubocop autocorrects with no model call, failing specs are fixed headlessly (up to
`heal.max_attempts`, default 3), then Phase 3 runs lint and tests again and four reviewers read the
diff in parallel.

```text
▶ Phase 2.5 — Running checks (auto-heal enabled, max 3 attempts)
✔ Auto-heal resolved failures after 1 attempt(s)
▶ Phase 3/4 — Reviewing
    ✔ review.json → …/review.json  (verdict=needs_fixes, 1🔴 1🟡 2🟢, source=structured)
```

### 5. The commit gate

```text
── Commit context ──────────────────────────
  Verdict: NEEDS FIXES
  Findings: 1 critical · 1 warnings · 2 notes
  Reason:  The 422 path for an invoice without line items is unhandled and untested.
  Specs:   all passed
  Rubocop: clean
  Cost:    17 calls, $2.94 so far (headless calls only)
────────────────────────────────────────────
Proceed? [y/f/e/n/q] (y=commit, f=Claude Code fixes, e=edit directly, n=fix it yourself, q=abort):
```

| The review says... | Press | What happens |
|---|---|---|
| READY TO COMMIT | `y` | knowledge capture and the PR description follow |
| a real problem the agent can fix | `f` | a session opens with the findings; when you exit it, lint, tests, and the full review re-run and the gate reopens |
| a problem that needs your judgement (a schema decision, a product question) | `n` | fix it yourself, press Enter, everything re-runs |
| a finding you disagree with | `e` | open `review.md`, strike or annotate it, then decide |

Loop through `f` or `n` as often as you need. Then `y`.

### 6. Finish

Migite asks one question for the repo's memory, writes the PR description in a short session, and
prints what it spent:

```text
▶ Phase 3.5/4 — Capturing knowledge
  Anything worth remembering from this run that migite might not catch? (optional, Enter to skip):
  invoices without line items are legal drafts, never an error
▶ Phase 4/4 — Generating PR description
✔ Workflow complete.
```

```bash
git add -A && git commit                                    # migite never commits
cat scratchpad/bb-1234/pr-description.md                    # paste into the PR
```

The complete transcript of this run, with every prompt and output, is in
[getting-started.md](../getting-started.md#first-run).

## Variants

### From a sentence instead of a ticket

```bash
migite "fix N+1 on the district index endpoint"
```

Migite asks for the type, opens that template with your sentence as the title, and continues as
above. The task folder is named after the title (`fix-n-1-on-the-district-index-endpoint`).

### From a ticket URL

```bash
migite https://acme.atlassian.net/browse/BB-1234
migite --jira https://acme.atlassian.net/browse/BB-1234
```

A browse URL as the only argument means `--jira`. The URL also tells migite which site to link to.

### Choose the type up front

```bash
migite --jira BB-1234 --type bug        # feature | bug | refactor | spike | config
migite "can we cache the tenant lookup?" --type spike
```

`spike` produces a recommendation rather than a dev plan: use it when the output you want is an
answer. Each type has its own template in `templates/`; see [migite.md](../migite.md#type-values).

### Give the planner reference material

```bash
migite --jira BB-1234 --attach ./docs/pdf-layout-spec.md --attach ./fixtures/sample-invoice.json
```

The planner's calls have no file access, so a path mentioned in the intake is never opened. `--attach`
folds each file's content into the planning context (up to 50,000 characters each). Attach the
spec, not the whole folder: every attachment is sent on every planning call.

### Write the specs first (TDD)

After the plan gate, answer `y` here:

```text
Phase 1.5 (TDD): Write spec files before implementation? [y/N]: y
```

A session writes only spec files, migite confirms they fail, and implementation then works towards
making them pass. Worth it when the acceptance criteria are precise enough to encode as specs.

### Implement in stages

```bash
migite --jira BB-1234 --staged
```

One implementation session per `###` sub-section of the plan's Scope (models, then controllers,
then ...), with a `git diff --stat` and a checkpoint between them:

```text
STAGE CHECKPOINT: 2/4 — controllers
Proceed? [c/r/e/q] (c=continue, r=redo this stage, e=edit next stage brief, q=abort):
```

Use it on large tasks where you want to check each layer before the next one builds on it. Two
known gaps: `r` doesn't yet re-run the current stage, and a note typed under `e` isn't yet passed to
the next one ([phases.md](../phases.md#phase-2-implement)).

### A repo that isn't Rails

```bash
migite "add retry with backoff to the uploader" --stack generic
```

`generic` skips rubocop, rspec, and `bundle`, and explores by file extension instead of Rails'
layers. Detection picks it automatically when there is no `Gemfile`; pass `--stack` (or set
`stack: generic` in the repo's `.migite.yml`) when detection guesses wrong, for example in a
monorepo with a stray Gemfile.

### Resume after stopping

Aborted at a gate, closed the terminal, or came back the next day? Run the same command again:

```bash
migite --jira BB-1234
```

```text
▶ Resuming existing intake: …/scratchpad/bb-1234/intake.md
…
────────────────────────────────────────
  EXISTING PLAN FOUND
  …/scratchpad/bb-1234/plan.md
────────────────────────────────────────
Use existing plan or redo? [u/r] (u=use existing plan, r=redo from scratch):
```

`u` goes straight to the plan gate. Nothing is re-fetched or re-planned unless you ask. If the
scratchpad is gone, migite restores the files from the vault mirror first.

### A stricter commit gate for a team repo

```bash
migite config --edit        # in the repo; commit the file so the team shares it
```

```yaml
gates:
  commit:
    policy: strict
```

With `strict`, `y` is refused while the verdict is NEEDS FIXES, specs fail, or rubocop offenses
remain. Capital `Y` approves anyway and records the blockers in `gate-overrides.md` beside the
review, so the exception is on record.

## What you get

Everything lands in `scratchpad/bb-1234/` in the repo and is mirrored to the vault:

| File | Use it for |
|---|---|
| `plan.md`, `architecture-critic.md` | what was agreed before any code, and what the critic flagged |
| `testing-plan.md` | seed data, curl steps, and teardown for verifying the change by hand |
| `implementation.md` | the agent's notes on what it changed |
| `review.md`, `review.json` | the review and its machine-readable verdict |
| `pr-description.md` | the PR body, ready to paste |
| `usage.json` | calls, tokens, and cost for the run |

The full list is in [outputs.md](../outputs.md). Add `scratchpad/` to the repo's `.gitignore`.

## When something goes wrong

| Symptom | Look at |
|---|---|
| the ticket's content isn't in the plan | `migite-ticket sources` ([Read a ticket](./read-a-ticket.md)) |
| rubocop or rspec never run | [troubleshooting.md](../troubleshooting.md#troubleshooting-permissions) |
| the commit gate keeps saying NEEDS FIXES | [troubleshooting.md](../troubleshooting.md#troubleshooting-needs-fixes) |
| the plan explores the wrong part of the codebase | `n` at the plan gate, or add paths and names to the intake's context |

## See also

- [Amend a task](./amend-a-task.md): the next step when PR feedback arrives
- [phases.md](../phases.md): what each phase calls, and every gate key in detail
- [configuration.md](../configuration.md): models, effort, gates, permissions
