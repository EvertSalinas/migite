# tests/explore_reextract_test.sh - migite-explore --from-exploration must hand the Python
# entry the repo root whenever it runs inside a repo, so the repo's .migite.yml (backend,
# models) applies. Without it the re-extract call ran on the personal config alone.

rx_dir=$(mktemp -d); CLEANUP_DIRS+=("$rx_dir")
mkdir -p "$rx_dir/repo" "$rx_dir/outside"
( cd "$rx_dir/repo" && git init -q . )
rx_repo=$(cd "$rx_dir/repo" && pwd -P)
: > "$rx_dir/exploration.md"

# A stand-in interpreter that records its argv instead of running migite.
cat > "$rx_dir/fake-python" <<'PY'
#!/usr/bin/env bash
printf '%s\n' "$@" > "$RX_ARGS_FILE"
PY
chmod +x "$rx_dir/fake-python"

# rx_args <cwd> <migite-explore args...> - the argv the Python entry received, one per line.
rx_args() {
  local cwd="$1"; shift
  rm -f "$rx_dir/args"
  ( cd "$cwd" && RX_ARGS_FILE="$rx_dir/args" MIGITE_PYTHON="$rx_dir/fake-python" \
      bash "$MIGITE_HOME/bin/migite-explore" "$@" >/dev/null 2>&1 )
  cat "$rx_dir/args" 2>/dev/null
}

out=$(rx_args "$rx_dir/repo" --from-exploration "$rx_dir/exploration.md")
check "re-extract inside a repo passes --repo-root" \
  bash -c '[[ "$1" == *$'"'"'--repo-root\n'"'"'"$2"* ]]' _ "$out" "$rx_repo"
check "re-extract without --jira passes no --jira" \
  bash -c '[[ "$1" != *--jira* ]]' _ "$out"

out=$(rx_args "$rx_dir/repo" --from-exploration "$rx_dir/exploration.md" --jira BB-1)
check "re-extract with --jira passes --repo-root and --jira" \
  bash -c '[[ "$1" == *--repo-root* && "$1" == *$'"'"'--jira\nBB-1'"'"'* ]]' _ "$out"

out=$(rx_args "$rx_dir/outside" --from-exploration "$rx_dir/exploration.md")
check "re-extract outside a repo still runs, with no --repo-root" \
  bash -c '[[ "$1" == *--from-exploration* && "$1" != *--repo-root* ]]' _ "$out"
