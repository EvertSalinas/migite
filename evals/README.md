# evals: calibrating migite's refuter with promptfoo

`migite/verify.py` has a second agent (the `refute` role) try to disprove each Critical before
it reaches a review's verdict. A judge like that can be confidently wrong in both directions, so
before trusting it (or changing its model or prompt) measure it against findings whose truth you
know. That is what this folder does. It is offline tooling: nothing here runs during a review,
and none of it is part of the Python package.

```
extract_candidates.py   past pr-review reports  ->  golden/findings.yaml (unlabeled)
label.py                you read the claim and the code, answer true / false
promptfooconfig.yaml    runs the real refuter on every labeled finding and scores it
```

The step-by-step version, with the output you should see and how to decide, is the
[Calibrate the refuter](../docs/workflows/calibrate-the-refuter.md) workflow.

## Setup (once)

Needs Node 20+ and the migite Python (`$MIGITE_PYTHON`, with PyYAML and the `claude` CLI logged in).

```bash
cd evals
npm install        # promptfoo only, pinned in package.json; node_modules is ~2 GB and gitignored
```

## Build the golden set

```bash
npm run extract    # reads vault.base from your migite config; ~/Code/<org>/<repo> must hold the repos
npm run label      # q quits; every answer is saved, so you can do it in sittings
```

`label.py` shows each finding next to the code it points at, at the commit the review ran on.
Label what is true of that code, not what the author later did. Skip (`s`) when the code shown
is not what the reviewer saw. `ref_approximate: true` means the report only had a date, so the
commit is the branch at the start of that day.

Aim for 30 to 50 labeled findings with a healthy share of **false** ones: a golden set of only
true findings cannot tell a judge that confirms everything from a good one. Keep some aside as a
holdout: do not tune the refuter's prompt on all of them. The data comes from private repos, so
`golden/*.yaml` is gitignored. `golden/example.yaml` shows the shape.

## Run

```bash
npm run eval       # PROMPTFOO_PYTHON=$MIGITE_PYTHON, telemetry off, no cache (a judge must be re-asked)
npm run view       # the web UI, on localhost
npm run eval -- --filter-pattern 'bb-3709'     # a subset, matched on the finding id
```

Each case extracts the repo at the finding's commit (`git archive`, cached under `.cache/`; your
checkout is never touched) and runs the refuter with that tree as its working directory, so its
Read/Grep/Glob see the code as it was.

**Cost.** A case is a real strong-tier call that reads files. On Fable 5.1 the same case (bb-3709)
cost $0.58, $0.43 and $0.32 over three runs, so expect about 30% run-to-run noise. A full run of 40
findings is roughly $15 to $25 per provider, and `promptfooconfig.yaml` ships two providers (below),
so use `--filter-pattern` while iterating.

## Comparing prompts and models

Each provider in `promptfooconfig.yaml` is one refuter setup, and promptfoo shows them side by side
on the same findings. Two ship by default: the prompt migite uses, and a `focused` variant
(`variants.py`) that stops at the first decisive line instead of corroborating. Add a variant there,
or a provider with `config: {model: ...}`, to try another. Scores include `turns` (tool calls plus
the answer, averaged per provider), the cost of a prompt next to its accuracy.

On the bb-3709 case alone: shipped prompt 10 turns and $0.32, focused 5 turns and $0.17, both
REFUTED. One case says nothing about accuracy: a variant that stops early is only better if
`false_refute` and `false_confirm` do not rise on the full labeled set.

## Reading the result

| Score | Meaning | Cost of getting it wrong |
|---|---|---|
| `correct` | confirmed a true finding, refuted a wrong one (half for demoting a wrong one) | |
| `false_refute` | a **true** finding was refuted | it is silently dropped from the review. Keep this at zero |
| `missed` | a true finding came back UNVERIFIABLE | demoted to a Note: it stops blocking |
| `false_confirm` | a **wrong** finding was confirmed | the original problem: a wrong Critical still blocks |

Change the model (`models.roles.refute` in your migite config, or a provider's `config.model`),
the refute prompt (`refute_prompt` in `migite/verify.py`), or the tools it gets, then re-run and
compare. A change that lowers `false_confirm` by raising `false_refute` is not an improvement.
