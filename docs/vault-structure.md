# Vault structure

The vault root is `vault.base` in the config (`DEV_LOG_BASE` env var, default `~/dev-log`).
Plain markdown and JSON; point it at an Obsidian vault if you want `[[wikilinks]]` to resolve.

```
<vault.base>/
├── <org>/                                          ← name of the directory that directly contains the repo (or $MIGITE_ORG)
│   └── <repo-name>/
│       ├── knowledge.md                          ← one file per repo, all lessons
│       ├── audit-<date>.md                       ← migite-audit, no --jira given
│       ├── pr-review-<branch>-<date>.md          ← migite-pr-review, no --jira given
│       ├── exploration-<slug>-<date>/            ← migite-explore, no --jira/--name given
│       │   ├── exploration.md
│       │   ├── challenges.md
│       │   └── intake-NN-<slug>.md               ← only with --intakes
│       └── <ticket>/                             ← grouped by --jira, shared across tools
│           ├── index.md                          ← generated after every run: current docs + one row per run (its summary line)
│           ├── plan.md  +  plan.json             ← the living plan, updated at the end of every run
│           ├── testing-plan.md                   ← current; regenerated in full on every --amend and fix round
│           ├── pr-description.md                 ← current; regenerated at the end of every run
│           ├── 00-build/                         ← the original build
│           │   ├── plan.md                       ← the plan as approved at the gate, never changed
│           │   ├── intake.md
│           │   ├── task.md                       ← optional, from the --intake supplementary prompt
│           │   ├── jira-context.md               ← optional, the fetched ticket
│           │   ├── architecture-critic.md
│           │   ├── spec-implementation.md        ← optional, the TDD red phase
│           │   ├── implementation.md
│           │   ├── implementation-stage-N.md     ← --staged only
│           │   ├── fix-rN.md                     ← one per commit-gate `f` round, from 1
│           │   ├── review.md  +  review.json
│           │   ├── gate-overrides.md             ← only with gates.commit.policy: strict, on a `Y`
│           │   ├── summary.md                    ← what the run did and why, written at its end
│           │   ├── testing-plan.md               ← the testing plan as the run left it
│           │   └── usage.json  +  usage.jsonl    ← every headless model call in the run, across resumes
│           ├── 01-amend-<slug>/                  ← one per --amend; <slug> is the feedback's first words
│           │   ├── amendment.md                  ← the scoped delta
│           │   ├── implementation.md
│           │   ├── fix-rN.md, review.md + review.json, gate-overrides.md, summary.md,
│           │   ├── testing-plan.md, usage.json + usage.jsonl
│           │   └── implementation-stage-N.md     ← --staged only
│           ├── 02-amend-<slug>/
│           ├── audit-<timestamp>.md              ← migite-audit --jira <ticket>
│           ├── pr-review-<branch>-<timestamp>.md ← migite-pr-review --jira <ticket>
│           ├── pr-review-<date>.md               ← migite-pr-review, no --jira, but the
│           │                                        branch name embeds <ticket> and this
│           │                                        folder already exists
│           └── explore-<timestamp>/              ← migite-explore --jira <ticket>
│               ├── exploration.md
│               ├── challenges.md
│               └── intake-NN-<slug>.md
└── Personal/
    ├── <project-name>/
    │   └── blueprint/
    │       ├── blueprint.md              ← written by migite-blueprint
    │       ├── knowledge.md              ← seed — copy to repo vault before first migite run
    │       ├── intake-01-foundation.md   ← milestone intake files
    │       └── intake-NN-<slug>.md
    └── <repo-name>/
        └── ...
```

`migite` and the standalone tools both resolve `<org>` the same way, via `migite.paths.detect_org()`: `$MIGITE_ORG` if set, otherwise the name of the directory that directly contains the repo (`~/Code/Acme/foo` → `Acme`), falling back to `Personal` only if the repo has no meaningful parent directory. No org names are hardcoded, so this works for any team, client, or personal-project layout without configuration — set `MIGITE_ORG` only if you want to force everything from a given shell into one bucket regardless of folder name.

`migite-audit`, `migite-pr-review`, and `migite-explore` each accept `--jira` (a Jira ticket key or
Atlassian URL) to group their output under an existing ticket's folder instead of writing a flat,
disconnected file. `migite/paths.py` is the shared resolver behind this — see its module docstring
for exact match/prefix-match/ambiguous-folder rules. Folder names come from one slug rule shared
by bash (`slugify` in `lib/vault.sh`, used by `migite`) and Python (`migite.paths.slugify`, used
by the standalone tools, and exposed as `python -m migite.paths slugify <text>`): lowercase, every run
of non-alphanumerics becomes one `-`, no leading/trailing `-`, max 50 chars.
`tests/slugify_test.sh` checks the two stay identical. `--output`, when given, always wins and skips
the resolver entirely. `migite` itself does not yet use this resolver.

`migite-pr-review` additionally falls back to a ticket key embedded in the branch name itself when
no `--jira` is given (or it doesn't resolve): same `<ticket>/` folder, but only if it already
exists — see [standalone-tools.md](./standalone-tools.md#migite-pr-review) for the exact rule.

**The `<ticket>/` files above are a read-only mirror, not the source of truth.** For the main
`migite` command (not the standalone tools), every file under `<ticket>/` except `index.md` is
written and read primarily in `<repo-root>/scratchpad/<ticket>/`, which has the same layout, and
synced out to this vault path after every write so both copies stay current. Use the vault copy for reading/browsing (e.g. in Obsidian); resuming or
amending a task reads from the scratchpad, falling back to the vault mirror only if the
scratchpad copy is missing. See [Output files](./outputs.md#output-files) for the full picture.

<a id="run-folders"></a>
## Run folders, and migrating older tasks

Each run's files live in their own folder, so a later run can never overwrite an earlier one's
implementation notes, fix rounds, review or usage. When migite reads the task's history (the
implementation notes and latest review for a new amendment, every amendment for the reviewer and
the PR description), it reads every run folder in order, oldest first, from the scratchpad or,
where the scratchpad lacks a run, from the vault. Fix rounds are numbered from 1 within their run.

Tasks created before run folders kept every file at the top of `<ticket>/`. Such a task is moved
into run folders automatically the next time `migite` runs or amends it, in both the scratchpad and
the vault: `amendment-NN.md` (and `implementation-amendment-NN.md`) into `NN-amend-<slug>/`, and
every other per-run file into `00-build/`. The review links in `knowledge.md` are repointed.
Nothing is overwritten: a file whose destination already exists stays where it is, with a warning.
To migrate every task in the vault at once, without waiting for a run:

```bash
migite migrate-vault --dry-run   # list the moves
migite migrate-vault             # make them, and write each task's index.md
```

The flat layout overwrote per-run files on every `--amend`, so after migration a task's
`00-build/` can hold a later amendment's implementation notes, fix rounds or review. They are
whatever survived; migration can't tell which run wrote them.
