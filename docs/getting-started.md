# Getting started

From nothing to a completed first run, with every command, every prompt, and every file it
produces. The [README quickstart](../README.md#quickstart) is the five-line version of this page.

## Contents

- [Install](#install)
  - [1. Clone and link](#clone)
  - [2. Platform prerequisites](#platforms): [macOS](#macos) · [Ubuntu](#ubuntu) · [Debian](#debian) · [Fedora and RHEL](#fedora) · [Arch, Manjaro, Omarchy](#arch)
  - [3. Python dependencies in a venv](#python)
  - [Version managers: asdf, mise, pyenv, uv](#version-managers)
- [Verify with `migite doctor`](#doctor)
- [Your personal config](#user-config)
- [First run, step by step](#first-run)
- [What it produced](#outputs)
- [Second run: amend after review feedback](#amend)
- [Where to go next](#next)

---

<a id="install"></a>
## Install

Migite is a git checkout, symlinks on your `PATH`, and a Python virtual environment inside the
checkout. Nothing is installed system-wide, and uninstalling is deleting the symlinks and the
checkout. Three steps, the same on every platform; only step 2 differs per OS.

<a id="clone"></a>
### 1. Clone and link

```bash
git clone <this-repo> ~/Code/migite
MIGITE_SRC="$HOME/Code/migite"
BIN="$HOME/.local/bin"
mkdir -p "$BIN"
for f in "$MIGITE_SRC"/bin/*; do ln -sf "$f" "$BIN/$(basename "$f")"; done
```

Only `bin/` goes on your `PATH`. Every command there resolves its real location through the
symlink, sources `lib/`, and puts the checkout on `PYTHONPATH`, so the Python package (`migite/`)
and the LangGraph tools (`python -m migite.tools.<name>`) need no symlink and no install. A
`git pull` in the checkout updates every command at once.

Then make sure `~/.local/bin` is on your `PATH`. Many Linux distributions already add it; adding
it again is harmless. Use the file your shell reads: `~/.zshrc` for zsh (the macOS default),
`~/.bashrc` for bash (the default on most Linux distributions).

```bash
RC=~/.zshrc   # or ~/.bashrc
echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$RC" && source "$RC"
```

<a id="platforms"></a>
### 2. Platform prerequisites

What each platform needs: `git`, a **Python 3.11+** that can create virtual environments, and
optionally a notifier. Pick your OS, run its block, then continue to [step 3](#python).

| Platform | Python it ships | Enough? | Notifications |
|---|---|---|---|
| macOS | 3.9 (Command Line Tools) | No, use Homebrew | `osascript`, built in |
| Ubuntu 24.04+ | 3.12+ | Yes | `libnotify-bin` |
| Ubuntu 22.04 | 3.10 | No, use deadsnakes or a version manager | `libnotify-bin` |
| Debian 12 / 13 | 3.11 / 3.13 | Yes | `libnotify-bin` |
| Debian 11 | 3.9 | No, use a version manager | `libnotify-bin` |
| Fedora 39+ | 3.12+ | Yes | `libnotify` |
| RHEL / Rocky / Alma 9 | 3.9 | No, install `python3.11` | `libnotify` |
| Arch, Manjaro, Omarchy | latest | Yes | `libnotify` |

<a id="macos"></a>
#### macOS

The `/usr/bin/python3` Apple ships is 3.9, too old for migite. Install a current one with
[Homebrew](https://brew.sh):

```bash
xcode-select --install          # git; skip if already installed
brew install python@3.13
PY=python3.13
```

Notifications use `osascript` and need no setup. Symlinks are resolved with a plain `readlink`
walk, so no GNU coreutils are needed.

<a id="ubuntu"></a>
#### Ubuntu

Ubuntu splits the `venv` module into its own package; without it, step 3 fails with
`ensurepip is not available`.

```bash
# 24.04 and newer
sudo apt update
sudo apt install -y git python3 python3-venv libnotify-bin
PY=python3
```

Ubuntu 22.04 ships 3.10. Install 3.12 alongside it from the deadsnakes PPA (or use a
[version manager](#version-managers)) and leave the system `python3` alone:

```bash
# 22.04
sudo add-apt-repository -y ppa:deadsnakes/ppa
sudo apt install -y git python3.12 python3.12-venv libnotify-bin
PY=python3.12
```

<a id="debian"></a>
#### Debian

```bash
# 12 (bookworm) and 13 (trixie)
sudo apt update
sudo apt install -y git python3 python3-venv libnotify-bin
PY=python3
```

Debian 11 ships 3.9; use a [version manager](#version-managers) for a 3.11+ Python.

<a id="fedora"></a>
#### Fedora and RHEL

Fedora's `python3` includes `venv`. RHEL 9 and its rebuilds ship 3.9 as `python3` but package
newer versions side by side:

```bash
# Fedora
sudo dnf install -y git python3 libnotify
PY=python3

# RHEL / Rocky / Alma 9
sudo dnf install -y git python3.11 libnotify
PY=python3.11
```

<a id="arch"></a>
#### Arch, Manjaro, Omarchy

Arch's `python` package is always current and includes `venv`. Omarchy already has Python and a
notification daemon, so there is usually nothing to install.

```bash
sudo pacman -S --needed git python libnotify
PY=python3
```

<a id="python"></a>
### 3. Python dependencies in a venv

Install migite's Python packages into a virtual environment inside the checkout, not into the
system Python. This is the one recipe that works everywhere: Debian 12+, Ubuntu 23.04+, Arch,
Fedora 38+ and Homebrew all mark their system Python as *externally managed*, so a plain
`pip install` there fails with `error: externally-managed-environment`, and `--user` fails the
same way.

```bash
$PY -m venv ~/Code/migite/.venv
~/Code/migite/.venv/bin/python -m pip install --upgrade pip
~/Code/migite/.venv/bin/python -m pip install langgraph pyyaml    # pyyaml: only for .yml configs
```

Then tell migite to use that interpreter, so it works from any repo without activating the venv:

```bash
echo 'export MIGITE_PYTHON="$HOME/Code/migite/.venv/bin/python3"' >> "$RC" && source "$RC"
"$MIGITE_PYTHON" -c "import langgraph, yaml; print('ok')"
```

Packages belong to an interpreter, not to a directory: the venv's Python sees langgraph whether
you run migite from `~/Code/migite` or from `~/Code/your-app`, and your projects' own Pythons
stay separate from migite's. To add or upgrade a package later, run
`"$MIGITE_PYTHON" -m pip install ...`.

`.venv/` is ignored by git, so `git pull` never touches it. If you upgrade the Python it was built
from (an Arch or Homebrew minor-version bump), the venv breaks with a `No such file or directory`
for its interpreter; delete `.venv` and rerun this step.

<a id="version-managers"></a>
### Version managers: asdf, mise, pyenv, uv

Any Python 3.11+ works as the base for the venv, including one from a version manager. Create the
venv from its full path, not from a shim, so it keeps working in directories where no version is
pinned:

```bash
PY="$(asdf where python 3.13.5)/bin/python3"   # asdf
PY="$(mise where python@3.13)/bin/python3"     # mise (preinstalled on Omarchy)
PY="$(pyenv prefix 3.13.5)/bin/python3"        # pyenv
```

Then run [step 3](#python) as written. With [uv](https://docs.astral.sh/uv/), which downloads its
own Python, step 3 is:

```bash
uv venv --python 3.13 ~/Code/migite/.venv
uv pip install --python ~/Code/migite/.venv/bin/python langgraph pyyaml
```

Why `MIGITE_PYTHON` matters here: without it, migite uses `python3` from `PATH` **if it actually
runs**, then falls back to a path under `~/.asdf`. An asdf or mise shim is on `PATH` even when no
version is selected for the current directory and fails with `No version is set for command
python3`. Migite detects that and falls through, but setting `MIGITE_PYTHON` removes the guesswork.

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
ℹ bash lib: 2855 lines in 13 files (largest: lib/phases/plan.sh, 527; split trigger: ~600)

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
plan, and `docs/improvements.md` in the migite checkout.

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

- [workflows/](./workflows/README.md): one tutorial per workflow, with the variants you will reach for next.
- Try `gates.commit.policy: strict` in a team repo's `.migite.yml`, and `models.effort.strong: xhigh` in your user config. Compare `usage.json` and the gate outcomes across a few runs.
- Feed a whole initiative through `migite-explore "…" --intakes`, then run each intake with `migite --intake`.
- Read `knowledge.md` after a few runs; it is the memory the planner starts from every time.
- [docs/migite.md](./migite.md) for every mode and gate, [docs/configuration.md](./configuration.md) for every key.
