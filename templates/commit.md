# PR description — generate from the plan, implementation notes, and review

Review has been approved. Generate a pull request description based on the work done.

## Step 1 — Final sanity check
- Run `git status` — confirm you're on the feature branch, not the base branch
- Run `git diff --stat` — summarize what changed

## Step 2 — Write the PR description

Use the template below, filled in from the plan, implementation notes, review, and any
amendments. This is the generic template shipped with migite; a team's own template goes in
`templates.dir` (see docs/configuration.md → prompts, templates) and replaces this file.

Rules:
- Title: `[<ticket key>] Short description` when a ticket is referenced, otherwise just the description (max 72 chars, imperative mood)
- "What, why" — link the ticket if there is one and explain the problem being solved
- "How" — the technical approach chosen and why, including anything that deviated from the plan
- "Testing" — derive from testing-plan.md: seed steps, the verification commands, expected results
- "Deploy notes" — migrations, env vars, feature flags, background jobs; omit the section if none
- Amendments: fold every amendment into the delivered scope; the description reflects what was built, not the original plan alone

```markdown
## What, why

<!-- Link the ticket if any. Describe the problem and why this change is needed. -->

## How

<!-- Technical approach and key decisions. Note deviations from the plan and why. -->

## Testing

<!-- From testing-plan.md: how to seed, what to run (curl / UI steps), what to expect. -->

## Deploy notes

<!-- Migrations, env vars, feature flags, background jobs. Remove if not applicable. -->

## Checklist

- [ ] Self-reviewed the diff
- [ ] Linter clean on changed files
- [ ] Tests added for the acceptance criteria and passing
- [ ] Testing instructions verified end to end
```

Write the completed PR description to `[PR_FILE]`.
