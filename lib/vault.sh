#!/usr/bin/env bash
# lib/vault.sh - the vault mirror and the names of things in it.
#
# The scratchpad is the source of truth for a run; the vault (DEV_LOG_BASE) is
# the read-only mirror for browsing. slugify names task folders (byte-for-byte
# compatible with migite_paths.slugify - tests/slugify_test.sh checks parity),
# stamp_file/sync_* keep the mirror current, resume_from_vault recovers a run
# whose scratchpad is gone. Expects $DATE.

# slugify <text> — lowercase, every run of non-[a-z0-9] becomes one "-",
# no leading/trailing "-", max 50 chars, and no trailing "-" left by the cut.
# This is the CANONICAL slug definition: migite_paths.slugify (Python) is
# kept byte-for-byte compatible and tests/slugify_test.sh checks parity, so
# vault folders created by `migite` (bash) and looked up by the standalone
# tools (Python) always agree. There used to be four implementations that
# disagreed on "_" and "+".
slugify() {
  echo "$1" \
    | tr '[:upper:]' '[:lower:]' \
    | sed 's/[^a-z0-9]/-/g' \
    | sed 's/--*/-/g' \
    | sed 's/^-//' \
    | sed 's/-$//' \
    | cut -c1-50 \
    | sed 's/-$//'
}

# recent_task_dirs <parent> [n=10] — basenames of the n most recently modified
# subdirectories of <parent>, newest first. `ls -td` is the portable way to
# sort by mtime; the previous `find -exec stat -f '%m %N'` was BSD-only
# (GNU stat uses -c) and broke the amend picker on Linux.
recent_task_dirs() {
  local parent="$1" n="${2:-10}"
  [[ -d "$parent" ]] || return 0
  # shellcheck disable=SC2012
  ls -1td "$parent"/*/ 2>/dev/null | head -n "$n" | while IFS= read -r d; do
    basename "${d%/}"
  done
}

# stamp_file <file> — prepend created/updated frontmatter, or bump updated if already present
stamp_file() {
  local file="$1"
  # `return 0`, not bare `return`: a bare return inherits the failed [[ -f ]]
  # status (1), which under migite's `set -e` would abort the whole run.
  [[ -f "$file" ]] || return 0
  if grep -q "^created:" "$file" 2>/dev/null; then
    # tmp + mv rather than `sed -i`: BSD sed wants `-i ''`, GNU sed wants `-i`
    # with no argument, and there is no spelling both accept.
    local tmp
    tmp=$(mktemp)
    sed "s/^updated: .*/updated: $DATE/" "$file" > "$tmp" && mv "$tmp" "$file"
  else
    local tmp
    tmp=$(mktemp)
    { printf -- '---\ncreated: %s\nupdated: %s\n---\n\n' "$DATE" "$DATE"; cat "$file"; } > "$tmp"
    mv "$tmp" "$file"
  fi
}

# sync_artifact <file> <vault-dest-path>
# Stamps <file> (the scratchpad-primary copy) with frontmatter, then copies it to
# <vault-dest-path>. Used after every scratchpad-side artifact write (plan/review/
# implementation/PR/knowledge/amendments) so the vault mirror (for reading/browsing,
# e.g. in Obsidian) stays current with the scratchpad (source of truth for the run).
sync_artifact() {
  local file="$1" vault_dest="$2"
  stamp_file "$file"
  mkdir -p "$(dirname "$vault_dest")"
  cp "$file" "$vault_dest"
}

# sync_json <file.json> <vault-dest> — mirror a JSON artifact to the vault.
# NOT sync_artifact: stamp_file would prepend markdown frontmatter and corrupt it.
sync_json() {
  local file="$1" vault_dest="$2"
  [[ -f "$file" ]] || return 0
  mkdir -p "$(dirname "$vault_dest")"
  cp "$file" "$vault_dest"
}

# resume_from_vault <scratchpad-path> <vault-path>
# If the scratchpad copy is missing but a vault copy exists (scratchpad was cleaned,
# fresh clone, different machine), pulls the vault copy in so resuming a task doesn't
# silently start over. No-op if the scratchpad copy already exists or there's nothing
# in the vault to recover.
resume_from_vault() {
  local scratch="$1" vault="$2"
  [[ -f "$scratch" || ! -f "$vault" ]] && return 0
  cp "$vault" "$scratch"
}
