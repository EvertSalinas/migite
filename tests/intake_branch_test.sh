# tests/intake_branch_test.sh - lib/intake.sh:intake_branch and
# lib/stack.sh:ensure_task_branch
#
# run_plan reads the branch named on the intake's "Branch base:" line and
# checks it out, creating it from the base branch when it doesn't exist yet.

ib_dir=$(mktemp -d); CLEANUP_DIRS+=("$ib_dir")

f="$ib_dir/filled.md"
printf '**Jira:** BB-1\n\n**Branch base:** BB-1234-add-pdf\n' > "$f"
check "intake_branch: reads the value of the Branch base: field" \
  test "$(intake_branch "$f")" = "BB-1234-add-pdf"

f="$ib_dir/after-comment.md"
printf '**Branch base:** <!-- main | staging | other --> quoting-system\n' > "$f"
check "intake_branch: a value typed after the placeholder comment is kept, comment dropped" \
  test "$(intake_branch "$f")" = "quoting-system"

f="$ib_dir/placeholder.md"
printf '**Branch base:** <!-- main | staging | other -->\n' > "$f"
check "intake_branch: an untouched placeholder yields nothing" \
  test -z "$(intake_branch "$f")"

f="$ib_dir/na.md"
printf '**Branch base:** N/A\n' > "$f"
check "intake_branch: N/A yields nothing" \
  test -z "$(intake_branch "$f")"

f="$ib_dir/audit.md"
printf 'Title: Audit Remediation\nType: refactor\nBranch: feature/x\n' > "$f"
check "intake_branch: reads the plain Branch: line of an audit-generated intake" \
  test "$(intake_branch "$f")" = "feature/x"

f="$ib_dir/none.md"
printf '**Title:** something\n' > "$f"
check "intake_branch: no branch field yields nothing" \
  test -z "$(intake_branch "$f")"

# ensure_task_branch runs in a subshell per case: it cds into the fixture repo
# and sets BRANCH, neither of which may leak into the rest of the suite.
branch_repo() {
  local repo
  repo=$(make_fixture_repo)
  git -C "$repo" commit -q --allow-empty -m init
  echo "$repo"
}

repo=$(branch_repo)
got=$(cd "$repo" && ensure_task_branch "BB-1-new" "main" >/dev/null && echo "$BRANCH:$(git branch --show-current)")
check "ensure_task_branch: creates a missing branch, checks it out, and updates BRANCH" \
  test "$got" = "BB-1-new:BB-1-new"

repo=$(branch_repo)
git -C "$repo" checkout -q -b other
git -C "$repo" commit -q --allow-empty -m "other only"
(cd "$repo" && ensure_task_branch "BB-2-new" "main" >/dev/null)
check "ensure_task_branch: a new branch starts from the base branch, not the current one" \
  test "$(git -C "$repo" rev-parse BB-2-new)" = "$(git -C "$repo" rev-parse main)"

repo=$(branch_repo)
git -C "$repo" branch existing
git -C "$repo" checkout -q existing
git -C "$repo" commit -q --allow-empty -m "work in progress"
git -C "$repo" checkout -q main
existing_sha=$(git -C "$repo" rev-parse existing)
(cd "$repo" && ensure_task_branch "existing" "main" >/dev/null)
check "ensure_task_branch: an existing branch is checked out as-is" \
  test "$(git -C "$repo" branch --show-current):$(git -C "$repo" rev-parse HEAD)" = "existing:$existing_sha"

repo=$(branch_repo)
git -C "$repo" checkout -q -b current
got=$(cd "$repo" && BRANCH=current && ensure_task_branch "current" "main" 2>&1 && echo "$BRANCH")
check "ensure_task_branch: already on the branch is a silent no-op" \
  test "$got" = "current"

repo=$(branch_repo)
git -C "$repo" checkout -q -b feature
(cd "$repo" && ensure_task_branch "main" "main" >/dev/null)
check "ensure_task_branch: naming the base branch leaves the current branch alone" \
  test "$(git -C "$repo" branch --show-current)" = "feature"

repo=$(branch_repo)
(cd "$repo" && ensure_task_branch "main | staging | other" "main" >/dev/null)
check "ensure_task_branch: an invalid branch name is ignored, no branch created" \
  test "$(git -C "$repo" branch --show-current):$(git -C "$repo" branch | wc -l | xargs)" = "main:1"

repo=$(branch_repo)
(cd "$repo" && ensure_task_branch "" "main" >/dev/null)
check "ensure_task_branch: an empty name is a no-op" \
  test "$(git -C "$repo" branch --show-current):$(git -C "$repo" branch | wc -l | xargs)" = "main:1"

upstream=$(branch_repo)
git -C "$upstream" branch remote-only
git -C "$upstream" checkout -q remote-only
git -C "$upstream" commit -q --allow-empty -m "pushed work"
git -C "$upstream" checkout -q main
clone=$(mktemp -d); CLEANUP_DIRS+=("$clone")
git clone -q "$upstream" "$clone/repo"
(cd "$clone/repo" && ensure_task_branch "remote-only" "main" >/dev/null)
check "ensure_task_branch: a branch only on origin is checked out tracking it" \
  test "$(git -C "$clone/repo" rev-parse --abbrev-ref '@{upstream}')" = "origin/remote-only"
