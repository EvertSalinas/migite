# Vault structure

```
~/dev-log/
├── <org>/                                          ← name of the directory that directly contains the repo (or $MIGITE_ORG)
│   └── <repo-name>/
│       ├── knowledge.md                          ← one file per repo, all lessons
│       ├── audit-<date>.md                       ← migite-audit, no --id given
│       ├── pr-review-<branch>-<date>.md          ← migite-pr-review, no --jira given
│       ├── exploration-<slug>-<date>/            ← migite-explore, no --id/--name given
│       │   ├── exploration.md
│       │   ├── challenges.md
│       │   └── intake-NN-<slug>.md               ← only with --intakes
│       └── <ticket>/                             ← grouped by --id / --jira, shared across tools
│           ├── intake.md
│           ├── task.md                           ← optional, from the --intake supplementary prompt
│           ├── plan.md
│           ├── testing-plan.md                   ← regenerated in full on every --amend
│           ├── architecture-critic.md
│           ├── implementation.md
│           ├── review.md
│           ├── pr-description.md
│           ├── audit-<timestamp>.md              ← migite-audit --id <ticket>
│           ├── pr-review-<branch>-<timestamp>.md ← migite-pr-review --jira <ticket>
│           └── explore-<timestamp>/              ← migite-explore --id <ticket>
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

`migite` and the standalone tools both resolve `<org>` the same way, via `migite_paths.detect_org()`: `$MIGITE_ORG` if set, otherwise the name of the directory that directly contains the repo (`~/Code/Acme/foo` → `Acme`), falling back to `Personal` only if the repo has no meaningful parent directory. No org names are hardcoded, so this works for any team, client, or personal-project layout without configuration — set `MIGITE_ORG` only if you want to force everything from a given shell into one bucket regardless of folder name.

`migite-audit`, `migite-pr-review`, and `migite-explore` each accept `--id`/`--jira` (a Jira ticket
key or Atlassian URL) to group their output under an existing ticket's folder instead of writing a
flat, disconnected file. `migite_paths.py` is the shared resolver behind this — see its module
docstring for exact match/prefix-match/ambiguous-folder rules. `--output`, when given, always wins
and skips the resolver entirely. `migite` itself does not yet use this resolver.
