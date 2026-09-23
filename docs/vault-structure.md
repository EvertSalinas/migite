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
│           ├── intake.md
│           ├── task.md                           ← optional, from the --intake supplementary prompt
│           ├── jira-context.md                   ← optional, the fetched ticket
│           ├── plan.md  +  plan.json
│           ├── amendment-NN.md                   ← one per --amend; plan.md is never rewritten
│           ├── testing-plan.md                   ← regenerated in full on every --amend and fix round
│           ├── architecture-critic.md
│           ├── implementation.md
│           ├── fix-rN.md                         ← one per commit-gate `f` round
│           ├── review.md  +  review.json
│           ├── gate-overrides.md                 ← only with gates.commit.policy: strict, on a `Y`
│           ├── usage.json                        ← every headless model call in the run, by model
│           ├── pr-description.md
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
`migite` command (not the standalone tools), every file under `<ticket>/` — `plan.md`,
`testing-plan.md`, `architecture-critic.md`, `implementation.md`, `review.md`,
`pr-description.md`, `amendment-NN.md`, `intake.md`, `task.md` — is written and read primarily in
`<repo-root>/scratchpad/<ticket>/`, and synced out to this vault path after every write so both
copies stay current. Use the vault copy for reading/browsing (e.g. in Obsidian); resuming or
amending a task reads from the scratchpad, falling back to the vault mirror only if the
scratchpad copy is missing. See [Output files](./outputs.md#output-files) for the full picture.
