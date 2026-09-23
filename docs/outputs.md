# Outputs

Every file a `migite` run writes, where it lands, what the commit-gate banner is built from, and
why the testing plan is its own regenerated file. Phase behaviour is in [phases.md](./phases.md);
the vault's directory tree is in [vault-structure.md](./vault-structure.md); the JSON envelopes'
fields are in [internals.md](./internals.md#machine-readable).

## Contents

- [Output files](#output-files)
  - [Scratchpad](#scratchpad)
  - [Vault](#vault)
  - [Logs](#logs)
- [The commit gate banner](#commit-gate-banner)
- [Testing Plan requirement](#testing-plan-requirement)

---

<a id="output-files"></a>
## Output files

<a id="scratchpad"></a>
### Scratchpad (`<repo-root>/scratchpad/<ticket>/`) — source of truth for the run

Every phase reads and writes here directly. `sync_artifact()` (`lib/vault.sh`) mirrors
each file out to the vault immediately after every write, edit, refine, or redo, so the vault
copy never lags behind.

| File | Contents |
|------|----------|
| `intake.md` | Filled-in task intake |
| `task.md` | Optional — supplementary details added via [Intake mode](./migite.md#intake-mode)'s prompt and/or `--attach`, kept separate from `intake.md` |
| `jira-context.md` | Optional — fetched Jira ticket content when `--jira` is used and the fetch succeeds |
| `plan.md` | Implementation plan |
| `amendment-NN.md` | Scoped delta from each `--amend` run — original plan stays untouched |
| `testing-plan.md` | QA/dev verification steps — seed script, curls, teardown. Regenerated in full on every `--amend`, unlike `plan.md` |
| `architecture-critic.md` | Pre-implementation risk findings |
| `implementation.md` | Notes from the implementation session |
| `implementation-stage-N.md` | Per-layer notes, `--staged` mode only |
| `review.md` | Code review verdict and findings |
| `fix-r<N>.md` | Summary of what Claude changed during a commit-gate `f` fix pass |
| `pr-description.md` | Ready to paste into GitHub |
| `plan.json` | Machine-readable envelope beside `plan.md`: critic finding counts and clean flag, open-question count, plan headings, stub retries, refine status, failed explorers, per-tool usage. Derived deterministically from the documents, so it can't disagree with them |
| `review.json` | Machine-readable envelope beside `review.md`: `verdict` (`needs_fixes` / `ready`), reason, typed `findings[]`, per-severity `counts`, per-dimension counts, `source` (`structured` from a schema-validated call, or `markdown` fallback), usage. **This is what the commit gate reads**; deleted before every review run and when you hand-edit `review.md` at the gate |
| `usage.json` | End-of-run summary of every headless model call (by model and by tool: calls, tokens, time, cost). Interactive sessions are not metered |
| `gate-overrides.md` | Only with `gates.commit.policy: strict` — one entry per capital-`Y` approval over blockers, listing what was overridden |
| `.plan.done` | Sentinel written by migite-plan on success |
| `.review.done` | Sentinel written by migite-review on success |
| `.plan-history/` | Timestamped `plan.md` snapshots, one per edit/refine/redo |

If the scratchpad copy of any of the above is missing (cleaned, fresh clone, different
machine), `resume_from_vault()` pulls it back in from the vault mirror before the phase that
needs it runs — see [Resuming a run](./migite.md#resuming-a-run).

<a id="vault"></a>
### Vault (`~/dev-log/<org>/<repo>/<ticket>/`) — read-only mirror

A synced copy of every file above (same names, same paths under `$TASK_DIR`), meant for
reading/browsing later — e.g. in Obsidian — not for resuming or working from directly. Safe to
delete the scratchpad copy after merging; the vault mirror keeps the durable record.
`knowledge.md` (one file per repo, not per-ticket) and `docs/improvements.md` (in the migite
tool's own repo) are the exceptions — they live in the vault only, with no scratchpad copy.

See [Vault structure](./vault-structure.md) for the full directory tree.

<a id="logs"></a>
### Logs (`~/.dev-workflow/logs/`)

| File | Contents |
|------|----------|
| `<ts>-<ticket>-rubocop.txt` | Pre-review rubocop output |
| `<ts>-<ticket>-rubocop-final.txt` | Post-review rubocop output |
| `<ts>-<ticket>-rspec.txt` | Rspec output |
| `<ts>-<ticket>-heal-rubocop.txt` | Heal loop rubocop |
| `<ts>-<ticket>-heal-rspec.txt` | Heal loop rspec |
| `<ts>-<ticket>-heal-fix-N.txt` | Claude's heal output per attempt |
| `<ts>-<ticket>-critic.txt` | Architecture critic raw output |
| `<ts>-<ticket>-knowledge.txt` | Raw knowledge extraction |
| `<ts>-<ticket>-improvements.txt` | Raw self-improvement notes |
| `<ts>-prompt-<label>.txt` | Every prompt sent to interactive phases |
| `<ts>-wrapper-<label>.sh` | tmux wrapper scripts |
| `<ts>-usage.jsonl` | The run's usage ledger - one JSON line per headless agent call (tool, label, model, tokens, cost, duration, ok). Source for `usage.json` and the gate banner's running cost. Override the path with `MIGITE_USAGE_LEDGER` |

---

<a id="commit-gate-banner"></a>
## The commit gate banner

```
── Commit context ──────────────────────────
  ⚠ Ruby version error — bundle exec could not run. Fix .tool-versions before approving.
  Verdict: NEEDS FIXES
  Specs:   ⚠ 1 failure — check /path/to/rspec.txt before approving
  Rubocop: 3 offense(s) remain
────────────────────────────────────────────
```

| Signal | Source |
|--------|--------|
| Tooling error | `tooling_failed()` matched either log: `No version is set for command`, `Bundler::GitError` / `not yet checked out`, or `0 examples` alongside a DB connection or load error — the tool never ran, so all results below are untrustworthy. Re-evaluated on every commit-gate re-check |
| Verdict | Read from the `## Verdict` section of review.md by `review_verdict()` (`lib/gate.sh`) — `NEEDS FIXES`/`NEEDS CHANGES` → red, `READY TO COMMIT`/`READY TO MERGE`/`APPROVED` → green, anything else → "unknown". Anchored on the heading on purpose: the review format's `## Brakeman: PASS` line sits above the verdict, and a whole-file keyword grep used to match it first and show a green verdict on `NEEDS FIXES` reviews |
| Spec failures | Failure count, DB connection failure, load errors, `0 examples`, or `skipped` — "all passed" is only claimed when examples actually ran |
| Rubocop state | Offense count from the post-review re-run |
| Findings / Reason | From `review.json`: critical / warning / note counts and the one-line reason the verdict was decided. Only shown when the envelope exists (i.e. not after a hand-edit of `review.md`) |
| Cost | Running total of headless model calls from the usage ledger — interactive sessions aren't metered |

| Key | Action |
|-----|--------|
| `y` | Commit — continue to Phase 3.5, even if the banner above shows blockers. There's no confirmation step beyond the keypress itself |
| `f` | Claude fixes the findings in an interactive session, then checks + review re-run automatically |
| `e` | Open `review.md` in `$EDITOR` to read and annotate before deciding |
| `n` | Manual fix — pauses for you, then re-runs checks + review when you're ready |
| `q` | Abort |

---

<a id="testing-plan-requirement"></a>
## Testing Plan requirement

The QA/dev verification steps — seed script, curls or browser actions, teardown — live in their own `testing-plan.md`, not inside `plan.md`. This is deliberate: `plan.md` is history (amendments accumulate beside it, never overwriting it), but the testing plan describes how to verify the code **as it exists right now**. If it lived inside `plan.md`, every amendment that changed behaviour would leave it silently describing the pre-amendment version.

`migite-plan` writes `testing-plan.md` once during Phase 1, from the finished plan. Every `--amend` run regenerates it **in full** (not appended) from the current testing plan + the new amendment + the diff, so a step an amendment invalidates gets rewritten or dropped instead of lingering. `migite-review`'s `testing_plan` specialist reads this file directly (not `plan.md`) and returns `NEEDS FIXES` if it's missing, empty, or placeholder-only — and if the task has amendments, checks that the steps match current behaviour, not the original plan's.

Required shape:

````markdown
# Testing Plan

### Prerequisites — seed records (Rails console)
```ruby
district = District.find_by!(subdomain: "qa-district")
user = User.create!(email: "test.user@example.com", ...)
puts "Seeded: user=#{user.id}"
```

### Verification steps
1. Hit the endpoint: `curl -X POST https://localhost:3000/api/v1/... | jq`
2. Check the log: `grep "EventName" log/development.log | tail -5`

### Teardown
```ruby
User.find_by(email: "test.user@example.com")&.destroy
```
````

Only generic emails (`test.user@example.com`, `admin.qa@example.com`) — never real addresses.

One gap worth knowing: regeneration only happens on `--amend` (and on a full plan `n`-redo at the Phase 1 gate). The lightweight `f`/`e` plan-gate edits — feedback refine and direct `$EDITOR` edits, both pre-implementation — don't touch `testing-plan.md`, since nothing has been built yet for it to verify at that point.

---

