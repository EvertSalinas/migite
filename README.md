# Migite

> 右手 (みぎて) — right hand. The trusted assistant that handles the groundwork so you can focus on what matters.

Migite wraps an agent CLI (Claude Code by default, Cursor CLI or OpenCode by config) around every
engineering task in the same shape: **plan** before you touch
a file, **gate** before you implement, **review** before you commit, and **write down** what was
learned. It never commits for you. It extends your reach without replacing your judgment.

```bash
migite --jira BB-1234 --type feature      # plan → gate → implement → heal → review → gate → PR description
```

- **Two human gates**, one after the plan and one before the commit, with approve / AI-refine / hand-edit / redo / abort at each.
- **Autonomous everywhere else**: 7 parallel codebase explorers, an architecture critic, an auto-heal loop for lint and tests, 4 specialist reviewers, a typed verdict.
- **Memory**: every artifact mirrored to a markdown vault (Obsidian-friendly), and a per-repo `knowledge.md` injected into every future plan.
- **Machine-readable**: `plan.json`, `review.json`, and a per-run usage ledger with cost per model.
- **Configurable**: one layered `.migite.yml` for models, effort, gates, permissions, prompts, budget.
- **Agent-agnostic**: drives Claude Code by default, Cursor CLI or OpenCode with one config line (`agent.backend`). Capabilities a CLI lacks degrade explicitly, never silently.

---

## Contents

