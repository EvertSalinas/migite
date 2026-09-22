# tests/fill_intake_field_test.sh — helpers.sh:fill_intake_field
#
# The intake template's placeholders used to be filled with
# `sed -i "s|<placeholder>|$TASK|"`, which put user text inside a sed
# expression. A task containing `|` aborted the run under set -e; `&` or `\`
# silently corrupted the Title field. These fixtures hold the exact template
# lines plan.sh fills in.

intake_dir=$(mktemp -d)
CLEANUP_DIRS+=("$intake_dir")

make_intake() {
  printf '**Title:** <!-- one sentence, action-oriented: "Add PDF export to invoices endpoint" -->\n\n**Jira:** <!-- ticket ID or N/A -->\n\n**Context:**\n<!-- one sentence about the why -->\n' > "$1"
}

f="$intake_dir/plain.md"; make_intake "$f"
fill_intake_field "$f" '<!-- one sentence.*-->' "add PDF export to invoices"
check "fill_intake_field: plain text replaces the Title placeholder" \
  grep -qxF '**Title:** add PDF export to invoices' "$f"
check "fill_intake_field: only the FIRST match is replaced (Context placeholder untouched)" \
  grep -qF '<!-- one sentence about the why -->' "$f"

f="$intake_dir/pipe.md"; make_intake "$f"
fill_intake_field "$f" '<!-- one sentence.*-->' "fix a|b routing"
check "fill_intake_field: a pipe in the task text is kept literally" \
  grep -qxF '**Title:** fix a|b routing' "$f"

f="$intake_dir/amp.md"; make_intake "$f"
fill_intake_field "$f" '<!-- one sentence.*-->' "match & replace tokens"
check "fill_intake_field: an ampersand is kept literally (sed would insert the match)" \
  grep -qxF '**Title:** match & replace tokens' "$f"

f="$intake_dir/backslash.md"; make_intake "$f"
fill_intake_field "$f" '<!-- one sentence.*-->' 'escape \d in regex'
check "fill_intake_field: a backslash sequence is kept literally (awk -v would unescape it)" \
  grep -qxF '**Title:** escape \d in regex' "$f"

f="$intake_dir/jira.md"; make_intake "$f"
fill_intake_field "$f" '<!-- ticket ID or N/A -->' "https://example.atlassian.net/browse/BB-1234?focusedId=1&x=2"
check "fill_intake_field: a Jira URL with query string fills the Jira placeholder intact" \
  grep -qxF '**Jira:** https://example.atlassian.net/browse/BB-1234?focusedId=1&x=2' "$f"

f="$intake_dir/nomatch.md"; make_intake "$f"
before=$(cat "$f")
fill_intake_field "$f" '<!-- does not exist -->' "whatever"
check "fill_intake_field: no match leaves the file byte-for-byte unchanged" \
  test "$(cat "$f")" = "$before"
