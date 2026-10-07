# Configuration

Migite reads a layered configuration. Every value has a built-in default equal to how migite
behaved before the config file existed, so adopting it is opt-in and incremental.

## Contents

- [Precedence](#precedence)
- [Files and formats](#files)
- [`migite config`](#command)
- [Recipes](#recipes)
- [Reference](#reference)
  - [agent](#agent)
  - [vault, logs](#vault)
  - [models](#models)
  - [stack](#stack)
  - [stacks](#stacks)
  - [gates](#gates)
  - [permissions](#permissions)
  - [heal](#heal)
  - [frontend](#frontend)
  - [knowledge](#knowledge)
  - [plan](#plan)
  - [review](#review)
  - [prompts, templates](#prompts)
  - [budget](#budget)
  - [ui](#ui)
- [Environment variables](#env)
- [How the pieces read it](#internals)

---

<a id="precedence"></a>
## Precedence

Highest first:

| Layer | Example |
|---|---|
| Command-line flags | `--stack generic`, `--type bug` |
| Environment variables | `DEV_LOG_BASE`, `MIGITE_ORG`, `MAX_HEAL_ATTEMPTS`, ... ([full list](#env)) |
| `$MIGITE_CONFIG` | an explicit file, any path |
| `<repo root>/.migite.yml` | per-project settings, kept out of git ([why](#gitignore)) |
| `~/.config/migite/config.yml` | your personal defaults (`$XDG_CONFIG_HOME` honoured) |
| Built-in defaults | identical to migite's pre-config behaviour |

Files merge key by key, so a repo file that sets only `gates.commit.policy` inherits everything
else from your user file and the defaults.

<a id="files"></a>
## Files and formats

`.migite.yml`, `.migite.yaml`, or `.migite.json` at the repo root; `config.yml` / `.yaml` /
`.json` under `~/.config/migite/`. YAML needs **PyYAML** (`"$MIGITE_PYTHON" -m pip install pyyaml`); it is only
required when a YAML file actually exists — with no config files, or JSON ones, migite runs
without it.

An invalid file (bad YAML, a value outside its enum, a non-integer where one is required)
**aborts the run** with the offending key and file named. Running on defaults after you wrote a
config would be worse than stopping. Unknown keys are a warning, not an error, so a typo is
visible but not fatal.

<a id="gitignore"></a>
## Keep repo config out of git

The per-repo `.migite.yml` (and a `.migite/` directory of prompt or template overrides) is your
local setup for that repo, not part of the project. Don't commit it: teammates who don't use
migite shouldn't see it in the tree, and yours shouldn't overwrite theirs on a pull.

Ignore it once for every repo on your machine, with git's global ignore file, instead of editing
each project's shared `.gitignore`:

```bash
mkdir -p ~/.config/git
printf '%s\n' '.migite.yml' '.migite.yaml' '.migite.json' '.migite/' >> ~/.config/git/ignore
```

`~/.config/git/ignore` is the file git reads when `core.excludesFile` is unset. If you have set
`core.excludesFile` (`git config --global core.excludesFile` prints it), append the lines to that
file instead. To ignore it in a single repo without touching its `.gitignore`, add the same lines
to that repo's `.git/info/exclude`. Check that it took effect with
`git check-ignore -v .migite.yml`.

<a id="command"></a>
## `migite config`

```bash
migite config --edit --user   # open ~/.config/migite/config.yml, your defaults for every repo
migite config --edit          # open this repo's .migite.yml (git-ignored, see above)
migite config                 # effective configuration, with the source of every value
migite config --init [--user] # write a starter file without opening it (--force overwrites)
migite config --validate      # exit 1 on errors, print warnings
migite config --path [--user] # print the file --edit would open
migite config --help          # all of the above
migite doctor                 # also validates the config and lists the files it loaded
```

`--edit` writes the starter first when the file doesn't exist yet, opens it in `$EDITOR` (else
`ui.editor`, else `vim`), and validates the result as soon as you close the editor, so a typo
shows up then rather than at the next run. It also opens a file that currently fails to load,
which is usually why you want to edit it.

**Getting started.** Run `migite config --edit --user` and delete every line you
are not changing — each value in the starter is the built-in default, so an empty file and no
file behave identically. Typical first edits: `vault.base` if your vault is not `~/dev-log`,
`ui.editor`, and `models.effort.strong: xhigh`. Then, in a repo that needs something different
(a strict commit gate, a prompt override, `stack: generic` on a monorepo), run `migite config
--edit` there and keep only those keys. `migite config` at any time prints what won and from
which file. `--init` refuses to overwrite an existing file unless you pass `--force`.

`migite config` output ends with the resolved model for every call-site role and which layer
pinned it — the quickest way to see what a change to `models:` actually did.

---

<a id="recipes"></a>
## Recipes

Copy the one that matches, delete the rest of the starter file.

**Solo developer, first config** (`~/.config/migite/config.yml`)

```yaml
vault:
  base: ~/Documents/MyVault/dev-log
ui:
  editor: nvim
models:
  effort:
    strong: xhigh
```

**Repo with a hard gate** (`<repo>/.migite.yml`)

```yaml
gates:
  commit:
    policy: strict              # y refused over NEEDS FIXES / failing specs / offenses; Y overrides and is logged
    require_clean_lint: true
    require_green_specs: true
heal:
  full_suite_fallback: false    # never run the whole suite when no spec files changed
templates:
  dir: .migite/templates        # your own PR template for this repo: .migite/templates/commit.md
```

**Cheap mode** for spikes and throwaway branches (`.migite.yml` or `MIGITE_CONFIG=cheap.yml`)

```yaml
models:
  roles:
    think: claude-sonnet-5
    explore_refine: claude-sonnet-5
    review_correctness: claude-sonnet-5
    review_security: claude-sonnet-5
    verdict: claude-sonnet-5
  effort:
    standard: medium
budget:
  max_usd_per_run: 1.50
```

**Node repo: heal and review run its linter and tests** (`<repo>/.migite.yml`)

```yaml
stacks:
  node:
    detect: [package.json]
    source: ["*.js", "*.ts"]            # quote globs: a bare * starts a YAML alias
    specs: ["*.test.js", "*.test.ts"]
    autofix: npx eslint --fix {files}
    lint: npx eslint {files}
    test: npx jest {files}
```

**Repo with nothing to lint or test, or a monorepo where detection picks wrong**

```yaml
stack: generic                  # skip lint and tests; plan, implement, review still run
permissions:
  headless: edits               # if your managed settings forbid auto-approval
```

**Override one prompt for one project**

```yaml
prompts:
  dir: .migite/prompts          # only files present here are overridden; the rest fall back
```

```bash
mkdir -p .migite/prompts && cp ~/Code/migite/prompts/review.md .migite/prompts/review.md
$EDITOR .migite/prompts/review.md      # e.g. add the repo's checklist items
migite config | grep prompts           # confirm it is picked up
```

---

<a id="reference"></a>
## Reference

The starter file written by `migite config --init` is the same as this reference with defaults
filled in.

<a id="agent"></a>
### `agent`

```yaml
agent:
  backend: claude        # claude | cursor | kimi | opencode   (MIGITE_AGENT)
  command: null          # override the executable: a name on PATH or a full path
```

Which agent CLI every call goes through. Model tiers, effort, structured output, and the Jira
fetch behave differently per backend; see [agents.md](./agents.md) for the capability matrix.

<a id="tracker"></a>
### `tracker`

```yaml
tracker:
  provider: auto          # auto | jira-acli | jira-agent | none   (MIGITE_TRACKER)
  jira:
    acli: acli            # MIGITE_ACLI: Atlassian's CLI, a name on PATH or a full path
    acceptance_field: null   # custom field id holding acceptance criteria, e.g. customfield_10035
```

Where `--jira` gets the ticket's content. `auto` uses `acli` when it is installed and logged in
(`acli jira auth login --web`, once, through the browser), else one agent call through the
Atlassian MCP tools when the agent supports the `jira.read` scope, else nothing. migite never
handles a Jira credential; a token key in this block is refused as a config error. Setup and the
`migite-ticket` command: [tickets.md](./tickets.md).

<a id="vault"></a>
### `vault`, `logs`

```yaml
vault:
  base: ~/dev-log       # DEV_LOG_BASE — where the read-only mirror + knowledge.md live
  org: Acme             # MIGITE_ORG — default: name of the directory containing the repo
logs:
  dir: ~/.dev-workflow/logs   # LOG_DIR — per-run logs, prompts, wrapper scripts, usage ledger
```

<a id="models"></a>
### `models`

Model choice is expressed as three **tiers** plus optional per-**role** pins. Every call site in
every tool has a role; each role has a default tier.

```yaml
models:
  # Tiers are model ids for the ACTIVE backend. Unset = that backend's default
  # (claude: haiku-4-5 / sonnet-5 / opus-5-5; cursor and opencode: no --model passed until you pin one).
  fast: claude-haiku-4-5-20251001   # tier: file exploration
  standard: claude-sonnet-5         # tier: lenses, analysts, audit areas, checklist review, knowledge, amendments
  strong: claude-opus-5-5           # tier: plan synthesis/refine, critic, correctness + security
                                   # review, verdicts, interactive sessions
  roles:                            # optional — pin one call site without moving its tier
    think: claude-sonnet-5
    review_security: claude-opus-5-5
  effort:                           # --effort per tier: low | medium | high | xhigh | max | none
    fast: none                      # none = don't pass the flag (CLI default). Haiku never receives it.
    standard: none
    strong: none
  roles_effort:                     # optional — per-role effort override
    critic: max
  timeout_seconds: 600              # headless calls off the strong tier
  thinking_timeout_seconds: 900     # strong-tier calls (synthesis, critic, review, verdict)
  timeouts:                         # optional per-tier timeout override, in seconds
    strong: 1800
  roles_timeouts:                   # optional per-role timeout override, in seconds (beats the tier)
    think: 1800
```

The default tiering follows one rule: **a drafter is never weaker than the critic whose findings it
must apply**, and the calls that do the actual finding (plan synthesis, correctness and security
review) sit on the strong tier, while checklist work and extraction stay standard.

| Role | Default tier | Where |
|---|---|---|
| `explore` | fast | `migite-plan` — the 7 parallel codebase explorers, 8 when the task may touch the frontend (grounding, capped at 14 files each) |
| `think` | **strong** | `migite-plan` — synthesis and refine: the highest-leverage text in the run |
| `critic` | strong | `migite-plan` — architecture critic |
| `review_correctness`, `review_security` | **strong** | `migite-review` — the two reviewers where a miss costs the most |
| `review_test_coverage`, `review_testing_plan` | standard | `migite-review` — checklist dimensions |
| `review_frontend` | standard | `migite-review` - Hotwire/Stimulus checklist, only when the diff touches views or JavaScript |
| `verdict` | strong | `migite-review` — structured verdict synthesis (decides the gate) |
| `knowledge`, `improve` | standard | `migite` Phases 3.5 / 4.5 |
| `plan_fold` | standard | `migite` Phase 3.8, the exact edits that keep `plan.md` current after each run |
| `summary` | fast | `migite` Phase 4.2, the run's `summary.md` (condenses files the run already wrote) |
| `amend`, `plan_refine`, `jira` | standard | `migite` amend mode, plan-gate refine, Jira fetch |
| `testing_plan` | standard | `generate_testing_plan` (in `migite-plan`, or in Phase 3 with `plan.testing_plan_when: review`), and the testing-plan edits and regeneration in amend and fix rounds. It writes a checklist document from the finished plan, so it does not need the strong tier |
| `heal` | standard | `migite` Phase 2.5 auto-heal fixes for failing specs and leftover lint |
| `session` | **strong** | `migite` interactive sessions (`run_phase`): implement, spec writing, gate fixes, PR description |
| `lens` | standard | `migite-explore` lenses |
| `explore_synth`, `challenge`, `explore_refine` | strong | `migite-explore` synthesis, adversarial challenge, and the revision that applies it |
| `analyst`, `extract` | standard | `migite-blueprint` analysts, milestone/knowledge extraction |
| `blueprint_synth` | strong | `migite-blueprint` synthesis |
| `audit_area` | standard | `migite-audit` per-layer auditors (Haiku misses subtle auth / N+1 issues) |
| `audit_synth` | standard | `migite-audit` report synthesis |
| `pr_review_correctness`, `pr_review_security` | strong | `migite-pr-review` reviewers |
| `pr_review_test_coverage`, `pr_review_conventions_and_migrations` | standard | `migite-pr-review` reviewers |
| `pr_verdict` | strong | `migite-pr-review` verdict |
| `refute` | strong | `migite-review` and `migite-pr-review`: the second agent that tries to disprove each Critical before the verdict. Never weaker than the reviewers it checks; pin it to another model or backend to decorrelate errors |

Changing a tier moves every role in it; pinning a role moves only that call. An unknown role
name under `roles:` or `roles_effort:` is an error.

**Interactive sessions.** `run_phase` (implement, spec writing, gate fixes, PR description) opens
the agent's own interface, so it is neither metered nor a headless call — but it runs on the
`session` role's model, passed to the CLI as its model for that run. Without it, the CLI decides
on its own: opencode resumes whatever model its last session in this directory used, whatever
`models:` says. Pin `models.roles.session` to move interactive work without touching a tier.
(`migite config` shows a `timeout=` for the role like any other; it is unused — a session ends
when you exit it.)

**Effort.** `models.effort.<tier>` (or `models.roles_effort.<role>`) becomes `--effort <level>` on
every headless call for that tier/role. `none` (the default everywhere) passes no flag, so the
CLI's own default applies — today's behaviour. On Sonnet 5 and Opus 5.5 effort is the first
quality/cost lever: `xhigh` is the recommended setting for coding work, `low` for cheap subagents.
Haiku 4.5 rejects the flag and never receives it, whatever the fast tier says. To go back to the
pre-2026-09 cheaper tiering, pin `think`, `explore_refine`, `review_correctness`, and
`review_security` to `claude-sonnet-5` and `audit_area` to the Haiku id.

**Timeouts.** Every headless call is bounded. Strong-tier calls (synthesis, critic, review,
verdict — the slow ones) get `thinking_timeout_seconds`; every other call gets
`timeout_seconds`. The tier is enough, so a slow call can't be forgotten: a role on the strong
tier always gets the longer limit. To size a specific model or call, override per tier with
`models.timeouts.<fast|standard|strong>` or per role with `models.roles_timeouts.<role>` (the
role wins). Set a generous value when a reasoning backend routinely runs long — e.g.
`models.timeouts.strong: 1800`. `migite config` prints each role's resolved `timeout=`.

The testing plan used to run on the strong tier, so its generation had `thinking_timeout_seconds`
(900s). On the standard tier it gets `timeout_seconds` (600s). A long testing plan on a slow
backend can need more: `models.roles_timeouts.testing_plan: 900`. A timeout while `migite-plan`
generates it fails the whole plan run (with `plan.testing_plan_when: review` it only costs the
testing plan, see [plan](#plan)), so raise it if you see one.

<a id="stack"></a>
### `stack`

```yaml
stack: auto             # auto | rails | generic | a profile name from stacks   (MIGITE_STACK; --stack on the command line wins)
```

`auto` runs the detection in `lib/stack.sh`, in this order:

1. the [`stacks`](#stacks) profiles, by their `detect` files
2. `rails`: a `Gemfile` at the root or one level down
3. `generic`: everything else, with no lint or tests

Set it explicitly for a monorepo where detection picks wrong, to force the no-tooling path, or to
pick a profile that has no `detect` files. A name that is neither built in nor a configured
profile is a config error.

<a id="stacks"></a>
### `stacks`

```yaml
stacks:
  node:                                 # the name: lowercase letters, digits, underscores
    detect: [package.json]              # any one of these at the repo root selects the profile
    source: ["*.js", "*.ts"]            # the changed files lint and autofix get
    specs: ["*.test.js", "*.test.ts"]   # the changed files test gets
    autofix: npx eslint --fix {files}   # runs before lint; its exit code is ignored
    lint: npx eslint {files}            # exit code 0 = clean
    test: npx jest {files}              # exit code 0 = green
```

A profile describes a non-Rails repo's lint and test commands as data. Heal (Phase 2.5) and
review (Phase 3) run them where they run rubocop and rspec on a Rails repo. Every key is optional
except one command: a profile needs at least one of `lint`, `autofix` and `test`. Profiles are
empty by default, which leaves detection as it was before they existed: `rails` or `generic`.

**Detection.** Profiles are tried before `rails`. A profile from a higher-precedence file comes
first (the repo's `.migite.yml` before `~/.config/migite/config.yml`), then each file's in the
order written. A profile matches when any one of its `detect` paths or globs exists at the repo
root. One with no `detect` is used only when `stack:` or `--stack` names it. `rails`, `generic` and
`auto` can't be profile names: the Rails path stays as it is.

> **A profile in your user config applies to every repo.** A user-level `node` profile with
> `detect: [package.json]` also claims every Rails app that ships a `package.json`, because
> profiles come before `rails`. Put a profile in the repo's `.migite.yml`, give it a `detect`
> file only that kind of repo has, or set `stack: rails` in the Rails repos. The run's `Stack:` line
> and `migite doctor` name the profile and the file that matched.

**Which files a command gets.** `source` and `specs` filter the same changed-file list every phase
uses: the diff against the base branch plus untracked files, minus deletes and `scratchpad/`
([Which files get linted and tested](./phases.md#lint-test-selection)). A glob matches the path
from the repo root:

- `*` crosses directories, so `*.js` is every `.js` file and `src/*` is all of `src/`.
- `**/` is zero or more directories, so `**/test_*.py` is a `test_*.py` at any depth.
- No globs means every changed file.

Quote globs in YAML: a bare `*` starts an alias. A command runs only when its list has at least
one file.

**`{files}`.** In a command, `{files}` becomes that list, each path a separate, quoted argument (a
space in a name is safe). A command without `{files}` runs as written, which is what a whole-suite
runner like `go test ./...` wants; the globs then only decide *when* it runs. Commands run with
`bash -c` from the repo root, with stdin closed. Chain several with `&&`.

**Pass or fail is the exit code.** 0 passes. 126 or 127 (not executable, not installed) means the
command could not start: that is a tooling error on the commit-gate banner, never something heal
asks the agent to fix. Any other code is a failure. Heal hands the failing output to the agent
through the same capped excerpt as rubocop and rspec (`heal.prompt_log_max_bytes`). Under
`gates.commit.policy: strict`, failing tests and lint are blockers (`require_green_specs`,
`require_clean_lint`).

**What a profile does not have.** These are Rails' and stay in its code path:

- the `tooling_failed` log patterns
- the app directory one level down
- `heal.full_suite_fallback` (a test command without `{files}` already is the whole suite)
- the `frontend` linters and reviewer

More examples:

```yaml
stacks:
  python:
    detect: [pyproject.toml, setup.py]
    source: ["*.py"]
    specs: ["**/test_*.py", "*_test.py"]
    autofix: ruff check --fix {files} && ruff format {files}
    lint: ruff check {files}
    test: pytest {files}
  go:
    detect: [go.mod]
    source: ["*.go"]
    specs: ["*.go"]                     # any Go change runs the suite
    lint: test -z "$(gofmt -l {files})" && go vet ./...
    test: go test ./...
```

<a id="gates"></a>
### `gates`

```yaml
gates:
  commit:
    policy: lenient           # lenient | strict
    require_clean_lint: true  # strict only: remaining rubocop offenses (a profile's failing lint) block approval
    require_green_specs: true # strict only: spec failures (a profile's failing tests) or a tooling error block approval
  plan:
    warn_after_rejections: 3  # warn that the task may be too large after N full redos (0 = never)
```

`lenient` is the pre-config behaviour: `y` at the commit gate always approves. `strict` refuses
`y` while any blocker remains — a `NEEDS FIXES` verdict (read from `review.json`), spec failures,
a tooling error, or remaining rubocop offenses depending on the two `require_*` flags — and lists
them. A capital **`Y`** approves anyway and appends the blockers to `gate-overrides.md` in the
scratchpad (mirrored to the vault), so overrides leave a record.

<a id="permissions"></a>
### `permissions`

```yaml
permissions:
  interactive: auto          # implement / fix / PR-description sessions (run_phase)
  heal: auto                 # the auto-heal loop's headless fixes
  headless: none             # plan, review, knowledge, amendments, standalone tools
  headless_tools: isolated   # isolated | default
```

Values are migite's own words, which each agent adapter maps onto its CLI's flags:

| Word | Meaning | Claude Code alias |
|---|---|---|
| `auto` | approve every tool use without asking | `bypassPermissions` |
| `edits` | approve file edits, ask for anything else | `acceptEdits` |
| `plan` | read-only planning mode | `plan` |
| `ask` | the CLI's own default: ask before acting | `default` |
| `none` | pass no permission flag at all | |

The Claude Code names are accepted as aliases and normalized, so configs written before the
neutral words keep working. `headless` applies uniformly to every headless call in every tool;
the `MIGITE_PERMISSION_MODE` env var still works and maps onto it. The Jira fetch always runs
with `auto` inside the `jira.read` tool scope when it goes through the agent. See [agents.md](./agents.md) for what
each word becomes on each CLI.

**`headless_tools`** decides what a headless call can use besides its prompt. With `isolated` (the
default) each role gets only what it needs, from `ROLE_TOOLS` in `migite/config.py`, and none of
the agent CLI's MCP servers, plugins, hooks or skills:

| Roles | Tools |
|---|---|
| `critic`, `review_*`, `pr_review_*`, `audit_area`, `refute` | `Read`, `Grep`, `Glob` only, capped at `budget.review_call_max_usd` per call |
| `heal` | the CLI's full toolset (it edits files) |
| `jira` | the CLI's full context (its scoped tools are MCP tools) |
| every other role | no tools: the prompt carries everything |

Your `~/.claude/CLAUDE.md` and the repo's `CLAUDE.md` files are still passed to every isolated
call, so their rules keep applying. Without isolation every call started by writing about 24k
tokens of CLI context into the cache, and prompts that said "you have no tools" still browsed the
repo turn after turn: one correctness review read up to 1.4M cached tokens. The usage summary's
`turns` column shows how many turns each call took. `default` gives every headless call the CLI's
full toolset and context again. Agents that can't restrict a call (Cursor, Kimi, OpenCode) print a
one-time notice and run as `default`.

<a id="heal"></a>
### `heal`

```yaml
heal:
  max_attempts: 3             # MAX_HEAL_ATTEMPTS — Phase 2.5 fix-loop cap
  full_suite_fallback: true   # Phase 3: run the whole rspec suite when no spec files changed (rails only)
  prompt_log_max_bytes: 60000 # per-log cap on the rubocop/rspec/frontend-lint (or a profile's lint/test) excerpts in a heal prompt
```

`full_suite_fallback: false` makes Phase 3 consistent with Phase 2.5 (skip rspec, say so) instead
of running the full suite, which needs a live DB and verifies nothing about the diff.

`prompt_log_max_bytes` bounds each tooling log embedded in a heal prompt. The rspec log is
compacted first (`migite/log_compact.py`): identical failures merge into one block with a count,
each keeps only its first backtrace frame, and the examples summary and the full "Failed examples:"
list are kept. Rubocop and frontend-lint logs, and an rspec log still over the cap after
compaction, keep the head (the first failure blocks) and the tail (the summary) and elide the
middle. The prompt names each full log's path so the agent can read more. Without a cap, a mass-failure rspec run (hundreds of backtraces) overflows the model's
context window and the CLI rejects the call.

<a id="frontend"></a>
### `frontend`

```yaml
frontend:
  lint: auto            # auto = erb_lint / eslint when the repo configures them; off = never
  system_specs: on      # off = skip spec/system in the changed-spec run and the full-suite fallback
  browser_check: off    # off | ask | on: Phase 3.1, the agent walks the testing plan in a browser
```

Every key here applies only when the diff touches views or JavaScript, so a backend-only task
behaves exactly as before. A bare `on` / `off` is fine in YAML: these keys (and `ui.tmux` /
`ui.notify`) read the YAML booleans back as the words. How the frontend is detected, and what
each check does, is in [docs/phases.md](./phases.md#frontend).

<a id="knowledge"></a>
### `knowledge`

```yaml
knowledge:
  inject_max_bytes: 8000      # knowledge.md entries put into prompts, up to this many bytes
  select: relevant            # relevant | recent
```

`knowledge.md` gains an entry every run. The plan, implement, fix and amend prompts get up to
`inject_max_bytes` of its entries, printed newest first, with a note naming the file for the
rest. They used to get the whole file, and the planner's explorers got its first 800 characters,
which were the header and the oldest lessons.

- `select: relevant` fills the budget with the entries that share the most words with the task
  first: the intake, plus the Jira ticket when `--jira` was used. The template's own text doesn't
  count. When no entry shares a word, it behaves like `recent`.
- `select: recent` fills it with the newest entries.

Each planner explorer gets the same kind of selection at 800 bytes. How words are matched is in
[docs/phases.md](./phases.md#active-memory-injection).

<a id="plan"></a>
### `plan`

```yaml
plan:
  testing_plan_when: plan     # plan | review
```

`plan` (the default) writes `testing-plan.md` in Phase 1, from the finished plan. `review` leaves
it out of Phase 1 and writes it at the start of Phase 3, from `plan.md` and the change as built
(the diff capped at `ui.prompt_diff_max_bytes`, plus the name of every changed file, since a diff
leaves new untracked files out), before the browser check and the reviewers read it. It then
describes what was built rather than what was planned, and a plan you redo no longer pays for a
testing plan nobody reads.

- A testing plan that already exists is kept: a resume, an `--amend` run, a fix round and a
  re-review never write over one.
- Redoing the plan of a task that already has a testing plan (`r` on an existing plan) sets the old
  testing plan aside in `scratchpad/<task>/.plan-history/` and removes both copies, so Phase 3
  writes a new one for the new plan.
- A failed or empty call only warns, and the review runs without it: the testing-plan reviewer
  reports it missing (unless `review.dimensions.testing_plan` is `off`), and the next re-review
  tries again. With `plan` timing a failed call fails `migite-plan` as a whole.
- `plan.json` records `testing_plan_when`, and `outputs.testing_plan` is `null` under `review`.
- The ledger shows the same `generate_testing_plan` call, role `testing_plan`, at either moment.

<a id="review"></a>
### `review`

```yaml
review:
  dimensions:
    testing_plan: on     # on | off: the testing-plan reviewer in Phase 3
```

`migite-review` runs four reviewers (correctness, security, test coverage, testing plan), plus
the frontend one when the diff touches views or JavaScript. `testing_plan: off` drops the
testing-plan reviewer: three reviewers run, the verdict is told the dimension did not run and
treats the testing-plan checklist item as N/A, and a re-review never carries or re-runs it.
`testing-plan.md` is still written, and still read by the browser check and the PR
description; the key only skips grading it. Turning it back on makes the next re-review run the
dimension, because the last review never did.

<a id="prompts"></a>
### `prompts`, `templates`

```yaml
prompts:
  dir: .migite/prompts        # relative to the repo root, or absolute
templates:
  dir: .migite/templates
```

Any `<dir>/<name>.md` overrides the same-named file under migite's `prompts/`
(`plan`, `implement`, `review`, `architecture_critic`) or `templates/` (`feature`, `bug`,
`refactor`, `spike`, `config`, `commit`). Files not present in the override dir fall back to the
repo copies, so you can override just the PR-description prompt for one project.

The directory may be relative (anchored at the repo root) or absolute, and may contain **`{org}`**
and **`{repo}`**, substituted with the vault org and the repo name. That makes a single user-level
setting serve several organisations with no file in any repo:

```yaml
# ~/.config/migite/config.yml
templates:
  dir: ~/.config/migite/templates/{org}
```

```text
~/.config/migite/templates/
├── Acme/commit.md        ← used for every repo under ~/Code/Acme/ (org = parent directory name)
└── Personal/commit.md    ← used for repos whose org resolves to Personal
                            repos under any other org fall back to migite's generic templates/commit.md
```

The generic `templates/commit.md` shipped with migite has no company-specific checklist items; a
team's own PR template belongs in an override like the one above.

<a id="budget"></a>
### `budget`

```yaml
budget:
  max_usd_per_run: 5.00       # soft cap; unset = no cap
  print_summary: true         # print the usage table at exit
  review_call_max_usd: 2.0    # hard cap on one read-only reviewer call
```

The run cap is **soft**: once the run's usage ledger passes it, every gate banner shows a red
over-budget line. Nothing is aborted mid-graph. Interactive sessions aren't metered.

`review_call_max_usd` is a **hard** cap on each read-only reviewer call (`--max-budget-usd`), a
guard against a reviewer that keeps opening files. A reviewer that hits it is reported as a
warning in the review, not a critical finding, so it doesn't block the gate; re-run the review or
raise the cap.

<a id="ui"></a>
### `ui`

```yaml
ui:
  tmux: auto                  # auto (split panes when inside tmux) | on | off
  notify: auto                # auto | off
  editor: nvim                # EDITOR
  prompt_inline_max: 100000   # MIGITE_PROMPT_INLINE_MAX
  prompt_diff_max_bytes: 30000  # cap on a branch diff embedded in a prompt
```

`prompt_diff_max_bytes` bounds the branch diff in the amend and testing-plan prompts. They get
`git diff --stat` for the whole change, then the diff cut to this size (its start and end), with
the full diff saved under `logs.dir` for reference.

---

<a id="env"></a>
## Environment variables

Every variable migite documented before the config file existed still works and **beats the
files**:

| Variable | Config key |
|---|---|
| `DEV_LOG_BASE` | `vault.base` |
| `MIGITE_AGENT` | `agent.backend` |
| `MIGITE_TRACKER` | `tracker.provider` |
| `MIGITE_ACLI` | `tracker.jira.acli` |
| `MIGITE_ORG` | `vault.org` |
| `LOG_DIR` | `logs.dir` |
| `MIGITE_STACK` | `stack` |
| `MAX_HEAL_ATTEMPTS` | `heal.max_attempts` |
| `MIGITE_PERMISSION_MODE` | `permissions.headless` |
| `EDITOR` | `ui.editor` |
| `MIGITE_PROMPT_INLINE_MAX` | `ui.prompt_inline_max` |
| `MIGITE_CONFIG` | path to an extra config file, loaded above the repo file |
| `MIGITE_PYTHON` | Python binary — **env only**, since Python is needed to read the config |
| `MIGITE_USAGE_LEDGER` | usage ledger path — env only, set per run by `migite` |

---

<a id="internals"></a>
## How the pieces read it

`migite/config.py` is the single resolver. `migite` calls `load_migite_config` (`lib/config.sh`)
right after the repo root is known; it `eval`s `python -m migite.config env`, which prints one
`MIGITE_CFG_<KEY>=value` assignment per leaf plus `MIGITE_CFG_MODEL_<ROLE>` for every resolved
role, then maps them onto the variables the phases already read (`DEV_LOG_BASE`, `LOG_DIR`, ...).
A `stacks` profile's lists go out one item per line (`MIGITE_CFG_STACKS_NODE_SOURCE`), and
`MIGITE_CFG_STACKS` names the profiles in the order detection tries them.
Phases use `cfg <key>`, `prompt_path <name>`, `template_path <name>`, and pass roles, never
models, to `agent_ask` / `agent_think`. `load_migite_config` also caches the configured agent's
description as `MIGITE_AGENT_*` (`load_agent_info`).

The Python agents and standalone tools call `migite.config.load(repo_root)` themselves, so they
work identically when invoked directly. `migite.paths.detect_org` consults `vault.org` after the
`MIGITE_ORG` env var. `migite.gateway.configure_from(cfg)` selects the agent and applies
timeouts, the headless permission mode, the role-to-model table, the per-role effort table, and
the per-role timeout table to every later call. Bash reaches the same gateway through
`python -m migite.agent_cli ask --role <role>`,
so the model and effort for a bash call site are resolved in exactly the same place.
