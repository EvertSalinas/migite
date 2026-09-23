#!/usr/bin/env bash
# lib/intake.sh - building the intake and the prompt context around it.
#
# Attachments (--attach), the repository-conventions block injected from
# knowledge.md, and the safe placeholder fill for intake templates.

# build_attachments_block — reads each path in the global ATTACH_FILES array
# (populated by migite's --attach flag) and renders it as a heading + raw
# content block, so it can be folded into plain prompt text. Attachments feed
# directly into every plan model call (synthesize/critic/refine, all headless
# agent calls), so a huge file multiplies
# token cost per call, not just once; truncated at a safety cap accordingly.
# Echoes nothing if ATTACH_FILES is empty or unset.
build_attachments_block() {
  # ${ATTACH_FILES+set} (not ${#ATTACH_FILES[@]}) so this is safe under `set -u`
  # whether the array was never declared or declared empty — both mean "no
  # attachments" here, and only the former would otherwise raise "unbound variable".
  [[ -z "${ATTACH_FILES+set}" ]] && return 0
  local max_chars=50000
  local f content
  for f in "${ATTACH_FILES[@]}"; do
    content=$(cat "$f")
    if [[ ${#content} -gt $max_chars ]]; then
      content="${content:0:$max_chars}"$'\n\n'"[... truncated, file is larger than the ${max_chars}-char cap ...]"
      # Callers capture this function's stdout via command substitution — the
      # warning must not land in that string, so it goes to stderr instead.
      warn "Attachment $(basename "$f") truncated to $max_chars chars" >&2
    fi
    printf '## Attachment: %s\n\n%s\n\n' "$(basename "$f")" "$content"
  done
}

# build_knowledge_injection <knowledge-file>
# Renders the "repository conventions" block injected into Plan/Implement/Amend prompts.
# Echoes nothing if the file doesn't exist yet.
build_knowledge_injection() {
  local file="$1"
  [[ -f "$file" ]] || return 0
  printf '## Repository conventions and past lessons\n\nThe following lessons were captured from previous tasks in this repo. Adhere to them strictly before planning or implementing anything.\n\n%s\n\n---\n\n' "$(cat "$file")"
}

# fill_intake_field <file> <regex> <value> — replaces the first match of
# <regex> (awk extended regex) in <file> with <value>, taken LITERALLY.
# Replaces the old `sed -i "s|<placeholder>|$TASK|"`, which spliced user text
# straight into a sed expression: a task description containing `|` aborted
# the run, and one containing `&` or `\` silently wrote garbage into the
# intake's Title field. The value travels through the environment, not `-v`,
# because awk -v processes backslash escapes and would mangle it too.
fill_intake_field() {
  local file="$1" pattern="$2" value="$3"
  local tmp
  tmp=$(mktemp)
  MIGITE_FILL_VALUE="$value" awk -v pat="$pattern" '
    !done && match($0, pat) {
      $0 = substr($0, 1, RSTART - 1) ENVIRON["MIGITE_FILL_VALUE"] substr($0, RSTART + RLENGTH)
      done = 1
    }
    { print }
  ' "$file" > "$tmp" && mv "$tmp" "$file"
}
