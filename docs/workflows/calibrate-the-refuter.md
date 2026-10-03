# Calibrate the refuter

Migite's reviews check themselves: after the reviewers, a second agent (the refuter) tries to
disprove every Critical before it can decide a verdict. A judge like that can be wrong in both
directions, so before you trust it, or change its model or prompt, measure it against findings
whose truth you know. This workflow builds that set of labeled findings from your own past PR
reviews and scores the refuter on it with [promptfoo](https://www.promptfoo.dev). It is offline
tooling in [`evals/`](../../evals/README.md): nothing here runs during a review.

```text
past pr-review reports
   │
   ├─ extract ─────── Critical and Warning findings, each with the commit it was raised on
   ├─ label ───────── you read the claim and the code at that commit: true or false
   ├─ eval ────────── the real refuter on every labeled finding, in a throwaway checkout
   └─ compare ─────── prompts and models side by side: accuracy, turns, cost
```

**Use it when** you want to change the `refute` role's model or prompt, when a refuted finding
turned out to be real (or a wrong Critical survived), or before trusting the refuter on a new
backend. Don't use it to review code: that is [Review a pull request](./review-a-pull-request.md).

## Why a judge needs checking

The refuter errs two ways, and they are not equally costly:

| Mistake | What happens | Is a later phase a safety net? |
|---|---|---|
| a **wrong** finding is CONFIRMED | it still blocks the merge, and `f` may "fix" something that was fine | partly: specs, rubocop and the re-review catch a fix that breaks something, not a redundant one, and `e` at the gate strikes it |
| a **true** finding is REFUTED | it is dropped from the review | no: nothing re-raises it. The appendix at the end of the report is the only trace |

So the number to watch is `false_refute`, and it should be zero. A refuter tuned only to confirm
less will look better on the first mistake and worse on the second.

## Walkthrough: the first calibration

### 1. Set up (once)

```bash
cd ~/Code/Evert/migite/evals
npm install                       # promptfoo only, pinned in package.json
```

Needs Node 20+ and the migite Python (`$MIGITE_PYTHON`, with PyYAML, and the `claude` CLI logged in).
`node_modules` is about 2 GB and git-ignored. Promptfoo is a development tool for this folder:
it is not a dependency of migite itself.

### 2. Extract candidate findings

```bash
npm run extract
```

```text
  44 findings in …/evals/golden/findings.yaml (44 new); 44 unlabeled, 44 of them have a commit to check them against
```

It reads every `pr-review-*.md` under your vault (`vault.base`), keeps the Critical and Warning
findings (Notes are not worth calibrating on), and matches each report to the checkout at
`~/Code/<org>/<repo>`. For each finding it records the branch's commit at the time of the review.
That commit is exact when the report's file name carries a time, and the start of the review's day
otherwise (`ref_approximate: true`). Re-running adds new findings and keeps every label.

The file is git-ignored on purpose: it holds findings from your private repos.

### 3. Label them

```bash
npm run label
```

```text
====================================================================================================
[1/44] pr-review-bb-1250-20261001-151652#1   CRITICAL   app/controllers/api/v1/chat/messages/insights_controller.rb:7
commit 005e4897   later commits touching the file: 0

Wrong base controller breaks `authorize` and `current_user`
InsightsController inherits from the bare ApplicationController …

     5          class InsightsController < ApplicationController
>    7            def index
…
true / false / skip / quit [t/f/s/q]:
```

You see the claim next to the code at that commit. Answer **t** if the finding is right (the code
really has this problem), **f** if it is wrong, **s** to skip. Label what is true of the code at
that commit, not what the author later did about it. Skip when the code shown is not what the
reviewer saw, which can happen with an approximate commit. Answers are saved one by one, so you
can stop with `q` and come back.

Aim for 30 to 50 labels, with a healthy share of **false** ones: a set of only true findings
cannot tell a judge that confirms everything from a good one. Keep some aside as a holdout, and
don't tune the prompt on all of them.

### 4. Run the evaluation

```bash
npm run eval
```

Each labeled finding becomes a test. The repo is extracted at its commit with `git archive`
(cached under `evals/.cache/`; your checkout is never touched), and the refuter runs with that
tree as its working directory, so its Read, Grep and Glob see the code as it was. It is the real
refuter: same prompt builder, same role, same model and effort from your config.

```text
Running 88 test cases (up to 3 at a time)...      ← 44 findings × 2 providers; yours will differ
✓ Eval complete

Results:
  ✓ …  passed
  ✗ …  failed
```

Without a filter it runs every labeled finding on every provider (step 6). While iterating,
narrow it: `npm run eval -- --filter-pattern 'bb-1250'`, matched against the finding id.

### 5. Read the result

```bash
npm run view          # the promptfoo web UI, on localhost
```

| Score | Meaning | If it is wrong |
|---|---|---|
| `correct` | confirmed a true finding, refuted a wrong one (half credit for demoting a wrong one to a Note) | |
| `false_refute` | a **true** finding was REFUTED | silently dropped from the review. Keep this at zero |
| `missed` | a true finding came back UNVERIFIABLE | demoted to a Note: it stops blocking |
| `false_confirm` | a **wrong** finding was CONFIRMED | the original problem: a wrong Critical still blocks |
| `turns` | tool calls plus the answer, averaged per provider | the cost of a prompt |

Open the failures first. A `false_refute` is the one to read in full: the refuter's reply quotes
the line it relied on, so you can see whether it found something the reviewer missed (then your
label may be wrong) or reasoned its way out of a real problem.

### 6. Compare a change

Each provider in `promptfooconfig.yaml` is one refuter setup, and promptfoo shows them side by
side on the same findings. Two ship by default:

```yaml
providers:
  - id: file://providers/refuter.py
    label: shipped prompt                # the prompt migite uses
  - id: file://providers/refuter.py
    label: focused prompt                # variants.py: stop at the first decisive line
    config:
      variant: focused
  # - id: file://providers/refuter.py    # another model: add a provider
  #   label: shipped prompt on opus
  #   config:
  #     model: claude-opus-5-5
```

Try a prompt change by adding a variant to `evals/variants.py` (it swaps the refuter prompt's
"How to work" block, `HOW_TO_WORK` in `migite/verify.py`) and a provider that names it. Try a
model with `config: {model: ...}`. Every provider multiplies the cost of a run.

### 7. Decide

A change is better only if it does not trade one error for the other:

- `false_refute` stays at zero, and `false_confirm` does not rise.
- Cheaper (`turns`, cost) is a bonus, not a reason. A variant that stops early earns its place
  only if the accuracy columns hold on the findings it did not see while you tuned it.

To ship a prompt change, edit `HOW_TO_WORK` in `migite/verify.py` and rerun the unit tests
(`python -m unittest tests/test_verify.py tests/test_evals.py`). To ship a model change, pin it
for every repo in `~/.config/migite/config.yml`:

```yaml
models:
  roles:
    refute: claude-fable-5-1      # a different, stronger model than the reviewers it checks
```

## What it costs

Every case is a real strong-tier call that reads files, so a run costs real money. On one case
(a wrong Critical about a base class), the refuter on Fable 5.1 cost $0.58, $0.43 and $0.32 over
three runs, 10 to 14 turns each, so expect about 30% noise. The focused prompt on the same case
used 5 turns and $0.17.

| Run | Roughly |
|---|---|
| one case, one provider | $0.2 to $0.6 |
| 40 labeled findings, one provider | $10 to $25 |
| the same, two providers (the default config) | $20 to $50 |

Use `--filter-pattern` while iterating, and run the full set when you have a candidate.

## Variants

### Compare models

Add a provider with `config: {model: claude-opus-5-5}` next to the shipped one, and run both on the
same findings. This is how to check that a pricier model earns its price on your code.

### Label in sittings, or relabel

`npm run label` shows only unlabeled findings. To revisit a label, edit its `label:` in
`evals/golden/findings.yaml` directly (`true`, `false`, or `null` to bring it back). `--only <text>`
restricts the labeling session to findings whose id contains the text.

### Reports in another location, repos elsewhere

```bash
python extract_candidates.py --reviews ~/reports --code-root ~/src
```

By default it reads `vault.base` from your migite config and expects repos under `~/Code/<org>/<repo>`.

### A finding whose repo or commit is missing

Findings with no `ref` (the branch no longer exists locally) are listed as unlabeled but are not
offered by `label.py` and are not tests. Fetch the branch and re-run `npm run extract`, or put a
commit in the file by hand.

## What you get

| File | Contents |
|---|---|
| `evals/golden/findings.yaml` | the labeled findings (git-ignored: private repos) |
| `evals/golden/example.yaml` | the shape of one entry, committed |
| `evals/.cache/trees/` | the repo at each commit, extracted once (git-ignored) |
| promptfoo's local store | every eval run, for `npm run view` |

Telemetry is off in the npm scripts, and nothing is shared unless you run `promptfoo share`.

## When something goes wrong

| Symptom | Fix |
|---|---|
| `No version is set for command python3` | set `MIGITE_PYTHON` to the interpreter with PyYAML (`~/.venvs/migite/bin/python3`); the scripts fall back to `python3` otherwise |
| `0 findings` from `npm run extract` | the vault has no `pr-review-*.md`, or `vault.base` is not where they are: pass `--reviews` |
| a finding has no commit | the branch isn't a local or `origin/` branch: fetch it, then re-extract |
| every case errors with a timeout | the refuter's calls are slow on a strong model: raise `models.timeouts.strong` or `roles_timeouts.refute` |
| `false_refute` on a finding you now think is wrong | fix the label (`label: false`) rather than the prompt: the set is only as good as its labels |
| results differ between runs | expected: the model is not deterministic and the cost varies about 30%. Judge a change on the whole set, not on one case |

## See also

- [evals/README.md](../../evals/README.md): the folder, file by file
- [Review a pull request](./review-a-pull-request.md): the review the refuter protects
- [phases.md](../phases.md#phase-3-review): how verification sits in the review
- [configuration.md](../configuration.md#models): the `refute` role, models and effort
