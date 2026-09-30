# tests/task_layout_test.sh - lib/vault.sh's run-folder layout: run folder
# lookup, amend numbering and naming, fix-round numbering, the flat-layout
# migration, and index.md.
#
# A task folder keeps its current documents at the top and one folder per run
# (00-build/, NN-amend-<slug>/), in the scratchpad and in the vault mirror.
# Either copy can be missing runs the other has (cleaned scratchpad, fresh
# clone), so every lookup reads both. The flat layout this replaces overwrote
# implementation.md, review.md and the fix rounds on every --amend.

_tl_scratch=$(mktemp -d); _tl_vault=$(mktemp -d)
CLEANUP_DIRS+=("$_tl_scratch" "$_tl_vault")

# ── Run folders ──────────────────────────────────────────────────────────────
check "next_amend_num: a task with no runs yet starts at 01" \
  test "$(next_amend_num "$_tl_scratch" "$_tl_vault")" = "01"

mkdir -p "$_tl_scratch/00-build" "$_tl_vault/00-build" "$_tl_vault/01-amend-reduce-delay" \
         "$_tl_scratch/02-amend-go-live-fallback" "$_tl_vault/explore-20260929" "$_tl_vault/.hidden"
echo "scratch build" > "$_tl_scratch/00-build/implementation.md"
echo "vault build" > "$_tl_vault/00-build/implementation.md"
echo "amend 1" > "$_tl_vault/01-amend-reduce-delay/implementation.md"
echo "amend 2" > "$_tl_scratch/02-amend-go-live-fallback/implementation.md"

check "task_run_slugs: run folders from both dirs, oldest first, other folders left out" \
  test "$(task_run_slugs "$_tl_scratch" "$_tl_vault" | tr '\n' ' ')" = "00-build 01-amend-reduce-delay 02-amend-go-live-fallback "
check "task_run_slugs: an empty dir argument is skipped, never globbed as /" \
  test "$(task_run_slugs "" "$_tl_vault" | tr '\n' ' ')" = "00-build 01-amend-reduce-delay "

_tl_expected="$_tl_scratch/00-build/implementation.md
$_tl_vault/01-amend-reduce-delay/implementation.md
$_tl_scratch/02-amend-go-live-fallback/implementation.md"
check "task_run_files: one file per run, oldest first, scratchpad copy wins, vault-only runs count" \
  test "$(task_run_files "$_tl_scratch" "$_tl_vault" implementation.md)" = "$_tl_expected"
check "task_run_files: runs without the file are skipped" \
  test -z "$(task_run_files "$_tl_scratch" "$_tl_vault" amendment.md)"
check "next_amend_num: one past the highest run folder in either dir" \
  test "$(next_amend_num "$_tl_scratch" "$_tl_vault")" = "03"

check "amend_run_slug: NN-amend- plus the feedback's first six words" \
  test "$(amend_run_slug 03 "Reduce the delay before the job is enqueued to 15 seconds")" = "03-amend-reduce-the-delay-before-the-job"
check "amend_run_slug: multi-line feedback and punctuation still give a clean slug" \
  test "$(amend_run_slug 04 $'  Fix N+1 on\n/districts!  ')" = "04-amend-fix-n-1-on-districts"
check "amend_run_slug: feedback with no ASCII words falls back to 'change'" \
  test "$(amend_run_slug 05 "¿¡")" = "05-amend-change"
check "amend_run_slug: the result is a folder task_run_slugs recognises" \
  bash -c '[[ "$1" =~ $2 ]]' _ "$(amend_run_slug 06 "Rename the Foo::Bar service, then update callers")" "$RUN_DIR_RE"

# ── set_run_paths ────────────────────────────────────────────────────────────
(
  # shellcheck disable=SC2034  # read by set_run_paths, not by this file
  SCRATCHPAD_DIR="$_tl_scratch"
  # shellcheck disable=SC2034  # read by set_run_paths, not by this file
  TASK_DIR="$_tl_vault"
  set_run_paths "03-amend-x"
  check "set_run_paths: creates the run folder in both places" \
    test -d "$_tl_scratch/03-amend-x" -a -d "$_tl_vault/03-amend-x"
  check "set_run_paths: per-run files go in the run folder" \
    test "$IMPLEMENTATION_FILE|$REVIEW_VAULT" = "$_tl_scratch/03-amend-x/implementation.md|$_tl_vault/03-amend-x/review.md"
  check "set_run_paths: the plan and testing plan stay at the task's top level" \
    test "$PLAN_FILE|$TESTING_PLAN_VAULT" = "$_tl_scratch/plan.md|$_tl_vault/testing-plan.md"
)

# ── Fix rounds ───────────────────────────────────────────────────────────────
check "next_fix_round: a run with no fix rounds yet starts at 1" \
  test "$(next_fix_round "$_tl_scratch/00-build" "$_tl_vault/00-build")" = "1"
echo "r9" > "$_tl_vault/00-build/fix-r9.md"
echo "r10" > "$_tl_scratch/00-build/fix-r10.md"
echo "not a round" > "$_tl_scratch/00-build/fix-r11-notes.md"
check "next_fix_round: numeric across both copies of the run folder, non-round files ignored" \
  test "$(next_fix_round "$_tl_scratch/00-build" "$_tl_vault/00-build")" = "11"
check "next_fix_round: counts only its own run folder" \
  test "$(next_fix_round "$_tl_scratch/02-amend-go-live-fallback" "$_tl_vault/02-amend-go-live-fallback")" = "1"

