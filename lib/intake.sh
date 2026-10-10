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

# build_knowledge_injection <knowledge-file> [<task-file>...]
# Renders the "repository conventions" block injected into Plan/Implement/Amend prompts:
# up to knowledge.inject_max_bytes of entries (migite/knowledge.py), printed newest first.
# With knowledge.select: relevant the entries sharing the most words with the task files
# (the intake, and jira-context.md: a --jira intake is often the bare template) go in
# first; with recent, or no task file, the newest do. Missing or empty task files are
# skipped. The whole file grew by an entry every run and went into every one of those
# prompts. Echoes nothing if the file doesn't exist yet or has no entries.
build_knowledge_injection() {
  local file="$1" lessons task_file
  shift
  [[ -f "$file" ]] || return 0
  local args=(relevant --file "$file" --max-bytes "$(cfg knowledge.inject_max_bytes 8000)")
  if [[ "$(cfg knowledge.select relevant)" == "relevant" ]]; then
    for task_file in "$@"; do
      if [[ -s "$task_file" ]]; then args+=(--keywords-file "$task_file"); fi
    done
  fi
  lessons=$("$MIGITE_PYTHON" -m migite.knowledge "${args[@]}" 2>/dev/null || true)
  [[ -n "$lessons" ]] || return 0
  printf '## Repository conventions and past lessons\n\nThe following lessons were captured from previous tasks in this repo, newest first. Adhere to them strictly before planning or implementing anything.\n\n%s\n\n---\n\n' "$lessons"
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

# intake_branch <intake-file> - echoes the branch the intake asks to work on:
# the value of its "Branch base:" line (the templates' field) or "Branch:" line
# (the audit-generated intake), with placeholder comments and bold markers
# stripped. Echoes nothing when the field is missing, empty, or N/A.
intake_branch() {
  local line value
  line=$(grep -m1 -iE '^\**branch( base)?:' "$1" || true)
  value=$(printf '%s' "$line" | sed -E 's/^[^:]*:\**//; s/<!--.*-->//g' | xargs 2>/dev/null || true)
  [[ "$(printf '%s' "$value" | tr '[:upper:]' '[:lower:]')" == "n/a" ]] && return 0
  printf '%s' "$value"
}
