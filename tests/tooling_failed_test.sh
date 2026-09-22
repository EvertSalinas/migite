# tests/tooling_failed_test.sh — helpers.sh:tooling_failed
#
# Each pattern here was added to tooling_failed one at a time after a real
# rubocop/rspec run passed its exit-code check while actually having failed
# to run at all (migite-improvements.md) — a clean log must stay clean.

log=$(mktemp)
CLEANUP_DIRS+=("$log")

printf 'No version is set for command rubocop\n' > "$log"
check "tooling_failed: detects missing Ruby version" tooling_failed "$log"

printf 'Bundler::GitError: some gem is not yet checked out\n' > "$log"
check "tooling_failed: detects Bundler::GitError" tooling_failed "$log"

printf '0 examples, 0 failures\nConnectionBad: could not connect to server\n' > "$log"
check "tooling_failed: detects DB connection failure (0 examples + ConnectionBad)" \
  tooling_failed "$log"

printf '0 examples, 0 failures\nLoadError: cannot load such file\n' > "$log"
check "tooling_failed: detects load error (0 examples + LoadError)" tooling_failed "$log"

printf '10 examples, 0 failures\n' > "$log"
check "tooling_failed: a clean rspec log is not flagged" not tooling_failed "$log"

printf 'Inspecting 3 files\n3 files inspected, no offenses detected\n' > "$log"
check "tooling_failed: a clean rubocop log is not flagged" not tooling_failed "$log"

check "tooling_failed: a missing log file is not flagged" not tooling_failed "/tmp/migite-tests-no-such-log-$$"