# ── Migration from the flat layout ───────────────────────────────────────────
_tl_root=$(mktemp -d); CLEANUP_DIRS+=("$_tl_root")
_tl_ms="$_tl_root/scratchpad/bb-1"; _tl_mv="$_tl_root/vault/Acme/api/bb-1"
mkdir -p "$_tl_ms" "$_tl_mv"
for _tl_d in "$_tl_ms" "$_tl_mv"; do
  printf '# BB-1: Export invoices\n' > "$_tl_d/plan.md"
  echo "tp" > "$_tl_d/testing-plan.md"
  echo "intake" > "$_tl_d/intake.md"
  echo "notes" > "$_tl_d/implementation.md"
  echo "review" > "$_tl_d/review.md"
  echo "r0" > "$_tl_d/fix-r0.md"
  echo "stage" > "$_tl_d/implementation-stage-1.md"
  printf '# Amendment 01\n\n## Feedback\nReduce the enqueue delay to 15 seconds.\n\n## Scope\n' > "$_tl_d/amendment-01.md"
done
echo '{}' > "$_tl_mv/usage.json"
echo "a2 notes" > "$_tl_ms/implementation-amendment-01.md"
echo "audit" > "$_tl_mv/audit-20260901.md"
printf -- '- a lesson [[dev-log/Acme/api/bb-1/review]]\n- other [[dev-log/Acme/api/bb-10/review]]\n' > "$_tl_root/vault/Acme/api/knowledge.md"

_tl_out=$(migrate_task_layout "$_tl_ms" "$_tl_mv" true)
check "migrate_task_layout: --dry-run lists the moves" \
  bash -c '[[ "$1" == *"would move review.md -> 00-build/review.md"* ]]' _ "$_tl_out"
check "migrate_task_layout: --dry-run moves nothing" \
  test -f "$_tl_mv/review.md" -a ! -e "$_tl_mv/00-build"

mkdir -p "$_tl_ms/00-build"; echo "newer" > "$_tl_ms/00-build/fix-r0.md"
_tl_out=$(migrate_task_layout "$_tl_ms" "$_tl_mv")
_tl_amend="01-amend-reduce-the-enqueue-delay-to-15"
check "migrate_task_layout: each amendment gets the same NN-amend-<slug>/ in both dirs" \
  test -f "$_tl_ms/$_tl_amend/amendment.md" -a -f "$_tl_mv/$_tl_amend/amendment.md"
check "migrate_task_layout: implementation-amendment-NN.md joins its amendment's folder" \
  test "$(cat "$_tl_ms/$_tl_amend/implementation.md")" = "a2 notes"
check "migrate_task_layout: other per-run files go to 00-build/" \
  test -f "$_tl_mv/00-build/review.md" -a -f "$_tl_mv/00-build/intake.md" -a -f "$_tl_mv/00-build/usage.json" -a -f "$_tl_ms/00-build/implementation-stage-1.md"
check "migrate_task_layout: current documents and other tools' files stay at the top" \
  test -f "$_tl_mv/plan.md" -a -f "$_tl_mv/testing-plan.md" -a -f "$_tl_mv/audit-20260901.md"
check "migrate_task_layout: an existing destination is never overwritten" \
  test "$(cat "$_tl_ms/00-build/fix-r0.md")" = "newer" -a -f "$_tl_ms/fix-r0.md"
check "migrate_task_layout: a skipped move is reported" \
  bash -c '[[ "$1" == *"Not moving fix-r0.md"* ]]' _ "$_tl_out"
check "migrate_task_layout: knowledge.md's review link now points into 00-build/" \
  grep -qF '[[dev-log/Acme/api/bb-1/00-build/review]]' "$_tl_root/vault/Acme/api/knowledge.md"
check "migrate_task_layout: another task's knowledge link is untouched" \
  grep -qF '[[dev-log/Acme/api/bb-10/review]]' "$_tl_root/vault/Acme/api/knowledge.md"
rm -f "$_tl_ms/fix-r0.md"
check "migrate_task_layout: a second pass over a migrated task moves nothing" \
  test -z "$(migrate_task_layout "$_tl_ms" "$_tl_mv")"

# ── index.md ─────────────────────────────────────────────────────────────────
printf '# Review\n\n## Verdict: NEEDS FIXES\n' > "$_tl_mv/00-build/review.md"
write_task_index "$_tl_mv"
_tl_index=$(cat "$_tl_mv/index.md")
check "write_task_index: titled from plan.md" \
  bash -c '[[ "$1" == "# BB-1: Export invoices"* ]]' _ "$_tl_index"
check "write_task_index: links the current documents" \
  bash -c '[[ "$1" == *"- [plan](plan.md)"* && "$1" == *"- [testing-plan](testing-plan.md)"* ]]' _ "$_tl_index"
check "write_task_index: one row per run with its verdict and files" \
  bash -c '[[ "$1" == *"| 00-build | Original build | needs_fixes | "*"[review](00-build/review.md)"* ]]' _ "$_tl_index"
check "write_task_index: an amend row says what the amendment asked for" \
  bash -c '[[ "$1" == *"| $2 | Reduce the enqueue delay to 15 seconds. | - | [amendment]($2/amendment.md)"* ]]' _ "$_tl_index" "$_tl_amend"
check "write_task_index: lists other files, not the current documents again" \
  bash -c '[[ "$1" == *"## Other files"*"[audit-20260901.md](audit-20260901.md)"* && "$1" != *"[plan.md]"* ]]' _ "$_tl_index"

unset _tl_scratch _tl_vault _tl_expected _tl_root _tl_ms _tl_mv _tl_d _tl_out _tl_amend _tl_index
