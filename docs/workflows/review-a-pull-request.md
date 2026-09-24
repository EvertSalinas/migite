# Review a pull request

Review a teammate's branch the way migite reviews its own work: the branch's diff against its base,
rubocop and rspec on the changed files, four reviewers in parallel (correctness, security, test
coverage, conventions and migrations), and a verdict. Every finding comes with a fix concrete
enough to apply, and alternatives when there is more than one sensible way. Read-only: it never
changes code or posts anything.

```text
branch
   │
   ├─ diff ────────── git diff base...branch (only the branch's own commits)
   ├─ checks ──────── rubocop and rspec on the changed files (skippable)
   ├─ 4 reviewers ─── correctness · security · test coverage · conventions and migrations
   ├─ synthesis ───── numbered findings, each with Problem, Fix, Alternatives
   └─ verdict ─────── APPROVED · APPROVED WITH COMMENTS · NEEDS CHANGES
```

## Walkthrough: a teammate's PR

### 1. Check out the branch

The review reads file contents and runs the specs from your working tree, so check the branch out
rather than pointing at a remote ref:

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
model calls.

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

### 4. Turn it into PR comments

Nothing is posted for you. Copy the findings you agree with into the PR, one comment per finding on
its line, fix included. Drop the ones you don't agree with; the review is input to your judgement,
not a replacement for it. Then switch back to your own branch.

## Variants

### A different base branch

```bash
migite-pr-review --branch feature/BB-1250-soft-delete --base develop
```

When the PR targets something other than the detected default.

### Skip rubocop and rspec

```bash
migite-pr-review --branch feature/big-refactor --skip-tests
```

When the suite needs services you don't have locally, or is too slow to run for a review. The
reviewers then work from the diff alone.

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

## See also

- [standalone-tools.md](../standalone-tools.md#migite-pr-review): every option and limit
- [Amend a task](./amend-a-task.md): when the review is of your own migite task