- [Quickstart](#quickstart)
- [A run, end to end](#a-run-end-to-end)
- [Which tool for which situation](#which-tool)
- [Configuration in one minute](#configuration)
- [Requirements and portability](#requirements)
- [Documentation](#documentation)
- [Roadmap](#roadmap)

---

<a id="quickstart"></a>
## Quickstart

Five minutes from clone to first run. The detailed version, with macOS and Linux notes, is in
[docs/getting-started.md](./docs/getting-started.md).

```bash
# 1. Clone and put the commands on your PATH
git clone <this-repo> ~/Code/migite
MIGITE_SRC="$HOME/Code/migite"; BIN="$HOME/.local/bin"; mkdir -p "$BIN"
for f in "$MIGITE_SRC"/bin/*; do ln -sf "$f" "$BIN/$(basename "$f")"; done
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc   # or ~/.bashrc

# 2. Python deps (3.11+). PyYAML is optional: only needed for YAML config files.
pip3 install langgraph pyyaml

# 3. Your personal defaults (vault location, editor, models). Every value in the
#    generated file is a default — delete the lines you don't change.
migite config --init --user && $EDITOR ~/.config/migite/config.yml

# 4. Check the install, then run your first task
cd ~/Code/your-rails-app
migite doctor
migite "add a health-check endpoint" --type feature
```

Prerequisites: an agent CLI logged in (Claude Code by default; Cursor CLI or OpenCode via
`agent.backend`), `git`, Python 3.11+, and `bundle` for Rails repos. No separate API key: every
model call shells out to the CLI and shares its auth.

---

<a id="a-run-end-to-end"></a>
## A run, end to end

What `migite --jira BB-1234 --type feature` looks like on a Rails repo, abbreviated. Everything
between the two gates is autonomous. The full transcript with every prompt and output file is in
[docs/getting-started.md](./docs/getting-started.md#first-run).

```text
▶ Jira mode: BB-1234
▶ Current branch: feature/BB-1234-pdf-export
▶ Stack: rails — app dir: .
▶ Config: /Users/you/.config/migite/config.yml
▶ Repo: Acme/invoices-api
▶ Base branch: main

▶ Phase 1/4 — Planning
▶ Fetching Jira ticket BB-1234...
✔ Jira ticket fetched, added to planning context
▶ Created intake from template: feature          ← $EDITOR opens the intake; fill it in
  Starting LangGraph agent: Planning
  ▶ Fanning out 7 explorers in parallel (stack=rails)
  ▶ Synthesising plan from 7 exploration reports
  ▶ Architecture critic
  ▶ Refining plan with critic findings
  ▶ Generating testing plan
    ✔ plan.md → scratchpad/bb-1234/plan.md
    ✔ plan.json → scratchpad/bb-1234/plan.json

── Architecture critic ─────────────────────────────
- 🟡 **Warning** — `Invoice#pdf_url` is called per row in the index serializer; eager-load or cache
────────────────────────────────────────────────────
────────────────────────────────────────
  REVIEW GATE: plan
────────────────────────────────────────
Proceed with plan? [y/f/e/n/q] (y=approve, f=feedback refine, e=edit directly, n=full redo, q=abort): y

▶ Phase 2/4 — Implementing                        ← interactive Claude Code session, /exit when done
▶ Phase 2.5 — Running checks (auto-heal enabled, max 3 attempts)
✔ All checks passed — no healing needed

▶ Phase 3/4 — Reviewing
✔ Rubocop clean
  ▶ Fanning out 4 specialist reviewers in parallel
    ✔ review.json → scratchpad/bb-1234/review.json  (verdict=ready, 0🔴 1🟡 2🟢, source=structured)

── Commit context ──────────────────────────
  Verdict: READY TO COMMIT
  Findings: 0 critical · 1 warnings · 2 notes
  Reason:  Implementation matches the plan; the one warning is a missing request spec for the 422 path.
  Specs:   all passed
  Rubocop: clean
  Cost:    16 calls, $2.87 so far (headless calls only)
────────────────────────────────────────────
Proceed? [y/f/e/n/q] (y=commit, f=Claude fixes, e=edit directly, n=fix it yourself, q=abort): y

▶ Phase 3.5/4 — Capturing knowledge
▶ Phase 4/4 — Generating PR description           ← interactive, writes pr-description.md
▶ Phase 4.5/4 — Capturing improvement notes
✔ Workflow complete.

── Usage ───────────────────────────────────────
  model                              calls  in+cache tok  out tok    time     cost
  claude-opus-5-5                        6       241,318    9,204    212s    $2.41
  claude-haiku-4-5-20251001              7       165,438    2,710     41s    $0.33
  claude-sonnet-5                        5        98,120    4,411     64s    $0.44
  total                                 18       504,876   16,325    317s    $3.18
```

`y` at the commit gate does not commit. It ends the review loop; the commit is yours to make, and
`pr-description.md` is ready to paste. With `gates.commit.policy: strict`, `y` is refused while
blockers remain and a capital `Y` overrides with a record.

---

<a id="which-tool"></a>
## Which tool for which situation

| Situation | Command | Output |
|-----------|---------|--------|
| Repo exists, work is decided, ready to build | `migite --jira BB-1234` | plan, review, PR description, knowledge |
| Feedback arrived after implementation (PR comments, QA) | `migite --amend "must be idempotent on retry"` | `amendment-NN.md`, then the normal implement → review flow |
| Big initiative, unsure if it's worth doing | `migite-explore "extract billing into a service" --intakes` | feasibility doc with a PROCEED / SPIKE / DEFER / NOT WORTH IT verdict, adversarial challenge, one intake per workstream |
| No repo yet, defining a new project | `migite-blueprint --brief brief.md --name billing-api` | blueprint, milestone intakes, seed `knowledge.md` |
| What's wrong with this codebase? | `migite-audit --focus jobs` | ranked findings, feedable into `migite --audit` |
| Reviewing a teammate's branch | `migite-pr-review --branch feat/x` | review with verdict, read-only |
| Is my install healthy? | `migite doctor` | config, prompts, tools, scratchpad drift, orphaned sentinels |
| What does this ticket say? | `migite-ticket BB-1234` | the ticket as markdown, via Atlassian's `acli` or the agent |

All commands: [docs/migite.md](./docs/migite.md) for the orchestrator's modes,
[docs/standalone-tools.md](./docs/standalone-tools.md) for the other four, [docs/tickets.md](./docs/tickets.md) for `migite-ticket`.

---

<a id="configuration"></a>
## Configuration in one minute

Precedence: **flags > env vars > `$MIGITE_CONFIG` > `<repo>/.migite.yml` > `~/.config/migite/config.yml` > defaults**.
Every default equals the behaviour without a file, so nothing changes until you write one.

```bash
migite config --init --user   # once: ~/.config/migite/config.yml, your defaults for every repo
migite config --init          # per repo: .migite.yml, commit it with the project
migite config                 # effective config, with the source of every value
```

A repo file that turns on the strict gate and pins one model:

```yaml
# .migite.yml
gates:
  commit:
    policy: strict            # y refused while NEEDS FIXES / failing specs / offenses remain; Y overrides and is logged
models:
  roles:
    think: claude-sonnet-5    # cheaper planner for this repo; everything else stays on the defaults
  effort:
    strong: xhigh             # --effort for every strong-tier call
```

Models are three tiers, `fast` / `standard` / `strong` (Haiku 4.5 / Sonnet 5 / Opus 5.5 by
default), plus 28 per-call-site roles you can pin individually. Full reference, every key, and
five ready-made recipes: [docs/configuration.md](./docs/configuration.md).

---

<a id="requirements"></a>
## Requirements and portability

| Needs | Why |
|-------|-----|
| An agent CLI, logged in: `claude` (default), `cursor-agent`, or `opencode` | Every model call. Auth is the CLI's own; no API key. See [docs/agents.md](./docs/agents.md) |
| `git` | Branch detection, diff scoping, base-branch resolution |
| Python 3.11+ with `langgraph` | The planning, review, explore, audit, blueprint, and PR-review agents |
| `bundle` | Only for the `rails` stack (rubocop + rspec). Not needed for `generic` |
| PyYAML | Only if you use `.yml` config files. `.json` works without it |

Runs on **macOS and Linux** with bash 4+. Notifications use `osascript` or `notify-send` when
present and are skipped otherwise. tmux is optional: inside tmux, agents and interactive phases
open in split panes; outside, they run inline. Python is resolved as `MIGITE_PYTHON` if set, else
`python3` if it actually runs (an asdf shim with no version pinned is detected and skipped), else
a fallback path. The vault is plain markdown at `~/dev-log` by default; point `vault.base` at an
Obsidian vault if you want the `[[wikilinks]]` to resolve.

Stacks: `rails` is detected from a `Gemfile` at the repo root or one level down; anything else is
`generic`, which runs the whole pipeline minus lint and tests. See
[`--stack`](./docs/migite.md#stack-values).

---

<a id="documentation"></a>
## Documentation

| Doc | Read it when |
|-----|--------------|
| [docs/getting-started.md](./docs/getting-started.md) | Installing on macOS or Linux, verifying with `doctor`, a full first run with every file it produces |
| [docs/migite.md](./docs/migite.md) | The command-line reference and the run modes: amend, intake, blueprint, audit, staged; resuming a run |
| [docs/phases.md](./docs/phases.md) | Phase by phase: what each step calls, every gate key, which files get linted and tested, memory injection, tmux |
| [docs/outputs.md](./docs/outputs.md) | Every file a run writes, the commit-gate banner, the testing-plan requirement |
| [docs/configuration.md](./docs/configuration.md) | You are writing a `.migite.yml`: every key, model roles and effort, strict gate, recipes |
| [docs/agents.md](./docs/agents.md) | Running migite on Cursor CLI or OpenCode instead of Claude Code: capability matrix, per-backend models, what degrades |
| [docs/standalone-tools.md](./docs/standalone-tools.md) | `migite-blueprint`, `migite-explore`, `migite-audit`, `migite-pr-review` |
| [docs/tickets.md](./docs/tickets.md) | Using `--jira`: setting up `acli` (browser login, no token), the agent fallback, `migite-ticket` |
| [docs/internals.md](./docs/internals.md) | Repository layout, the agent scripts' CLIs, `plan.json` / `review.json` / usage ledger with examples |
| [docs/vault-structure.md](./docs/vault-structure.md) | Where every file lands in the vault and how the org folder is chosen |
| [docs/troubleshooting.md](./docs/troubleshooting.md) | Permission failures, config errors, missing Python or PyYAML, review loops, nested Claude sessions |
| [docs/faq.md](./docs/faq.md) | Does it commit? Does `--jira` fetch the ticket? Why only 14 files per explorer? |
| [docs/glossary.md](./docs/glossary.md) | Intake, scratchpad, vault, gate, envelope, ledger, role, tier |

---

<a id="roadmap"></a>
## Roadmap

Roughly in the order they are likely to land:

- **Stack profiles as data.** `stack:` can pick `rails` or `generic`; describing detect / lint / autofix / test / globs in `.migite.yml` would make `node`, `python`, and `go` config blocks instead of bash function pairs, and let `migite-audit` / `migite-pr-review` drop their Rails-only checklists.
- **Run manifest and `--yes`.** A `run.json` at every phase boundary so a run can resume from a recorded state, and a non-interactive mode so migite can run from CI or from another agent.
- **Meter interactive sessions.** Headless calls are in the usage ledger; implement, gate fixes, and the PR description are not, because the CLI only reports usage in `--print` mode.
- **Verify Cursor and OpenCode live.** The adapters exist and are unit-tested against fake CLIs ([docs/agents.md](./docs/agents.md)); the first real runs should confirm the output shapes and pin default model ids per backend.
- **CI hardening.** Promote shellcheck warnings to blocking once triaged; add a smoke run of the agents against the fake CLI.
- **One-line installer** to replace the clone-and-symlink block above.
