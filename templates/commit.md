# /commit — Generate PR description

Review has been approved. Generate a PR description based on the work done.

## Step 1 — Final sanity check
- Run `git status` — confirm you're on the feature branch, not main
- Run `git diff --stat` — summarize what changed

## Step 2 — Write the PR description

Use the template below, filled in from the plan, implementation notes, and review.

Rules:
- Title format: `[JIRA-KEY] Short description` (max 72 chars, imperative mood)
- "What, why" — link to the Jira ticket and explain the problem being solved
- "How" — explain the technical approach chosen and why
- "Testing Instructions" — derive from the test plan in the plan.md
- "Deploy Notes" — flag migrations, env vars, feature flags, or background jobs; omit section if none
- curl examples from the plan go under Testing Instructions if present

```markdown
*Reminder: Add Jira Key to PR title: `[THRILL-XXXX] Title goes here`

## What, why

[Title](https://apptegy.atlassian.net/browse/<jira-ticket-id>)

<!-- describe the problem and why this change is needed -->

## How

<!-- describe the technical approach and key decisions made -->

## Demo

<!-- Before and after screenshots/gifs. Remove if not applicable. -->

### Before

### After

## Testing Instructions

<!-- How to test. Include curl examples if applicable. -->

## Deploy Notes

<!-- Migrations, env vars, background jobs. Remove if not applicable. -->

## Checklist
Make sure that all of these items are checked off before adding code reviewers to the PR.

- [ ] Self-reviewed my code in GH
- [ ] Added relevant GH labels (WIP, reviewable, qa pending, reviewed, ready to merge, qa env, etc)
- [ ] Ran `rubocop` and fixed any linting issues
- [ ] Ran `brakeman` and fixed any linting issues
- [ ] Created unit tests which cover the Acceptance Criteria covered in this ticket
- [ ] Ran `rspec` and made sure that all tests are passing
- [ ] Confirmed that the testing instructions are working
- [ ] Confirmed that the status checks are passing. If the checks are not passing due to Jenkins/delay issues, specify it in comments for this PR

After approval and before merging:
- [ ] Rebase/squash commits to clean up history
- [ ] Ask for re-review if necessary
```

Write the completed PR description to `[PR_FILE]`.
