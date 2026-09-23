# Getting started

From nothing to a completed first run, with every command, every prompt, and every file it
produces. The [README quickstart](../README.md#quickstart) is the five-line version of this page.

## Contents

- [Install](#install)
  - [macOS](#macos)
  - [Linux](#linux)
  - [Python and asdf](#python)
- [Verify with `migite doctor`](#doctor)
- [Your personal config](#user-config)
- [First run, step by step](#first-run)
- [What it produced](#outputs)
- [Second run: amend after review feedback](#amend)
- [Where to go next](#next)

---

<a id="install"></a>
## Install

Migite is a git checkout plus symlinks on your `PATH`. Nothing is installed system-wide, and
uninstalling is deleting the symlinks and the checkout.

```bash
git clone <this-repo> ~/Code/migite
MIGITE_SRC="$HOME/Code/migite"
BIN="$HOME/.local/bin"
mkdir -p "$BIN"
for f in migite migite-plan migite-review \
         migite-blueprint migite-blueprint.py \
         migite-explore migite-explore.py \
         migite-audit migite-audit.py \
         migite-pr-review migite-pr-review.py migite-ticket \
         migite_paths.py migite_call.py migite_config.py migite_agent.py; do
  ln -sf "$MIGITE_SRC/$f" "$BIN/$f"
done
```

The module symlinks let you run `migite_config.py` and friends by name. The tools themselves
don't need them: Python resolves a symlinked script to its real directory, so `migite-plan` and
the others import `migite_call`, `migite_config`, and the `agents/` package from the checkout.
`migite.d/`, `prompts/`, and `agents/` are not symlinked; `migite` likewise resolves its real
location through the symlink and finds them beside itself.

<a id="macos"></a>
### macOS

```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc && source ~/.zshrc
pip3 install langgraph pyyaml
```

Notifications use `osascript` and need no setup. `readlink -f` requires macOS 12.3+; older
versions fall back to a manual symlink walk automatically.

<a id="linux"></a>
### Linux

```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc && source ~/.bashrc
python3 -m pip install --user langgraph pyyaml
sudo apt-get install -y libnotify-bin    # optional: desktop notifications via notify-send
```

Everything else is POSIX or bash 4. If `python3` is not 3.11+, install one and point
`MIGITE_PYTHON` at it (below).

<a id="python"></a>
### Python and asdf

Migite picks its Python in this order: `MIGITE_PYTHON` if set, then `python3` **if it actually
runs**, then a fallback under `~/.asdf`. The "actually runs" check matters with asdf: a shim is
always on `PATH` even when no version is selected for the current directory, and would fail with
`No version is set for command python3`. Migite detects that and falls through. To remove the
guesswork:

```bash
export MIGITE_PYTHON="$HOME/.asdf/installs/python/3.13.5/bin/python3"   # in ~/.zshrc or ~/.bashrc
"$MIGITE_PYTHON" -c "import langgraph, yaml; print('ok')"
```

<a id="doctor"></a>
## Verify with `migite doctor`

Run it inside any git repo. It is read-only and exits non-zero if it finds anything.

```text
$ cd ~/Code/your-rails-app && migite doctor

migite doctor

✔ Stack: rails — app dir: .
✔ Tool resolves: claude
✔ Tool resolves: git
✔ Tool resolves: /Users/you/.asdf/installs/python/3.13.5/bin/python3
✔ Tool resolves: bundle
✔ config valid (1 file(s): /Users/you/.config/migite/config.yml)
✔ Prompt present: prompts/plan.md
✔ Prompt present: prompts/implement.md
✔ Prompt present: prompts/review.md
✔ Prompt present: prompts/architecture_critic.md
✔ Scratchpad/vault sync: no drift found
✔ Sentinels: none orphaned
✔ Knowledge duplicates: none found
ℹ helpers.sh: 780 lines (split trigger: ~2,000)

0 issues found.
```

A `✘ Tool does not resolve: claude` means the Claude Code CLI is not on `PATH`; `✘ Config:` shows
the exact file and key that failed validation.

<a id="user-config"></a>
## Your personal config

```bash
migite config --edit --user      # writes the starter the first time, then opens it
```

The generated file has every setting at its default with a one-line comment. The usual first
edits, and the only ones most people make:

```yaml
vault:
  base: ~/Documents/MyVault/dev-log   # where plans, reviews, and knowledge.md are mirrored
ui:
  editor: nvim
models:
  effort:
    strong: xhigh                     # first thing to try on the planner, critic, and reviewers
```

Delete everything else. Then `migite config` shows the effective result with the source of each
value, and `migite doctor` validates the file. A repo can override any key with its own
`.migite.yml` (`migite config --edit` inside the repo). Details: [configuration.md](./configuration.md).

**If you use Jira**, install Atlassian's CLI so `--jira` plans from the real description and
acceptance criteria. It logs in through your browser, so there is no token to store, and it costs
no model call on any agent:

```bash
brew install atlassian/acli/acli     # Linux: see docs/tickets.md
acli jira auth login --web           # once; pick your site in the browser
migite-ticket sources                # → jira-acli would be used
```

Without it, Claude Code can still fetch tickets through its Atlassian MCP tools. Details:
[tickets.md](./tickets.md#setup).

---

<a id="first-run"></a>
## First run, step by step

Scenario: a Rails API, a Jira ticket `BB-1234` asking for a PDF export on invoices, a branch
`feature/BB-1234-pdf-export` already checked out. Text after `←` is commentary.

```bash
migite --jira BB-1234 --type feature
```

**Setup.** Migite anchors to the repo root, detects the stack and base branch, and loads config.

```text
▶ Jira mode: BB-1234
▶ Current branch: feature/BB-1234-pdf-export
▶ Stack: rails — app dir: .
▶ Config: /Users/you/.config/migite/config.yml
▶ Repo: Acme/invoices-api                        ← org = the directory containing the repo
▶ Base branch: main
```

**Phase 1, plan.** The ticket is fetched by `migite-ticket` (Atlassian's `acli`, or the agent's
Atlassian MCP tools as a fallback) and cached. A `feature` intake template opens in
your editor with the title and ticket pre-filled; fill in acceptance criteria and save.

```text
▶ Phase 1/4 — Planning
▶ Fetching Jira ticket BB-1234...
✔ Jira ticket fetched, added to planning context
▶ Created intake from template: feature
▶ Scratchpad: /Users/you/Code/invoices-api/scratchpad/bb-1234
▶ Vault mirror: /Users/you/Documents/MyVault/dev-log/Acme/invoices-api/bb-1234
  Starting LangGraph agent: Planning
  Log: /Users/you/.dev-workflow/logs/20260922-141502-plan-agent.log
  migite-plan | base branch: main
  migite-plan | Jira ticket context loaded (1842 chars)
  migite-plan | explore=claude-haiku-4-5-20251001  think=claude-opus-5-5  critic=claude-opus-5-5
  ▶ Fanning out 7 explorers in parallel (stack=rails)
    ◦ models
    ◦ controllers
    ◦ services
    ◦ serializers
    ◦ specs
    ◦ migrations_schema
    ◦ routes_config
  ▶ Synthesising plan from 7 exploration reports
  ▶ Architecture critic
  ▶ Refining plan with critic findings
  ▶ Generating testing plan
  ▶ Writing outputs
    ✔ plan.md → .../scratchpad/bb-1234/plan.md
    ✔ architecture-critic.md → .../scratchpad/bb-1234/architecture-critic.md
    ✔ testing-plan.md → .../scratchpad/bb-1234/testing-plan.md
    ✔ plan.json → .../scratchpad/bb-1234/plan.json
    ✔ sentinel written
  ✔ migite-plan complete
✔ Plan written to /Users/you/Code/invoices-api/scratchpad/bb-1234/plan.md
```

Inside tmux this runs in a split pane below you and hands focus back when done. Outside tmux it
runs inline.

**The plan gate.** The critic's findings are printed above the prompt.

```text
── Architecture critic ─────────────────────────────
- 🟡 **Warning** — `InvoiceSerializer#pdf_url` will be called per row on the index; eager-load the attachment or cache the URL
- 🟢 **Note** — the new `invoices/:id/pdf` route should sit under the existing `Api::V1` namespace
────────────────────────────────────────────────────

────────────────────────────────────────
  REVIEW GATE: plan
────────────────────────────────────────
Proceed with plan? [y/f/e/n/q] (y=approve, f=feedback refine, e=edit directly, n=full redo, q=abort):
```

| Key | What happens |
|-----|--------------|
| `y` | Approve and continue |
| `f` | Type one line of feedback; one model call revises the plan in place and shows a coloured diff |
| `e` | Open `plan.md` in your editor, no model call |
| `n` | Throw the plan away and re-run all 7 explorers, synthesis, and critic |
| `q` | Abort. Nothing is lost: re-running the same command resumes from `plan.md` |

Say `f`, type `use ActiveStorage instead of writing the PDF to /tmp`, review the diff, then `y`.

**Phase 1.5, optional TDD.**

```text
Phase 1.5 (TDD): Write spec files before implementation? [y/N]: n
```

**Phase 2, implement.** An interactive Claude Code session opens with the plan and your repo's
`knowledge.md` preloaded. You watch, steer, and type `/exit` when done.

```text
▶ Phase 2/4 — Implementing

  Starting interactive Claude Code session: Implementing
  Output file: .../scratchpad/bb-1234/implementation.md
  Permission mode: auto
  Type /exit when done to return here
```

**Phase 2.5, auto-heal.** Rubocop autocorrects with no model call; rspec runs on the changed spec
files, untracked ones included. Only real failures reach Claude, up to `heal.max_attempts` times.

```text
▶ Phase 2.5 — Running checks (auto-heal enabled, max 3 attempts)
⚠ Auto-heal attempt 1/3...
  Auto-healing: Auto-heal 1
▶ Re-running checks after heal attempt 1...
✔ Auto-heal resolved failures after 1 attempt(s)
```

**Phase 3, review.** An authoritative lint and test pass, then four reviewers in parallel and a
structured verdict.

```text
▶ Phase 3/4 — Reviewing
▶ Running rubocop on changed Ruby files...
✔ Rubocop clean
▶ Running specs on changed files...
  Starting LangGraph agent: Reviewing
  migite-review | correctness=claude-opus-5-5  security=claude-opus-5-5  test_coverage=claude-sonnet-5  testing_plan=claude-sonnet-5  verdict=claude-opus-5-5  base branch: main
  ▶ Loading inputs + git diff
  ▶ Fanning out 4 specialist reviewers in parallel
    ◦ correctness  (claude-opus-5-5)
    ◦ security  (claude-opus-5-5)
    ◦ test_coverage  (claude-sonnet-5)
    ◦ testing_plan  (claude-sonnet-5)
  ▶ Synthesising verdict from 4 reviews
    ✔ review.md → .../scratchpad/bb-1234/review.md
    ✔ review.json → .../scratchpad/bb-1234/review.json  (verdict=needs_fixes, 1🔴 1🟡 2🟢, source=structured)
```

**The commit gate.** The banner is built from `review.json`, the test logs, and the usage ledger.

```text
── Commit context ──────────────────────────
  Verdict: NEEDS FIXES
  Findings: 1 critical · 1 warnings · 2 notes
  Reason:  The 422 path for an invoice without line items is unhandled and untested.
  Specs:   all passed
  Rubocop: clean
  Cost:    17 calls, $2.94 so far (headless calls only)
────────────────────────────────────────────
Proceed? [y/f/e/n/q] (y=commit, f=Claude fixes, e=edit directly, n=fix it yourself, q=abort):
```

| Key | What happens |
|-----|--------------|
| `f` | An interactive session opens with the findings; on `/exit`, checks and the full review re-run and the gate reopens |
| `n` | You fix it yourself, press Enter, checks and review re-run |
| `e` | Open `review.md` to annotate or strike findings |
| `y` | Approve. With the default `lenient` policy this always works. With `strict`, it is refused while blockers remain and a capital `Y` overrides with a record in `gate-overrides.md` |

Say `f`, let Claude add the guard and the request spec, `/exit`, and the second banner reads
`READY TO COMMIT`, `0 critical`. Then `y`.

**Phases 3.5 to 4.5.** Knowledge capture first asks you a question, then extracts; the PR
description is an interactive session; the self-improvement pass is silent.

```text
▶ Phase 3.5/4 — Capturing knowledge
  Anything worth remembering from this run that migite might not catch? (optional, Enter to skip): invoices without line items are legal drafts, never an error
  Claude is thinking: Extracting knowledge
✔ Knowledge appended to /Users/you/Documents/MyVault/dev-log/Acme/invoices-api/knowledge.md

▶ Phase 4/4 — Generating PR description
  Starting interactive session: PR description

▶ Phase 4.5/4 — Capturing improvement notes
  Claude is thinking: Self-improvement
▶ No improvement notes for this run

✔ Workflow complete.

  Scratchpad: /Users/you/Code/invoices-api/scratchpad/bb-1234
    intake.md          → task intake
    plan.md            → planning output
    implementation.md  → implementation notes
    review.md          → review verdict
    pr-description.md  → ready to paste into GitHub
    plan.json / review.json / usage.json → machine-readable envelopes
  Vault (read-only mirror): /Users/you/Documents/MyVault/dev-log/Acme/invoices-api/bb-1234

── Usage ───────────────────────────────────────
  Model calls (headless only — interactive sessions not metered)

  model                              calls  in+cache tok  out tok    time     cost
  claude-opus-5-5                        9       377,406   14,210    338s    $3.62
  claude-haiku-4-5-20251001              7       165,438    2,710     41s    $0.33
  claude-sonnet-5                        7       142,880    6,015     92s    $0.61
  total                                 23       685,724   22,935    471s    $4.56
  cache-creation tokens: 23,624 (each headless call re-sends Claude Code's system context)
  Ledger: /Users/you/.dev-workflow/logs/20260922-141502-usage.jsonl
────────────────────────────────────────────────
```

Now commit. Migite never ran `git commit`; `pr-description.md` is ready to paste.

---

<a id="outputs"></a>
## What it produced

Everything lives in `<repo>/scratchpad/bb-1234/` and is mirrored to the vault after every write.
The scratchpad is the working copy; the vault is for reading later, in Obsidian or anywhere.

| File | What is in it |
|------|---------------|
| `intake.md` | The filled-in template. Reused, not re-opened, if you run the same ticket again |
| `jira-context.md` | The fetched ticket. Cached so redos do not refetch |
| `plan.md` | Summary, scope grouped by layer, approach, test plan, risks, out of scope, open questions |
| `architecture-critic.md` | The critic's findings, printed at the gate |
| `testing-plan.md` | Seed script, verification steps with curl commands, teardown. Regenerated in full on every amendment and fix round |
| `implementation.md` | Notes Claude wrote at the end of the implement session |
| `review.md` | The review document, verdict-first |
| `fix-r1.md` | What Claude changed in the first commit-gate fix round |
| `pr-description.md` | Filled PR template |
| `plan.json`, `review.json`, `usage.json` | The machine-readable envelopes, see below |
| `.plan-history/` | A snapshot of `plan.md` before every refine, edit, or redo |

Two files are per repo, not per task: `knowledge.md` in the vault, injected into every future
plan, and `migite-improvements.md` in the migite checkout.

`review.json`, the file the commit gate reads:

```json
{
  "schema_version": 1,
  "tool": "migite-review",
  "generated_at": "2026-09-22T14:41:07+00:00",
  "base_branch": "main",
  "verdict": "needs_fixes",
  "verdict_label": "NEEDS FIXES",
  "reason": "The 422 path for an invoice without line items is unhandled and untested.",
  "counts": { "critical": 1, "warning": 1, "note": 2 },
  "findings": [
    { "severity": "critical", "dimension": "correctness", "file": "app/services/invoices/pdf_exporter.rb", "line": 24,
      "problem": "`line_items.sum` raises on an invoice with no line items", "fix": "guard with `return Result.failure(:empty)` and add a request spec for 422" },
    { "severity": "warning", "dimension": "test_coverage", "file": "spec/requests/api/v1/invoices/pdf_spec.rb", "line": null,
      "problem": "no 401 example", "fix": "add an unauthenticated request example" }
  ],
  "dimensions": {
    "correctness":  { "critical": 1, "warning": 0, "note": 1, "failed": false },
    "security":     { "critical": 0, "warning": 0, "note": 1, "failed": false },
    "test_coverage":{ "critical": 0, "warning": 1, "note": 0, "failed": false },
    "testing_plan": { "critical": 0, "warning": 0, "note": 0, "failed": false }
  },
  "source": "structured",
  "usage": { "calls": 5, "cost_usd": 1.21, "input_tokens": 61, "output_tokens": 7902,
             "cache_read_input_tokens": 94496, "cache_creation_input_tokens": 23624, "duration_ms": 184300 }
}
```

<a id="amend"></a>
## Second run: amend after review feedback

A reviewer on the PR asks for the export to be idempotent on retry. Do not start a new task; amend
the existing one. Migite finds the task from the ticket key in the branch name.

```bash
migite --amend "the export must be idempotent on retry — a second call returns the existing PDF"
```

```text
▶ Amend mode — locating task to amend
▶ Amend target from branch name: bb-1234
▶ Amending: /Users/you/Documents/MyVault/dev-log/Acme/invoices-api/bb-1234
  Claude is thinking: Generating amendment 01
✔ Amendment written to .../scratchpad/bb-1234/amendment-01.md
────────────────────────────────────────
  REVIEW GATE: amendment 01
────────────────────────────────────────
Proceed with amendment? [y/f/e/q] (y=approve, f=feedback refine, e=edit directly, q=abort): y
▶ Updating testing plan for amendment 01...
✔ Amendment approved — continuing to implementation
```

One model call scoped a delta against the built code instead of ten calls re-planning from
scratch. `plan.md` is untouched; `amendment-01.md` sits beside it; `testing-plan.md` is
regenerated in full; then Phases 2 to 4 run as before and the PR description absorbs the
amendment.

<a id="next"></a>
## Where to go next

- Try `gates.commit.policy: strict` in a team repo's `.migite.yml`, and `models.effort.strong: xhigh` in your user config. Compare `usage.json` and the gate outcomes across a few runs.
- Feed a whole initiative through `migite-explore "…" --intakes`, then run each intake with `migite --intake`.
- Read `knowledge.md` after a few runs; it is the memory the planner starts from every time.
- [docs/migite.md](./migite.md) for every mode and gate, [docs/configuration.md](./configuration.md) for every key.
