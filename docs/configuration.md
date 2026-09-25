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
  - [gates](#gates)
  - [permissions](#permissions)
  - [heal](#heal)
  - [frontend](#frontend)
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

**Non-Rails repo or monorepo where detection picks wrong**

```yaml
stack: generic                  # skip rubocop/rspec; plan, implement, review still run
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
  backend: claude        # claude | cursor | opencode   (MIGITE_AGENT)
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
  strong: claude-opus-5-5           # tier: plan synthesis/refine, critic, correctness + security review, verdicts
  roles:                            # optional — pin one call site without moving its tier
    think: claude-sonnet-5
    review_security: claude-opus-5-5
  effort:                           # --effort per tier: low | medium | high | xhigh | max | none
    fast: none                      # none = don't pass the flag (CLI default). Haiku never receives it.
    standard: none
    strong: none
  roles_effort:                     # optional — per-role effort override
    critic: max
  timeout_seconds: 600              # per headless call
  thinking_timeout_seconds: 900     # for the calls marked as thinking-heavy (synthesis, critic)
```

The default tiering follows one rule: **a drafter is never weaker than the critic whose findings it
must apply**, and the calls that do the actual finding (plan synthesis, correctness and security
review) sit on the strong tier, while checklist work and extraction stay standard.

| Role | Default tier | Where |
|---|---|---|
| `explore` | fast | `migite-plan` — the 7 parallel codebase explorers, 8 when the task may touch the frontend (grounding, capped at 14 files each) |
| `think` | **strong** | `migite-plan` — synthesis, refine, testing plan: the highest-leverage text in the run |
| `critic` | strong | `migite-plan` — architecture critic |
| `review_correctness`, `review_security` | **strong** | `migite-review` — the two reviewers where a miss costs the most |
| `review_test_coverage`, `review_testing_plan` | standard | `migite-review` — checklist dimensions |
| `review_frontend` | standard | `migite-review` - Hotwire/Stimulus checklist, only when the diff touches views or JavaScript |
| `verdict` | strong | `migite-review` — structured verdict synthesis (decides the gate) |
| `knowledge`, `improve` | standard | `migite` Phases 3.5 / 4.5 |
| `amend`, `plan_refine`, `testing_plan`, `jira` | standard | `migite` amend mode, plan-gate refine, testing-plan regeneration, Jira fetch |
| `heal` | standard | `migite` Phase 2.5 auto-heal fixes for failing specs and leftover lint |
| `lens` | standard | `migite-explore` lenses |
| `explore_synth`, `challenge`, `explore_refine` | strong | `migite-explore` synthesis, adversarial challenge, and the revision that applies it |
| `analyst`, `extract` | standard | `migite-blueprint` analysts, milestone/knowledge extraction |
| `blueprint_synth` | strong | `migite-blueprint` synthesis |
| `audit_area` | standard | `migite-audit` per-layer auditors (Haiku misses subtle auth / N+1 issues) |
| `audit_synth` | standard | `migite-audit` report synthesis |
| `pr_review_correctness`, `pr_review_security` | strong | `migite-pr-review` reviewers |
| `pr_review_test_coverage`, `pr_review_conventions_and_migrations` | standard | `migite-pr-review` reviewers |
| `pr_verdict` | strong | `migite-pr-review` verdict |

Changing a tier moves every role in it; pinning a role moves only that call. An unknown role
name under `roles:` or `roles_effort:` is an error.

**Effort.** `models.effort.<tier>` (or `models.roles_effort.<role>`) becomes `--effort <level>` on
every headless call for that tier/role. `none` (the default everywhere) passes no flag, so the
CLI's own default applies — today's behaviour. On Sonnet 5 and Opus 5.5 effort is the first
quality/cost lever: `xhigh` is the recommended setting for coding work, `low` for cheap subagents.
Haiku 4.5 rejects the flag and never receives it, whatever the fast tier says. To go back to the
pre-2026-09 cheaper tiering, pin `think`, `explore_refine`, `review_correctness`, and
`review_security` to `claude-sonnet-5` and `audit_area` to the Haiku id.

<a id="stack"></a>
### `stack`

```yaml
stack: auto             # auto | rails | generic   (MIGITE_STACK; --stack on the command line wins)
```

`auto` runs the detection in `lib/stack.sh` (`Gemfile` at the root or one level down → `rails`,
else `generic`). Setting it explicitly is for monorepos where detection picks wrong, or to force
the no-tooling path. Describing stacks as data (lint/test commands, globs) is not supported yet —
see the README roadmap.

<a id="gates"></a>
### `gates`

```yaml
gates:
  commit:
    policy: lenient           # lenient | strict
    require_clean_lint: true  # strict only: remaining rubocop offenses block approval
    require_green_specs: true # strict only: spec failures or a tooling error block approval
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

<a id="heal"></a>
### `heal`

```yaml
heal:
  max_attempts: 3             # MAX_HEAL_ATTEMPTS — Phase 2.5 fix-loop cap
  full_suite_fallback: true   # Phase 3: run the whole rspec suite when no spec files changed
```

`full_suite_fallback: false` makes Phase 3 consistent with Phase 2.5 (skip rspec, say so) instead
of running the full suite, which needs a live DB and verifies nothing about the diff.

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
```

The cap is **soft**: once the run's usage ledger passes it, every gate banner shows a red
over-budget line. Nothing is aborted mid-graph. Interactive sessions aren't metered.

<a id="ui"></a>
### `ui`

```yaml
ui:
  tmux: auto                  # auto (split panes when inside tmux) | on | off
  notify: auto                # auto | off
  editor: nvim                # EDITOR
  prompt_inline_max: 100000   # MIGITE_PROMPT_INLINE_MAX
```

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
Phases use `cfg <key>`, `prompt_path <name>`, `template_path <name>`, and pass roles, never
models, to `agent_ask` / `agent_think`. `load_migite_config` also caches the configured agent's
description as `MIGITE_AGENT_*` (`load_agent_info`).

The Python agents and standalone tools call `migite.config.load(repo_root)` themselves, so they
work identically when invoked directly. `migite.paths.detect_org` consults `vault.org` after the
`MIGITE_ORG` env var. `migite.gateway.configure_from(cfg)` selects the agent and applies
timeouts, the headless permission mode, the role-to-model table, and the per-role effort table
to every later call. Bash reaches the same gateway through `python -m migite.agent_cli ask --role <role>`,
so the model and effort for a bash call site are resolved in exactly the same place.
