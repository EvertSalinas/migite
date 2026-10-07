# Review a pull request

Review a teammate's branch the way migite reviews its own work: the branch's diff against its base,
rubocop and rspec on the changed files, four reviewers in parallel (correctness, security, test
coverage, conventions and migrations), a second agent that tries to disprove each Critical, and a
verdict. Every finding comes with a fix concrete enough to apply, and alternatives when there is
more than one sensible way. Read-only: it never changes code or posts anything.

```text
branch
   │
   ├─ diff ────────── git diff base...branch (only the branch's own commits)
   ├─ checks ──────── rubocop and rspec on the changed files (skippable)
   ├─ 4 reviewers ─── correctness · security · test coverage · conventions and migrations
   ├─ verification ── a second agent tries to disprove each Critical; wrong ones are dropped
   ├─ synthesis ───── numbered findings, each with Problem, Evidence, Fix, Alternatives
   └─ verdict ─────── APPROVED · APPROVED WITH COMMENTS · NEEDS CHANGES
```

## Walkthrough: a teammate's PR

### 1. Check out the branch

The review reads file contents and runs the specs from your working tree, and the reviewers and the
refuter read files from it too, so check the branch out rather than pointing at a remote ref. migite
warns when the working tree is on a different branch than the one you asked for:

```bash
cd ~/Code/Acme/invoices-api
git fetch origin
git switch feature/BB-1250-soft-delete
```

### 2. Review it

```bash
migite-pr-review --branch feature/BB-1250-soft-delete
```

```text
  ▶ Loading PR: feature/BB-1250-soft-delete..main
  …
  ▶ Synthesising verdict
  ▶ Writing review
    ✔ …/dev-log/Acme/invoices-api/pr-review-feature-BB-1250-soft-delete-2026-09-24.md

  Verdict: NEEDS CHANGES
```

The base is detected (`origin/HEAD`, then `main`, `master`, `develop`). A few minutes, about five
model calls, plus one per Critical for verification (see below).

### 3. Read the findings

Findings are numbered across severities, so you can refer to them in comments:

```markdown
### Critical
#### 🔴 1. Any user can delete any item · `app/controllers/items_controller.rb:12`
**Problem:** `destroy` looks the item up by id alone, so a signed-in user can delete another user's item.
**Fix:** scope the lookup to the current user, which also turns a foreign id into a 404:
    @item = current_user.items.find(params[:id])
**Alternative:** an `ItemPolicy#destroy?` check, if the app already authorizes with Pundit elsewhere.

### Notes
#### 🟢 2. Unexplained retention period · `app/models/item.rb:4`
**Problem:** `30.days` has no name, and the same value appears in the purge job.
**Fix:** extract `RETENTION_PERIOD = 30.days` on `Item` and use it in both places.
```

### 4. Check what verification did

Reviewers must quote the line each Critical and Warning rests on (`**Evidence:**`), and migite
confirms the quote is really at that place in the branch. Then a second agent, with read-only
tools and no sight of the reviewer's reasoning or fix, tries to disprove every Critical (and every
Warning whose evidence did not check out). A finding that survives carries its proof:

```markdown
#### 🔴 1. Any user can delete any item · `app/controllers/items_controller.rb:12`
**Problem:** `destroy` looks the item up by id alone, so a signed-in user can delete another user's item.
**Evidence:** `app/controllers/items_controller.rb:12` `@item = Item.find(params[:id])`
**Verified:** `app/controllers/items_controller.rb:12` `@item = Item.find(params[:id])` (no scope, no policy on this action)
```

A finding the second agent could not confirm is demoted to a Note with a `**Verification:**` line.
One it **disproved is removed**, and listed at the end of the review so you can audit the call:

```markdown
## Refuted by verification
A second agent traced each finding below to the code and found it false, so it is not in the findings above. …
- **Wrong base controller** (was Critical, `app/controllers/messages/insights_controller.rb:7`, correctness): the constant
  resolves lexically to the namespace's own base class, which already provides `authorize`. Evidence: …
```

Read that section: a finding that is dropped is not re-raised by any later step, so if you
disagree with one, the review is wrong and you should keep the finding. If the second agent itself
fails, the finding stays as reported, marked `not checked`. Details and cost:
[phases.md](../phases.md#phase-3-review). To measure how often it errs:
[Calibrate the refuter](./calibrate-the-refuter.md).

### 5. Turn it into PR comments

Nothing is posted for you. Copy the findings you agree with into the PR, one comment per finding on
its line, fix included. Drop the ones you don't agree with; the review is input to your judgement,
not a replacement for it. Then switch back to your own branch.

## Variants

### A different base branch

```bash
migite-pr-review --branch feature/BB-1250-soft-delete --base develop
```

When the PR targets something other than the detected default.

### Skip the lint and tests

```bash
migite-pr-review --branch feature/big-refactor --skip-tests
```

When the suite needs services you don't have locally, or is too slow to run for a review. The
reviewers then work from the diff alone.

On a [stack profile](../configuration.md#stacks) the lint and tests are the profile's own commands
(rubocop and rspec are rails only), and the reviewers check what the stack's
[checklist](../configuration.md#checklists) says.

### File it under the ticket

```bash
migite-pr-review --branch feature/BB-1250-soft-delete --jira BB-1250
migite-pr-review --branch feature/BB-1250-soft-delete --jira https://acme.atlassian.net/browse/BB-1250
```

The review lands in the ticket's vault folder, created if needed, and the key is given to the
reviewers as context. Without `--jira`, a ticket key in the branch name is used when that ticket's
folder already exists.

### Review your own branch before opening the PR

```bash
migite-pr-review --branch "$(git branch --show-current)"
```

A second opinion on work you did without migite, or a last check on work you did with it.

### Write it to a file

```bash
migite-pr-review --branch feature/BB-1250-soft-delete --output ./review.md
```

`--output` always wins over the vault location.

## What you get

`pr-review-<branch or date>.md` in the vault (or `--output`): a summary, the numbered findings with
fixes and alternatives, and the verdict. Where it lands:

| Given | Written to |
|---|---|
| `--output <file>` | that file |
| `--jira <key>` | `<vault>/<org>/<repo>/<key>/pr-review-<branch>-<timestamp>.md` |
| a key in the branch name, with an existing ticket folder | `<vault>/<org>/<repo>/<key>/pr-review-<date>.md` |
| neither | `<vault>/<org>/<repo>/pr-review-<branch>-<date>.md` |

## When something goes wrong

| Symptom | Fix |
|---|---|
| `No diff found between 'main' and '...'` | the branch isn't fetched, or is already merged; `git fetch origin` and check `git log main..<branch>` |
| findings reference code that isn't on the branch | you ran it from another checkout; `git switch` to the branch first |
| a finding has no fix | rare: the review retries once when that happens; treat the finding as a question for the author |
| `⚠ the working tree is on 'x', not 'y'` | the reviewers and the refuter read the working tree: `git switch` to the branch and re-run, or the evidence checks will mislead |
| a Critical you believe is real is under "Refuted by verification" | trust your own read of the code: keep the finding, and add the case to your [golden set](./calibrate-the-refuter.md) as a **true** finding |
| a Critical you believe is wrong is still a Critical, with a `Verified:` line | read the refuter's evidence line; if it is wrong too, add it as a **false** finding to the golden set |

## See also

- [standalone-tools.md](../standalone-tools.md#migite-pr-review): every option and limit
- [Amend a task](./amend-a-task.md): when the review is of your own migite task
- [Calibrate the refuter](./calibrate-the-refuter.md): measure how often the verification step is wrong
