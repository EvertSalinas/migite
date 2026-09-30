# Run manifest and resume

Status: planned (implementation not started). Companion roadmap item: non-interactive
`--yes` mode — designed here, to land separately (see "Non-goals").

## Why

Today a migite run only "resumes" informally: `resume_from_vault()` recovers artifacts and
`run_plan` reuses an existing intake/plan, but re-running a command restarts the phase
sequence at the top (`bin/migite`) and redoes implement/review/deliver. There is no record
of *which phase a run reached* or *what was already decided*. A `run.json` written at every
phase boundary makes the run's position explicit, so a run can continue from a recorded
state — by re-running the same command, or explicitly with `--resume`.

## Design

### `run.json` (schema_version 1)

Lives at `scratchpad/<slug>/run.json`, mirrored to the vault `$TASK_DIR/run.json` via
`sync_json` (same convention as `plan.json` / `review.json` / `usage.json`). Envelope
follows `gateway.envelope_base` (`schema_version`, `generated_at`).

```json
{
  "schema_version": 1, "tool": "migite-run", "generated_at": "...", "updated_at": "...",
  "status": "in_progress | complete | failed",
  "next_phase": "plan | tdd | implement | heal | review | deliver | done",
  "args":  { "task", "task_type", "jira_ticket", "jira_url", "intake", "audit",
             "blueprint", "attach": [], "staged", "stack",
             "amend": { "feedback", "file", "num" } },
  "repo":  { "org", "name", "root", "branch", "base_branch" },
  "layout":{ "slug", "task_dir", "scratchpad_dir", "log_dir", "usage_ledger", "timestamp" },
  "phases": {
    "plan":      { "status": "...", "gate_attempts": 0 },
    "tdd":       { "status": "...", "decided": true|false|null },
    "implement": { "status": "...",
                   "stage_num": 0, "stage_count": 0, "stage_labels": [] },
    "heal":      { "status": "...", "heal_attempt": 0 },
    "review":    { "status": "...", "gate_attempts": 0 },
    "deliver":   { "status": "..." }
  }
}
```

- Phase statuses: `pending | running | pending_gate | done | skipped | failed`. Every
  phase entry also carries `started_at` / `completed_at` (omitted above). `running` is
  written when a phase begins, so a crash leaves the interrupted phase identifiable;
  `pending_gate` means the phase's work finished but its gate was not approved.
- `heal` is its own phase: `run_auto_heal_loop` is split out of `run_implement` (see
  Implementation step 3) so an interruption during healing does not replay the
  interactive implement session.
- File paths are **re-derived** from `layout.slug` + config on restore via
  `set_run_paths` (Implementation step 2), not stored one-by-one. Artifacts return
  through the existing `resume_from_vault` pattern.
- `run.json` resumes the **workflow position**, not git state: the worktree is assumed
  unchanged between runs (documented caveat).
- `phases.*.gate_attempts` and `tdd.decided` are the state a future `--yes` mode will
  consume; recording them now keeps one state model for both features.

### Write points ("every phase boundary")

| Event | `run.json` update |
|---|---|
| run start (fresh) | `status: in_progress`, all phases pending |
| any phase begins | that phase `status: running`, `started_at` |
| plan gate approved | `phases.plan.status: done`, `next_phase: tdd` |
| TDD prompt answered | `phases.tdd.decided`, `status: done\|skipped` |
| each staged checkpoint | `phases.implement.stage_num` |
| implement finished | `phases.implement.status: done`, `next_phase: heal` |
| heal loop finished | `phases.heal.status: done`, `heal_attempt` |
| commit gate approved | `phases.review.status: done` |
| deliver finished | `phases.deliver.status: done`, `status: complete` |
| any `q` gate abort | that phase `status: pending_gate` (gate re-opens on resume) |

Every "done" transition also stamps `completed_at`; every write bumps `updated_at`.

## Implementation

1. **`migite/runstate.py`** (new) — schema constants and a small CLI: `init`,
   `update --set dotted.key=value` (merge-rewrite), `export --shell` (prints
   `KEY='value'` for restore — same eval pattern as `ticket_cmd parse --shell`),
   `field`. Stdlib only; reads reuse `migite.gateway` JSON helpers. `update` stamps
   `started_at` / `completed_at` on phase status transitions and bumps `updated_at`.
   **Writes are atomic** (write `run.json.tmp` in the same dir, then `os.replace`): a
   crash mid-write must never leave a truncated manifest, or resume would fail on the
   very runs it exists to recover.

