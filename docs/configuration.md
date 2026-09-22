# Configuration

Migite reads a layered configuration. Every value has a built-in default equal to how migite
behaved before the config file existed, so adopting it is opt-in and incremental.

## Contents

- [Precedence](#precedence)
- [Files and formats](#files)
- [`migite config`](#command)
- [Reference](#reference)
  - [vault, logs](#vault)
  - [models](#models)
  - [stack](#stack)
  - [gates](#gates)
  - [permissions](#permissions)
  - [heal](#heal)
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
| `<repo root>/.migite.yml` | per-project settings, committed with the repo |
| `~/.config/migite/config.yml` | your personal defaults (`$XDG_CONFIG_HOME` honoured) |
| Built-in defaults | identical to migite's pre-config behaviour |

Files merge key by key, so a repo file that sets only `gates.commit.policy` inherits everything
else from your user file and the defaults.

<a id="files"></a>
## Files and formats

`.migite.yml`, `.migite.yaml`, or `.migite.json` at the repo root; `config.yml` / `.yaml` /
`.json` under `~/.config/migite/`. YAML needs **PyYAML** (`pip install pyyaml`); it is only
required when a YAML file actually exists — with no config files, or JSON ones, migite runs
without it.

An invalid file (bad YAML, a value outside its enum, a non-integer where one is required)
**aborts the run** with the offending key and file named. Running on defaults after you wrote a
config would be worse than stopping. Unknown keys are a warning, not an error, so a typo is
visible but not fatal.

<a id="command"></a>
## `migite config`

```bash
migite config              # effective configuration, with the source of every value
migite config --init       # write a commented starter .migite.yml (all defaults) into the repo
migite config --validate   # exit 1 on errors, print warnings
migite doctor              # also validates the config and lists the files it loaded
```

`migite config` output ends with the resolved model for every call-site role and which layer
pinned it — the quickest way to see what a change to `models:` actually did.

---

<a id="reference"></a>
## Reference

The starter file written by `migite config --init` is the same as this reference with defaults
filled in.

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
  fast: claude-haiku-4-5-20251001   # tier: file exploration, audit areas
  standard: claude-sonnet-5         # tier: synthesis, review dimensions, knowledge, amendments
  strong: claude-opus-5             # tier: architecture critic, verdicts, adversarial challenge
  roles:                            # optional — pin one call site without moving its tier
    critic: claude-opus-5
    knowledge: claude-haiku-4-5-20251001
  timeout_seconds: 600              # per headless call
  thinking_timeout_seconds: 900     # for the calls marked as thinking-heavy (synthesis, critic)
```

| Role | Default tier | Where |
|---|---|---|
| `explore` | fast | `migite-plan` — the 7 parallel codebase explorers |
| `think` | standard | `migite-plan` — synthesis, refine, testing plan |
| `critic` | strong | `migite-plan` — architecture critic |
| `review` | standard | `migite-review` — the 4 specialist reviewers |
| `verdict` | strong | `migite-review` — structured verdict synthesis |
| `knowledge`, `improve` | standard | `migite` Phases 3.5 / 4.5 |
| `amend`, `plan_refine`, `testing_plan`, `jira` | standard | `migite` amend mode, plan-gate refine, testing-plan regeneration, Jira fetch |
| `lens`, `explore_refine` | standard | `migite-explore` lenses and refine |
| `explore_synth`, `challenge` | strong | `migite-explore` synthesis and adversarial challenge |
| `analyst`, `extract` | standard | `migite-blueprint` analysts, milestone/knowledge extraction |
| `blueprint_synth` | strong | `migite-blueprint` synthesis |
| `audit_area` | fast | `migite-audit` per-layer auditors |
| `audit_synth` | standard | `migite-audit` report synthesis |
| `pr_review` | standard | `migite-pr-review` reviewers |
| `pr_verdict` | strong | `migite-pr-review` verdict |

Changing a tier moves every role in it; pinning a role moves only that call. An unknown role
name under `roles:` is an error.

<a id="stack"></a>
### `stack`

```yaml
stack: auto             # auto | rails | generic   (MIGITE_STACK; --stack on the command line wins)
```

`auto` runs the detection in `helpers.sh` (`Gemfile` at the root or one level down → `rails`,
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
  interactive: bypassPermissions   # implement / fix / PR-description sessions (run_phase)
  heal: bypassPermissions          # the auto-heal loop's headless fixes
  headless: none                   # plan, review, knowledge, amendments, standalone tools
```

Values: `bypassPermissions`, `acceptEdits`, `default`, `plan`, or `none` (don't pass
`--permission-mode` at all, so the CLI's own default applies). `headless` replaces the old
partial `MIGITE_PERMISSION_MODE` behaviour: it now applies uniformly to every headless call in
every tool. The env var still works and maps onto this key. The Jira fetch keeps its own
explicit mode and scoped tool allowlist regardless.

<a id="heal"></a>
### `heal`

```yaml
heal:
  max_attempts: 3             # MAX_HEAL_ATTEMPTS — Phase 2.5 fix-loop cap
  full_suite_fallback: true   # Phase 3: run the whole rspec suite when no spec files changed
```

`full_suite_fallback: false` makes Phase 3 consistent with Phase 2.5 (skip rspec, say so) instead
of running the full suite, which needs a live DB and verifies nothing about the diff.

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

`migite_config.py` is the single resolver. `migite` calls `load_migite_config` (`migite.d/config.sh`)
right after the repo root is known; it `eval`s `migite_config.py env`, which prints one
`MIGITE_CFG_<KEY>=value` assignment per leaf plus `MIGITE_CFG_MODEL_<ROLE>` for every resolved
role, then maps them onto the variables the phases already read (`DEV_LOG_BASE`, `LOG_DIR`, ...).
Phases use `cfg <key>`, `cfg_model <role>`, `prompt_path <name>`, `template_path <name>`.

The Python agents and standalone tools call `migite_config.load(repo_root)` themselves, so they
work identically when invoked directly. `migite_paths.detect_org` consults `vault.org` after the
`MIGITE_ORG` env var. `migite_claude.configure_from(cfg)` applies timeouts and the headless
permission mode to every subsequent model call.
