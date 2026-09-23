# tests/build_attachments_block_test.sh — lib/intake.sh:build_attachments_block
#
# ATTACH_FILES is unset in most callers until migite's --attach flag is used —
# `${#ATTACH_FILES[@]:-0}` (an earlier version of the empty/unset guard) is
# invalid bash ("bad substitution"), not just unsafe under `set -u`. Covered
# here since that only showed up under a real `bash -c`/script invocation,
# never in an interactive shell, which is exactly how this went unnoticed
# until tested that way.

unset ATTACH_FILES
check "build_attachments_block: unset ATTACH_FILES produces no output, no error" \
  test -z "$(build_attachments_block)"

ATTACH_FILES=()
check "build_attachments_block: empty ATTACH_FILES produces no output" \
  test -z "$(build_attachments_block)"

attach_fixture=$(mktemp)
CLEANUP_DIRS+=("$attach_fixture")
printf 'field,is_client_identifying\nemail,yes\n' > "$attach_fixture"

ATTACH_FILES=("$attach_fixture")
got=$(build_attachments_block)
check "build_attachments_block: includes the attachment heading with its basename" \
  bash -c "printf '%s' \"\$1\" | grep -qx '## Attachment: $(basename "$attach_fixture")'" _ "$got"
check "build_attachments_block: includes the file's raw content" \
  bash -c "printf '%s' \"\$1\" | grep -q 'email,yes'" _ "$got"

attach_fixture_2=$(mktemp)
CLEANUP_DIRS+=("$attach_fixture_2")
printf 'second file\n' > "$attach_fixture_2"

ATTACH_FILES=("$attach_fixture" "$attach_fixture_2")
got=$(build_attachments_block)
check "build_attachments_block: multiple attachments both appear" \
  bash -c "printf '%s' \"\$1\" | grep -q 'second file'" _ "$got"

big_fixture=$(mktemp)
CLEANUP_DIRS+=("$big_fixture")
printf 'x%.0s' $(seq 1 60000) > "$big_fixture"
attach_stderr=$(mktemp)
CLEANUP_DIRS+=("$attach_stderr")

ATTACH_FILES=("$big_fixture")
got=$(build_attachments_block 2>"$attach_stderr")
check "build_attachments_block: an oversized file is truncated, not passed through whole" \
  test "${#got}" -lt 60000
check "build_attachments_block: the inline truncation marker is in the captured block" \
  bash -c "printf '%s' \"\$1\" | grep -q 'truncated, file is larger'" _ "$got"
check "build_attachments_block: the warn() message goes to stderr, not into the captured block" \
  not bash -c "printf '%s' \"\$1\" | grep -q 'truncated to.*chars'" _ "$got"
check "build_attachments_block: the warn() message is still surfaced (on stderr)" \
  grep -q 'truncated to.*chars' "$attach_stderr"

unset ATTACH_FILES
