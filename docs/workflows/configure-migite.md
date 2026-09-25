# Configure migite

Migite works with no configuration at all. You configure it to make it yours (your vault, your
editor, stronger models where it pays) and to make a repo behave the same for everyone on the team
(a strict commit gate, a team PR template, a checklist in the review prompt). This workflow covers
both, and how to keep an install healthy.

```text
flags  >  env vars  >  $MIGITE_CONFIG  >  <repo>/.migite.yml  >  ~/.config/migite/config.yml  >  defaults
                                            the team's file          your personal file
```

## Walkthrough: set yourself up

### 1. Your personal defaults

```bash
migite config --edit --user
```

The first time, this writes `~/.config/migite/config.yml` with every setting at its default and a
comment on each, then opens it. Delete everything you don't change. Most people change three things:

```yaml
vault:
  base: ~/Documents/MyVault/dev-log     # where plans, reviews, and knowledge.md are mirrored
ui:
  editor: nvim
models:
  effort:
    strong: xhigh                       # more thinking on the planner, critic, and verdict
```

When you close the editor, the file is validated. A mistake is reported with the key and the file:

```text
✘ config error: gates.commit.policy must be one of lenient, strict (got 'strcit' from …/config.yml)
✘ …/config.yml has an error (above). Fix it with: migite config --edit --user
```

### 2. Check what is in effect

```bash
migite config
```

Every setting with the file (or env var) it came from, then the model every role resolves to.

### 3. Check the install

```bash
migite doctor
```

```text
✔ Stack: rails — app dir: .
ℹ Agent backend: claude
✔ Agent CLI: Claude Code at /opt/homebrew/bin/claude (2.1.280 (Claude Code))
ℹ Ticket source for --jira:
    …  → jira-acli would be used
✔ Tool resolves: git
✔ config valid (1 file(s): …/config.yml)
✔ Prompt present: prompts/plan.md
…
0 issues found.
```

Run it after installing, after upgrading your agent CLI, and whenever a run misbehaves. It changes
nothing.

## Walkthrough: set up a team repo

In the repo, create a shared file and commit it:

```bash
cd ~/Code/Acme/invoices-api
migite config --edit
```

Keep only the keys the team agrees on. A typical team file:

```yaml
gates:
  commit:
    policy: strict               # y is refused over NEEDS FIXES, failing specs, or offenses; Y overrides and is logged
heal:
  max_attempts: 2
prompts:
  dir: .migite/prompts           # team versions of any prompt; the rest fall back to migite's
templates:
  dir: .migite/templates         # the team's PR template: .migite/templates/commit.md
```

```bash
git add .migite.yml .migite/ && git commit -m "chore: team migite config"
```

Personal settings (vault, editor) stay in each person's user file; the repo file wins where both
set the same key.

## Variants

### Add the team's checklist to the review

```bash
mkdir -p .migite/prompts
cp "$(dirname "$(readlink -f "$(command -v migite)")")/../prompts/review.md" .migite/prompts/review.md
$EDITOR .migite/prompts/review.md     # add your items; keep the verdict format intact
```

Any of `plan.md`, `implement.md`, `review.md`, `architecture_critic.md` can be overridden this way;
only the files present are used.

### One PR template per organisation, without touching any repo

In your user file:

```yaml
templates:
  dir: ~/.config/migite/templates/{org}
```

```bash
mkdir -p ~/.config/migite/templates/Acme
$EDITOR ~/.config/migite/templates/Acme/commit.md
```

`{org}` is the folder containing the repo, so every repo under `~/Code/Acme/` uses Acme's template.
`{repo}` works the same way.

### Tune models and effort

```yaml
models:
  roles:
    think: claude-sonnet-5        # plan synthesis on the standard tier: cheaper, still good
  roles_effort:
    critic: max                   # the critic thinks hardest
```

`migite config` lists every role and the model it resolves to. The three tiers (`fast`,
`standard`, `strong`) move many roles at once; `roles` pins one. See
[configuration.md](../configuration.md#models).

### Cap what a run spends

```yaml
budget:
  max_usd_per_run: 5.00
```

A soft cap: every gate banner turns red once the run's headless spend passes it. Nothing is
stopped.

### Override one run from the environment

```bash
MIGITE_STACK=generic migite "…"            # stack for this run only
MAX_HEAL_ATTEMPTS=5 migite --jira BB-1234
MIGITE_CONFIG=./experiment.yml migite …    # an extra file, above the repo file
```

Environment variables beat files. The full list is in
[configuration.md](../configuration.md#env).

### Start over

```bash
migite config --init --force          # the repo file back to the starter
migite config --init --user --force   # your file back to the starter
migite config --path [--user]         # which file --edit would open
```

## Read what a run cost

Every run ends with a usage table and writes `usage.json` beside the task:

```bash
cat scratchpad/bb-1234/usage.json
```

Compare a few runs before and after a model or effort change; the cost of each role is in the
ledger lines (`MIGITE_USAGE_LEDGER`, in `logs.dir`).

## When something goes wrong

| Symptom | Fix |
|---|---|
| `✘ migite config error: …` before anything runs | `migite config --edit` opens the file even while it is broken |
| a setting doesn't take effect | `migite config` shows which file or env var won |
| `... exists but PyYAML is not installed` | `"$MIGITE_PYTHON" -m pip install pyyaml`, or rename the file to `.json` |

## See also

- [configuration.md](../configuration.md): every key, with recipes
- [Use another agent](./use-another-agent.md): the `agent` block
