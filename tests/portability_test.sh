# tests/portability_test.sh — helpers that used to be macOS-only
#
# stamp_file used `sed -i ''` (BSD spelling; GNU sed rejects it), the amend
# picker used `stat -f '%m %N'` (BSD; GNU stat uses -c). Both now go through
# portable helpers. These run the real helpers against fixtures.

# ── stamp_file ───────────────────────────────────────────────────────────────
sf_dir=$(mktemp -d); CLEANUP_DIRS+=("$sf_dir")
f="$sf_dir/plan.md"
printf '# Plan\n\nbody\n' > "$f"

stamp_file "$f"
check "stamp_file: prepends created/updated frontmatter on first stamp" \
  bash -c 'head -4 "$1" | grep -q "^created: " && head -4 "$1" | grep -q "^updated: "' _ "$f"
check "stamp_file: original body preserved after first stamp" \
  grep -qx 'body' "$f"

# Simulate an older stamp, then re-stamp: only `updated:` should move
sed "s/^updated: .*/updated: 2000-01-01/" "$f" > "$f.tmp" && mv "$f.tmp" "$f"
stamp_file "$f"
check "stamp_file: re-stamp bumps updated: to today" \
  grep -qx "updated: $DATE" "$f"
check "stamp_file: re-stamp does not add a second frontmatter block" \
  test "$(grep -c '^created: ' "$f")" = "1"
check "stamp_file: missing file is a silent no-op" \
  stamp_file "$sf_dir/does-not-exist.md"

# ── recent_task_dirs ─────────────────────────────────────────────────────────
rt_dir=$(mktemp -d); CLEANUP_DIRS+=("$rt_dir")
mkdir -p "$rt_dir/oldest" "$rt_dir/middle" "$rt_dir/newest"
touch -t 202001010000 "$rt_dir/oldest"
touch -t 202101010000 "$rt_dir/middle"
touch -t 202201010000 "$rt_dir/newest"
echo 'flat file' > "$rt_dir/audit-2026-01-01.md"

got=$(recent_task_dirs "$rt_dir")
check "recent_task_dirs: newest first" \
  test "$(printf '%s\n' "$got" | head -1)" = "newest"
check "recent_task_dirs: oldest last" \
  test "$(printf '%s\n' "$got" | tail -1)" = "oldest"
check "recent_task_dirs: directories only (flat files excluded)" \
  not bash -c 'printf "%s\n" "$1" | grep -q audit' _ "$got"
check "recent_task_dirs: honours the limit" \
  test "$(recent_task_dirs "$rt_dir" 2 | wc -l | tr -d ' ')" = "2"
check "recent_task_dirs: basenames only (no path, no trailing slash)" \
  not bash -c 'printf "%s\n" "$1" | grep -q "/"' _ "$got"
check "recent_task_dirs: missing parent is an empty, successful result" \
  test -z "$(recent_task_dirs "$rt_dir/nope")"

# ── notify ───────────────────────────────────────────────────────────────────
check "notify: never fails the run, whatever the platform" \
  notify "test" "message"
