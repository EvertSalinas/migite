# tests/tree_fingerprint_test.sh - lib/stack.sh:tree_fingerprint, the check
# that lets review reuse auto-heal's rspec run when the code hasn't changed.

tf_repo=$(make_fixture_repo)
( cd "$tf_repo" && echo base > a.rb && git add a.rb && git commit -qm base )
( cd "$tf_repo" && echo changed > a.rb )
tf_first=$(cd "$tf_repo" && tree_fingerprint HEAD)
check "tree_fingerprint: the same tree gives the same fingerprint" \
  test "$tf_first" = "$(cd "$tf_repo" && tree_fingerprint HEAD)"
( cd "$tf_repo" && mkdir -p scratchpad/bb-1 && echo notes > scratchpad/bb-1/implementation.md )
check "tree_fingerprint: migite's own scratchpad/ doesn't count" \
  test "$tf_first" = "$(cd "$tf_repo" && tree_fingerprint HEAD)"
( cd "$tf_repo" && echo "new spec" > a_spec.rb )
tf_untracked=$(cd "$tf_repo" && tree_fingerprint HEAD)
check "tree_fingerprint: a new untracked file changes it" \
  test "$tf_first" != "$tf_untracked"
( cd "$tf_repo" && echo "edited spec" > a_spec.rb )
check "tree_fingerprint: editing an untracked file changes it" \
  test "$tf_untracked" != "$(cd "$tf_repo" && tree_fingerprint HEAD)"
( cd "$tf_repo" && echo "changed again" > a.rb )
check "tree_fingerprint: editing a tracked file changes it" \
  test "$tf_first" != "$(cd "$tf_repo" && tree_fingerprint HEAD)"
out=$(cd "$tf_repo" && (set -euo pipefail; tree_fingerprint HEAD >/dev/null; echo completed))
check "tree_fingerprint: survives migite's set -euo pipefail" \
  test "$out" = "completed"
unset tf_repo tf_first tf_untracked out
