# tests/detect_base_branch_test.sh — lib/stack.sh:detect_base_branch
#
# origin/HEAD is authoritative when set; otherwise migite falls back to
# whichever of main/master/develop exists locally, and to a hardcoded "main"
# when neither exists — that fallback was a real 2026-08-05 bug
# (docs/improvements.md) when it silently returned empty instead.

repo=$(make_fixture_repo)
git -C "$repo" symbolic-ref refs/remotes/origin/HEAD refs/remotes/origin/main
check "origin/HEAD set -> its branch, stripped of the origin/ prefix" \
  test "$(cd "$repo" && detect_base_branch)" = "main"

repo=$(make_fixture_repo)
git -C "$repo" commit -q --allow-empty -m init
git -C "$repo" branch -m master
check "no origin/HEAD, only master exists -> master" \
  test "$(cd "$repo" && detect_base_branch)" = "master"

repo=$(make_fixture_repo)
git -C "$repo" commit -q --allow-empty -m init
git -C "$repo" branch -m master
git -C "$repo" checkout -q -b main
check "no origin/HEAD, both main and master exist -> main takes priority" \
  test "$(cd "$repo" && detect_base_branch)" = "main"

repo=$(make_fixture_repo)
check "no origin/HEAD, no local branches (fresh repo) -> falls back to main, not empty" \
  test "$(cd "$repo" && detect_base_branch)" = "main"
