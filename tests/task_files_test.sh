# tests/task_files_test.sh - lib/vault.sh:task_files, implementation_notes_files, next_fix_round
#
# A task's artifacts live in the scratchpad (source of truth) and the vault
# mirror, and either can be missing files the other has (cleaned scratchpad,
# fresh clone). These helpers are how amend, review and the fix rounds see the
# whole history instead of only one copy, and how an amend stopped
# overwriting implementation.md and the original run's fix-r0.md.

_tf_scratch=$(mktemp -d); _tf_vault=$(mktemp -d)
CLEANUP_DIRS+=("$_tf_scratch" "$_tf_vault")

check "next_fix_round: no fix rounds yet starts at 1" \
  test "$(next_fix_round "$_tf_scratch" "$_tf_vault")" = "1"

echo "vault copy" > "$_tf_vault/amendment-01.md"
echo "scratch copy" > "$_tf_scratch/amendment-01.md"
echo "two" > "$_tf_vault/amendment-02.md"
echo "notes" > "$_tf_scratch/implementation-amendment-01.md"
_tf_amendments=$(task_files "$_tf_scratch" "$_tf_vault" '^amendment-[0-9]+\.md$')

check "task_files: a basename in both dirs is listed once" \
  test "$(printf '%s\n' "$_tf_amendments" | grep -c 'amendment-01.md')" = "1"
check "task_files: the scratchpad copy wins over the vault copy" \
  test "$(printf '%s\n' "$_tf_amendments" | head -1)" = "$_tf_scratch/amendment-01.md"
check "task_files: a file only in the vault is still listed" \
  test "$(printf '%s\n' "$_tf_amendments" | tail -1)" = "$_tf_vault/amendment-02.md"
check "task_files: basenames that don't match the pattern are left out" \
  not grep -q 'implementation-amendment' <<< "$_tf_amendments"

echo "original" > "$_tf_vault/implementation.md"
echo "notes 2" > "$_tf_vault/implementation-amendment-02.md"
echo "stage" > "$_tf_vault/implementation-stage-1.md"
_tf_notes=$(implementation_notes_files "$_tf_scratch" "$_tf_vault")
_tf_expected="$_tf_vault/implementation.md
$_tf_scratch/implementation-amendment-01.md
$_tf_vault/implementation-amendment-02.md"

check "implementation_notes_files: original build first, then amendments in order, no stage files" \
  test "$_tf_notes" = "$_tf_expected"

echo "r0" > "$_tf_vault/fix-r0.md"
echo "r1" > "$_tf_vault/fix-r1.md"
check "next_fix_round: continues past the highest round already in the vault" \
  test "$(next_fix_round "$_tf_scratch" "$_tf_vault")" = "2"

echo "r9" > "$_tf_scratch/fix-r9.md"
echo "r10" > "$_tf_scratch/fix-r10.md"
echo "not a round" > "$_tf_scratch/fix-r11-notes.md"
check "next_fix_round: compares numerically across both dirs and ignores non-round files" \
  test "$(next_fix_round "$_tf_scratch" "$_tf_vault")" = "11"

unset _tf_scratch _tf_vault _tf_amendments _tf_notes _tf_expected
