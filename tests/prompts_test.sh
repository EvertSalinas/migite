# tests/prompts_test.sh — prompts/*.md drift guards
#
# The four phase prompts used to live in ~/.claude/commands/ and were read
# with "if exists else ''", so a missing file silently removed the plan/review
# format from the pipeline. They also accumulated instructions that only make
# sense in an interactive session (run brakeman, `git diff main`, write to a
# hardcoded vault path, fetch Jira via MCP) — none of which a headless
# `claude --print` call can do. These checks keep both problems from coming back.

prompts="$REPO_ROOT/prompts"

for p in plan implement review architecture_critic; do
  check "prompts/$p.md exists and is non-empty" test -s "$prompts/$p.md"
done

# Nothing user- or machine-specific baked in
check "prompts: no hardcoded home paths" \
  not grep -rqE '/Users/|MasterVault|~/Documents' "$prompts"
check "prompts: no org-specific vault bucket names" \
  not grep -rq 'Apptegy|Personal' "$prompts"

# Nothing the headless pipeline can't honour
check "prompts: no brakeman (never part of migite)" \
  not grep -rqi 'brakeman' "$prompts"
check "prompts: plan/review don't instruct running rubocop or rspec (migite runs them)" \
  not grep -qE 'Run `bundle exec (rubocop|rspec)' "$prompts/plan.md" "$prompts/review.md"
check "prompts: no Jira MCP fetch instruction (headless calls have no tool access; plan.sh pre-fetches)" \
  not grep -rq 'mcp__claude_ai_Atlassian' "$prompts"
check "prompts: no hardcoded 'git diff main' (base branch is auto-detected)" \
  not grep -rq 'git diff main' "$prompts"

# The stub failure mode: a confirmation sentence instead of the document
check "prompts/plan.md: no 'written to Obsidian' confirmation line (this WAS the observed stub output)" \
  not grep -qi 'written to Obsidian' "$prompts/plan.md"

# Contracts other code depends on
check "prompts/implement.md: keeps the [PLAN_PATH] placeholder implement.sh/plan.sh substitute" \
  grep -qF '[PLAN_PATH]' "$prompts/implement.md"
check "prompts/review.md: specifies the one-line '## Verdict:' heading review_verdict() parses" \
  grep -qE '^## Verdict: (READY TO COMMIT|NEEDS FIXES)' "$prompts/review.md"
check "prompts/architecture_critic.md: keeps the exact clean signal refine_plan() matches on" \
  grep -qF 'No architectural concerns found.' "$prompts/architecture_critic.md"

# A review written in the prompt's own format must parse correctly
sample=$(mktemp); CLEANUP_DIRS+=("$sample")
printf '# Review: sample\nDate: 2026-09-22\n\n## Verdict: NEEDS FIXES\n\nOne critical.\n\n## Checks\n- Rubocop: clean\n- RSpec: 12 examples, 0 failures\n' > "$sample"
check "prompts/review.md format: verdict-first document parses as needs_fixes" \
  test "$(review_verdict "$sample")" = "needs_fixes"
