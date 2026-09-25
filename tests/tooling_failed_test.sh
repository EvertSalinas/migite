# tests/tooling_failed_test.sh — lib/stack.sh:tooling_failed
#
# Each pattern here was added to tooling_failed one at a time after a real
# rubocop/rspec run passed its exit-code check while actually having failed
# to run at all (docs/improvements.md) — a clean log must stay clean.

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

# System specs whose browser never started read as N real failures; the heal
# loop must not hand those to the agent as code to fix.
printf '3 examples, 3 failures\nFerrum::BinaryNotFoundError: Could not find an executable for the browser\n' > "$log"
check "tooling_failed: detects Cuprite with no Chrome (Ferrum::BinaryNotFoundError)" tooling_failed "$log"

printf '2 examples, 2 failures\nSelenium::WebDriver::Error::SessionNotCreatedError: session not created\n' > "$log"
check "tooling_failed: detects a Selenium driver/browser mismatch" tooling_failed "$log"

printf "1 example, 1 failure\nExecutable doesn't exist at /Users/x/Library/Caches/ms-playwright/chromium-1091/chrome\n" > "$log"
check "tooling_failed: detects Playwright with no downloaded browsers" tooling_failed "$log"

printf '4 examples, 1 failure\nFerrum::TimeoutError: Timed out waiting for response\n' > "$log"
check "tooling_failed: a system spec that times out on the page is a real failure, not tooling" \
  not tooling_failed "$log"

printf '== eslint ==\neslint is configured but not installed (node_modules/.bin/eslint is missing)\n' > "$log"
check "tooling_failed: detects a configured-but-not-installed frontend linter" tooling_failed "$log"
