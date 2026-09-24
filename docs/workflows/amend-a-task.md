# Amend a task

For feedback that arrives after you built something: PR review comments, a QA bug, a scope change
from the product owner. Instead of a fresh run that re-plans from scratch in a new folder, an
amendment is one model call that reads the plan, the review, and the actual diff, and scopes only
the delta. It then goes through implement, heal, review, and the commit gate like any task.

```text
your feedback
   │
   ├─ find the task ──── --jira, else the ticket key in the branch, else a picker
   ├─ one call ───────── plan + implementation + review + git diff + feedback → amendment-NN.md
   ├─ AMENDMENT GATE ─── approve, refine, or edit
   ├─ testing plan ───── regenerated in full for the new behaviour
   └─ implement → auto-heal → review → COMMIT GATE → knowledge → PR description
```

**Use it when** the task was already built with migite. For a brand-new request, even a small one,
use [Build a task](./build-a-task.md).

## Walkthrough: a reviewer comment on the PR

You built `BB-1234` on `feature/BB-1234-pdf-export`. A reviewer writes: "a retry must not generate a
second PDF." Stay on the branch and run:

```bash
migite --amend "the export must be idempotent on retry: a second call returns the existing PDF"
```

```text
▶ Amend mode — locating task to amend
▶ Amend target from branch name: bb-1234                 ← found from the branch, no flag needed
▶ Amending: …/dev-log/Acme/invoices-api/bb-1234
  Claude Code is working: Generating amendment 01
✔ Amendment written to …/scratchpad/bb-1234/amendment-01.md
────────────────────────────────────────
  REVIEW GATE: amendment 01
────────────────────────────────────────
Proceed with amendment? [y/f/e/q] (y=approve, f=feedback refine, e=edit directly, q=abort):
```

Read `amendment-01.md`. It has a Scope (what changes), an Out of scope (what must not be touched),
and a Rationale grounded in the current code. Then:

| The amendment is... | Press |
|---|---|
| right | `y`: the testing plan is regenerated and implementation starts |
| off in one respect | `f`: type a line of feedback, one call revises it |
| right except for a detail | `e`: edit it directly |

From here it is the normal flow: an implementation session scoped to the amendment (the original
plan is marked as already built), auto-heal, review, the commit gate, and a PR description that now
covers the original work and the amendment together.

```bash
git add -A && git commit -m "fix(invoices): make PDF export idempotent on retry"
```

Each amendment is naturally its own commit.

## Variants

### Feedback from a file

```bash
migite --amend-file ./qa-notes.md
```

For anything longer than a sentence: QA reproduction steps, a list of review comments.

### Pasted PR comments

With the GitHub CLI, save the review comments to a file and amend from it:

```bash
gh pr view --comments > /tmp/pr-feedback.md
migite --amend-file /tmp/pr-feedback.md
```

Trim the file first if it contains comments that are already resolved; the amendment treats
everything in it as requested.

### Type the feedback in your editor

```bash
migite --amend
```

With no feedback given, your editor opens on an empty file.

### Name the task explicitly

```bash
migite --amend "fix the retry path" --jira BB-1234
```

Needed when you are not on the task's branch, or the branch name has no ticket key.

### Pick from recent tasks

When there is no `--jira` and no key in the branch name, migite lists the ten most recently changed
tasks for this repo and asks which one to amend.

### Several rounds of feedback

Run `--amend` again for each round. Amendments accumulate as `amendment-01.md`, `amendment-02.md`,
... beside the untouched `plan.md`, so the record of what changed and why stays with the work.

### Feedback the code already satisfies

If the current code already does what the feedback asks, the amendment says so and leaves Scope
empty rather than inventing work. Answer `q`, reply to the reviewer, and move on.

## What you get

| File | Contents |
|---|---|
| `amendment-NN.md` | the scoped delta: Scope, Out of scope, Rationale |
| `testing-plan.md` | rewritten in full for current behaviour (unlike `plan.md`, which never changes) |
| `review.md`, `review.json` | the new review of the whole diff |
| `pr-description.md` | regenerated, including every amendment |

## When something goes wrong

| Symptom | Fix |
|---|---|
| migite can't find the task, or picks the wrong one | pass `--jira BB-1234`, or pick from the list |
| the scratchpad was deleted | nothing to do: migite restores the plan, review, and notes from the vault mirror |
| the amendment re-plans the whole feature | the feedback was too broad; `e` to cut Scope down, or split the feedback into two amendments |

## See also

- [migite.md, amend mode](../migite.md#amend-mode): how the task is found, and what is skipped
- [Review a pull request](./review-a-pull-request.md): to produce the feedback in the first place