2. **`lib/manifest.sh`** (new) — `manifest_init`, `manifest_boundary <phase> <status>`,
   `manifest_set`, `manifest_find` (discovery), `manifest_restore`,
   `manifest_restore_artifacts` (the `resume_from_vault` set from
   `lib/phases/plan.sh` / `lib/phases/amend.sh`). Every write runs `sync_json` to the
   vault mirror (never `sync_artifact` — frontmatter would corrupt the JSON). Sourced
   by `bin/migite` **and `tests/run.sh`** so it is unit-testable in the existing harness.
   Restore calls **`set_run_paths <run-slug>`**, which already exists in `lib/vault.sh`
   and which `run_plan` and amend mode use, so the normal plan path, amend mode, and resume
   derive every path identically. The manifest records the run slug (`00-build`,
   `NN-amend-<slug>`) alongside `layout.slug`; see [Run folders](./vault-structure.md#run-folders).

3. **Phase hooks** — call `manifest_boundary` at the write points above in
   `lib/phases/{plan,implement,review,deliver,amend}.sh`; record `PLAN_GATE_ATTEMPTS`,
   `COMMIT_GATE_ATTEMPTS`, `HEAL_ATTEMPT`, and the TDD answer. Promote
   `STAGE_NUM`/`STAGE_COUNT`/`STAGE_LABELS` from `local` (`lib/phases/implement.sh`)
   to globals so staged runs resume mid-stage (stage notes
   `implementation-stage-N.md` already persist). Remove the internal
   `run_auto_heal_loop` call from `run_implement` so `bin/migite` sequences `heal` as
   its own skippable phase.

4. **`bin/migite` dispatch + `--resume [file]`** — replaces the unconditional
   `run_plan → run_tdd → run_implement → run_review → run_deliver` sequence (which
   becomes `plan → tdd → implement → heal → review → deliver`):
   - **Discovery:** `migite --resume [path]` (explicit), else implicit scan of
     `scratchpad/*/run.json` + vault for a manifest whose `args` match this invocation
     (jira key, task text, or intake file), newest wins; fall back to the slug-derived
     path. The scan closes today's gap where a Title-renamed slug breaks
     "re-run the same command".
   - **Restore:** set globals from `run.json`, verify same repo, check out/verify
     `repo.branch`, pull artifacts from the vault.
   - **Skip completed phases** (implicit on a plain re-run too): `plan done` → skip
     `run_plan`; `pending_gate` → re-enter the gate; `tdd decided` → no re-prompt;
     staged `implement` resumes at `stage_num`; `implement done` → start at `heal`;
     `review done` → skip to deliver. A phase left `running` or `failed` re-runs.
   - **Completed run:** `status: complete` → print the artifact summary, exit 0,
     point at `migite --amend` for follow-ups.
   - `--resume` added to `migite_usage` (help/flag parity is enforced by
     `tests/cli_test.sh`).

## Edge cases

- **Crash inside a phase** leaves it `running`; resume re-runs only that phase.
  `error()` exiting under `set -e` is what leaves the marker behind, on purpose.
- **Crash before the manifest exists** (or a legacy run with none) → today's behaviour,
  including the `EXISTING PLAN FOUND [u/r]` prompt.
- **Mid-plan resume:** `plan` not done → `run_plan` runs and may re-derive the slug;
  the manifest moves with the renamed scratchpad dir and is refreshed afterwards.
- **`deliver` is one boundary:** an interruption between the PR description and the
  improvement notes re-runs Phase 4 (interactive) on resume. Acceptable until `--yes`
  makes Phase 4 headless.
- **`--amend`** keeps working; the amendment number is stamped into `args.amend`, but
  amend mode still regenerates its amendment document on each invocation.
- **Usage ledger:** `layout.usage_ledger` is restored so the cost total continues across
  the interruption.
- **No new config keys.** `run.json` is always on; a `--no-manifest` escape hatch is a
  possible follow-up. `scratchpad/` is already git-ignored, so no tracked-file churn.

## Tests

- `tests/manifest_test.sh` (new) — init/boundary/restore round-trip, discovery
  matching, `--shell` restore, `set_run_paths` derivation, vault mirror via `sync_json`.
- `tests/test_runstate.py` (new) — merge semantics, schema, shell export, phase
  timestamps, atomic write (no partial file on a simulated crash), malformed input.
- `tests/resume_test.sh` (new, end-to-end with `tests/fake-claude` and piped gate
  answers) — run 1 answers `q` at the plan gate → `run.json` records `pending_gate`;
  run 2 (same command, no `--resume`) re-opens the gate; a completed plan skips to
  implement; a finished run exits 0 with the summary.
- `tests/cli_test.sh` extended for the `--resume` flag.
- CI stays green (`.github/workflows/ci.yml`): `bash tests/run.sh`,
  `python -m unittest discover -s tests -p 'test_*.py'`, `shellcheck -S error`.

## Docs touched

`docs/outputs.md` (run.json schema), `docs/migite.md` + `docs/phases.md` (dispatch,
resume matrix, implement/heal split), `docs/internals.md` (`migite/runstate.py`,
`lib/manifest.sh`), `docs/vault-structure.md` (`run.json`), `docs/glossary.md`
("run manifest"), `docs/faq.md` (resume entry), the "re-running resumes from plan.md"
wording in `docs/getting-started.md` and `docs/workflows/build-a-task.md`, and the
README roadmap (manifest/resume half done; `--yes` split into its own item).

## Non-goals — `--yes` non-interactive mode (next iteration)

Designed now so the manifest schema fits; not implemented in this change:

- `--yes` flag plus `ui.non_interactive` config (→ `MIGITE_CFG_UI_NON_INTERACTIVE`).
- Gate auto-answers: plan `y`, existing-plan `u`, intake `y`, TDD `N`, stage checkpoint
  `c`, amendment `y`, knowledge prompt empty.
- **Commit gate:** auto-approve only a clean verdict; when `_commit_gate_blockers` is
  non-empty (needs_fixes verdict, failed specs, dirty lint), **fail the run with a
  non-zero exit** listing the blockers — never silently take the `Y` override.
- **Implement in CI:** route `run_phase` through a headless `agent_ask` with the
  implement prompt; the reply becomes `implementation.md` (one-shot, no interactive
  refinement).

## Behavioral change to note

A plain re-run no longer redoes completed implement/review/deliver phases — it continues
from the recorded position. To force a fresh run of an old task: delete
`scratchpad/<slug>/run.json` (or use `migite --amend`).
