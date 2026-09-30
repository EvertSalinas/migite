#!/usr/bin/env bash
# lib/vault.sh - the vault mirror and the names of things in it.
#
# The scratchpad is the source of truth for a run; the vault (DEV_LOG_BASE) is
# the read-only mirror for browsing. slugify names task folders (byte-for-byte
# compatible with migite.paths.slugify - tests/slugify_test.sh checks parity),
# stamp_file/sync_* keep the mirror current, resume_from_vault recovers a run
# whose scratchpad is gone. Expects $DATE.

# slugify <text> — lowercase, every run of non-[a-z0-9] becomes one "-",
# no leading/trailing "-", max 50 chars, and no trailing "-" left by the cut.
# This is the CANONICAL slug definition: migite.paths.slugify (Python) is
# kept byte-for-byte compatible and tests/slugify_test.sh checks parity, so
# vault folders created by `migite` (bash) and looked up by the standalone
# tools (Python) always agree. There used to be four implementations that
# disagreed on "_" and "+".
#
# Run under LC_ALL=C so the pipeline is byte-wise: in a UTF-8 locale GNU
# sed/tr collation can treat accented letters as members of [a-z0-9], so the
# bash slug kept unicode the Python sibling (strict ASCII [^a-z0-9]+) strips.
# Byte-wise and char-wise agree here — every non-ASCII byte is non-alnum, and a
# multi-byte character is one contiguous run, same as one regex match.
slugify() {
  (
    export LC_ALL=C
    echo "$1" \
      | tr '[:upper:]' '[:lower:]' \
      | sed 's/[^a-z0-9]/-/g' \
      | sed 's/--*/-/g' \
      | sed 's/^-//' \
      | sed 's/-$//' \
      | cut -c1-50 \
      | sed 's/-$//'
  )
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

# task_files <scratch-dir> <vault-dir> <ERE> - paths of the files in either dir
# whose basename matches <ERE>, one per line, sorted by basename (C locale).
# A basename in both dirs is listed once, from the scratchpad (the source of
# truth), so a file that only survives in the vault mirror still counts.
task_files() {
  local scratch="$1" vault="$2" re="$3"
  local src f base seen=" "
  for src in "$scratch" "$vault"; do
    # An empty dir argument would glob "/*".
    [[ -n "$src" && -d "$src" ]] || continue
    for f in "$src"/*; do
      [[ -f "$f" ]] || continue
      base=$(basename "$f")
      [[ "$base" =~ $re ]] || continue
      [[ "$seen" == *" $base "* ]] && continue
      seen="$seen$base "
      printf '%s\t%s\n' "$base" "$f"
    done
  done | LC_ALL=C sort | cut -f2-
}

# next_fix_round <scratch-run-dir> <vault-run-dir> - the number for the next
# fix-r<N>.md in a run folder: one past the highest in either dir, starting at 1.
next_fix_round() {
  local last
  last=$(task_files "$1" "$2" '^fix-r[0-9]+\.md$' | sed -E 's/.*fix-r([0-9]+)\.md$/\1/' | sort -n | tail -1)
  echo $(( 10#${last:-0} + 1 ))
}

# ── Task layout ───────────────────────────────────────────────────────────────
# A task folder (<vault>/<org>/<repo>/<task>/, mirrored at <repo>/scratchpad/<task>/)
# keeps the documents that describe the task as it stands now at its top level:
# plan.md + plan.json, testing-plan.md, pr-description.md, and in the vault a
# generated index.md. Everything a run produces goes in that run's own folder:
# 00-build/ for the original build, NN-amend-<slug>/ for each --amend. Runs never
# overwrite one another's files. With the older flat layout, every --amend
# overwrote implementation.md, review.md and the fix rounds.

BUILD_RUN_SLUG="00-build"
RUN_DIR_RE='^[0-9][0-9]+-(build|amend-[a-z0-9-]+)$'

# task_run_slugs <scratch-dir> <vault-dir> - the run folder names found in
# either dir, oldest run first.
task_run_slugs() {
  local src d base seen=" "
  for src in "$1" "$2"; do
    [[ -n "$src" && -d "$src" ]] || continue
    for d in "$src"/*/; do
      [[ -d "$d" ]] || continue
      base=$(basename "$d")
      [[ "$base" =~ $RUN_DIR_RE ]] || continue
      [[ "$seen" == *" $base "* ]] && continue
      seen="$seen$base "
      echo "$base"
    done
  done | LC_ALL=C sort -n
}

# task_run_files <scratch-dir> <vault-dir> <name> - <name> from every run
# folder that has it, oldest run first. The scratchpad copy wins when both
# have it; a run that only survives in the vault mirror still counts.
task_run_files() {
  local scratch="$1" vault="$2" name="$3" run
  while IFS= read -r run; do
    [[ -n "$run" ]] || continue
    if [[ -f "$scratch/$run/$name" ]]; then
      echo "$scratch/$run/$name"
    elif [[ -f "$vault/$run/$name" ]]; then
      echo "$vault/$run/$name"
    fi
  done < <(task_run_slugs "$scratch" "$vault")
}

# next_amend_num <scratch-dir> <vault-dir> - the two-digit number for the next
# --amend run: one past the highest run folder (00-build is 00), so 01 first.
next_amend_num() {
  local last
  last=$(task_run_slugs "$1" "$2" | sed -E 's/^([0-9]+)-.*/\1/' | sort -n | tail -1)
  printf '%02d' $(( 10#${last:-0} + 1 ))
}

# amend_run_slug <num> <feedback> - an amend run's folder name: NN-amend- plus
# the first six words of the feedback, slugified and capped at 40 characters.
amend_run_slug() {
  local words slug
  words=$(printf '%s\n' "$2" | tr -s '[:space:]' ' ' | sed 's/^ //' | cut -d' ' -f1-6)
  slug=$(slugify "$words" | cut -c1-40 | sed 's/-$//')
  echo "$1-amend-${slug:-change}"
}

# set_run_paths <run-slug> - point the per-run and current-state paths at the
# task layout and create the run folder in both places. Expects SCRATCHPAD_DIR
# and TASK_DIR; sets RUN_SLUG, RUN_SCRATCH_DIR, RUN_VAULT_DIR, PLAN_FILE,
# PLAN_VAULT, TESTING_PLAN_FILE, TESTING_PLAN_VAULT, IMPLEMENTATION_FILE,
# IMPLEMENTATION_VAULT, REVIEW_FILE, REVIEW_VAULT.
# SC2034: these are globals the phase files read; shellcheck lints one file at a
# time, so it can't see the readers.
# shellcheck disable=SC2034
set_run_paths() {
  RUN_SLUG="$1"
  RUN_SCRATCH_DIR="$SCRATCHPAD_DIR/$RUN_SLUG"
  RUN_VAULT_DIR="$TASK_DIR/$RUN_SLUG"
  mkdir -p "$RUN_SCRATCH_DIR" "$RUN_VAULT_DIR"
  PLAN_FILE="$SCRATCHPAD_DIR/plan.md"
  PLAN_VAULT="$TASK_DIR/plan.md"
  TESTING_PLAN_FILE="$SCRATCHPAD_DIR/testing-plan.md"
  TESTING_PLAN_VAULT="$TASK_DIR/testing-plan.md"
  IMPLEMENTATION_FILE="$RUN_SCRATCH_DIR/implementation.md"
  IMPLEMENTATION_VAULT="$RUN_VAULT_DIR/implementation.md"
  REVIEW_FILE="$RUN_SCRATCH_DIR/review.md"
  REVIEW_VAULT="$RUN_VAULT_DIR/review.md"
}

# amendment_feedback <amendment.md> - the first non-blank line of its
# "## Feedback" section.
amendment_feedback() {
  awk '/^## Feedback/ { f = 1; next } /^## / { f = 0 } f && NF { print; exit }' "$1" 2>/dev/null
}

# Per-run files the flat layout kept at the top of a task folder.
LEGACY_RUN_FILE_RE='^((intake|task|jira-context|architecture-critic|spec-implementation|implementation|review|gate-overrides)\.md|(review|usage)\.json|implementation-stage-[0-9]+\.md|fix-r[0-9]+\.md)$'

# _migrate_move <src> <dest> <task-dir> <dry-run> - one move for
# migrate_task_layout. Never overwrites: an existing <dest> leaves <src> alone.
_migrate_move() {
  local src="$1" dest="$2" dir="$3" dry="$4"
  [[ -f "$src" ]] || return 0
  if [[ -e "$dest" ]]; then
    warn "Not moving ${src#"$dir"/}: ${dest#"$dir"/} already exists in $dir"
    return 0
  fi
  if [[ "$dry" == "true" ]]; then
    echo "    would move ${src#"$dir"/} -> ${dest#"$dir"/}"
    return 0
  fi
  mkdir -p "$(dirname "$dest")"
  mv "$src" "$dest"
  echo "    ${src#"$dir"/} -> ${dest#"$dir"/}"
}

# migrate_task_layout <scratch-dir> <vault-dir> [dry-run] - move a task that
# still uses the flat layout into run folders: amendment-NN.md (and
# implementation-amendment-NN.md) into NN-amend-<slug>/, every other per-run
# file into 00-build/. Each amendment's folder name is settled once and used in
# both dirs. Also repoints the review wikilinks in the repo's knowledge.md.
# Either dir may be empty or missing. Prints each move; a no-op on a task that
# is already migrated. Because the flat layout overwrote per-run files on every
# --amend, 00-build/ can end up holding a later run's notes, fix rounds or review.
migrate_task_layout() {
  local scratch="$1" vault="$2" dry="${3:-false}"
  local f base num run dir nums=" " dirs=()
  for dir in "$scratch" "$vault"; do
    # An empty dir argument would glob "/*".
    [[ -n "$dir" && -d "$dir" ]] && dirs+=("$dir")
  done
  [[ ${#dirs[@]} -gt 0 ]] || return 0
  for dir in "${dirs[@]}"; do
    for f in "$dir"/amendment-*.md; do
      [[ -f "$f" ]] || continue
      base=$(basename "$f")
      [[ "$base" =~ ^amendment-([0-9]+)\.md$ ]] || continue
      num="${BASH_REMATCH[1]}"
      [[ "$nums" == *" $num "* ]] || nums="$nums$num "
    done
  done
  for num in $nums; do
    run=$(task_run_slugs "$scratch" "$vault" | grep -m1 "^$num-amend-" || true)
    if [[ -z "$run" ]]; then
      f="${dirs[0]}/amendment-$num.md"
      [[ -f "$f" ]] || f="${dirs[${#dirs[@]}-1]}/amendment-$num.md"
      run=$(amend_run_slug "$num" "$(amendment_feedback "$f")")
    fi
    for dir in "${dirs[@]}"; do
      _migrate_move "$dir/amendment-$num.md" "$dir/$run/amendment.md" "$dir" "$dry"
      _migrate_move "$dir/implementation-amendment-$num.md" "$dir/$run/implementation.md" "$dir" "$dry"
    done
  done
  for dir in "${dirs[@]}"; do
    for f in "$dir"/*; do
      [[ -f "$f" ]] || continue
      base=$(basename "$f")
      [[ "$base" =~ $LEGACY_RUN_FILE_RE ]] || continue
      _migrate_move "$f" "$dir/$BUILD_RUN_SLUG/$base" "$dir" "$dry"
    done
  done
  # knowledge.md links each entry to [[.../<task>/review]]; that review now
  # lives in 00-build/.
  local knowledge task tmp
  task=$(basename "$vault")
  knowledge="$(dirname "$vault")/knowledge.md"
  if [[ -n "$vault" && "$dry" != "true" && -f "$vault/$BUILD_RUN_SLUG/review.md" && -f "$knowledge" ]] \
       && grep -qF "/$task/review]]" "$knowledge"; then
    tmp=$(mktemp)
    sed "s#/$task/review]]#/$task/$BUILD_RUN_SLUG/review]]#g" "$knowledge" > "$tmp" && mv "$tmp" "$knowledge"
    echo "    knowledge.md: review links -> $BUILD_RUN_SLUG/review"
  fi
  return 0
}

# migrate_task_if_flat <scratch-dir> <vault-dir> - migrate_task_layout for the
# task a run is about to use, announced only when it moved something.
migrate_task_if_flat() {
  local out
  out=$(migrate_task_layout "$1" "$2")
  [[ -n "$out" ]] || return 0
  log "Moved $(basename "$2") into the run-folder layout (docs/vault-structure.md):"
  printf '%s\n' "$out"
}

# write_task_index <vault-task-dir> - regenerate the vault's index.md: the
# task's current documents, one row per run, and any other files (audits, PR
# reviews, explorations). Built from the files alone, no model call.
write_task_index() {
  local dir="$1" title run what verdict links f base
  [[ -d "$dir" ]] || return 0
  title=$(grep -m1 '^# ' "$dir/plan.md" 2>/dev/null | sed 's/^# //' || true)
  {
    printf '# %s\n\n' "${title:-$(basename "$dir")}"
    printf '> Generated by migite after every run. Edits here are overwritten.\n\n'
    echo "## Current"
    echo ""
    for f in plan.md testing-plan.md pr-description.md; do
      [[ -f "$dir/$f" ]] && echo "- [${f%.md}]($f)"
    done
    echo ""
    echo "## Runs"
    echo ""
    echo "| Run | What | Verdict | Files |"
    echo "|---|---|---|---|"
    while IFS= read -r run; do
      [[ -n "$run" ]] || continue
      # The run summary's one-line "Summary:" says what the run delivered; a run
      # without one (aborted, or from before summaries) says what it was for.
      what=$(grep -m1 '^Summary: ' "$dir/$run/summary.md" 2>/dev/null | sed 's/^Summary: //' || true)
      if [[ -n "$what" ]]; then
        :
      elif [[ "$run" == "$BUILD_RUN_SLUG" ]]; then
        what="Original build"
      else
        what=$(amendment_feedback "$dir/$run/amendment.md")
        what="${what:-Amendment}"
      fi
      verdict="-"
      [[ -f "$dir/$run/review.md" ]] && verdict=$(review_verdict "$dir/$run/review.md")
      links=""
      for f in "$dir/$run"/*.md; do
        [[ -f "$f" ]] || continue
        base=$(basename "$f" .md)
        links="${links:+$links, }[$base]($run/$base.md)"
      done
      printf '| %s | %s | %s | %s |\n' "$run" "${what//|/\\|}" "$verdict" "$links"
    done < <(task_run_slugs "" "$dir")
    links=""
    for f in "$dir"/*; do
      base=$(basename "$f")
      case "$base" in
        index.md|plan.md|plan.json|testing-plan.md|pr-description.md) continue ;;
      esac
      [[ -d "$f" && "$base" =~ $RUN_DIR_RE ]] && continue
      [[ -e "$f" ]] || continue
      links="$links- [$base]($base)
"
    done
    if [[ -n "$links" ]]; then
      echo ""
      echo "## Other files"
      echo ""
      printf '%s' "$links"
    fi
  } > "$dir/index.md"
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

migrate_vault_usage() {
  cat <<'USAGE'
migite migrate-vault - move task folders from the flat layout into run folders

Usage:
  migite migrate-vault [--dry-run]

Every task folder under vault.base that still keeps its per-run files at the top
level is moved into the run-folder layout:
  amendment-NN.md                     -> NN-amend-<slug>/amendment.md
  implementation-amendment-NN.md      -> NN-amend-<slug>/implementation.md
  intake.md, review.md, fix-rN.md,... -> 00-build/
plan.md, plan.json, testing-plan.md, pr-description.md and the standalone tools'
files stay where they are. Nothing is overwritten, knowledge.md's review links are
repointed, and each migrated task gets an index.md.

Only the vault is changed. A repo's scratchpad copy of a task is moved the next
time migite runs or amends that task there.

Options:
  --dry-run   list the moves without making them
  -h, --help  show this help
USAGE
}

# run_migrate_vault [--dry-run] - `migite migrate-vault`. Expects DEV_LOG_BASE.
# A task folder is <vault.base>/<org>/<repo>/<task>/ with a plan.md or
# intake.md at its top level.
run_migrate_vault() {
  local dry=false arg dir out migrated=0
  for arg in "$@"; do
    case "$arg" in
      --dry-run) dry=true ;;
      *) error "Unknown option: $arg (see: migite migrate-vault --help)" ;;
    esac
  done
  [[ -d "$DEV_LOG_BASE" ]] || error "Vault not found: $DEV_LOG_BASE (vault.base in the config)"
  for dir in "$DEV_LOG_BASE"/*/*/*/; do
    dir="${dir%/}"
    [[ -f "$dir/plan.md" || -f "$dir/intake.md" ]] || continue
    out=$(migrate_task_layout "" "$dir" "$dry")
    [[ -n "$out" ]] || continue
    migrated=$((migrated + 1))
    echo "${dir#"$DEV_LOG_BASE"/}"
    printf '%s\n' "$out"
    [[ "$dry" == "true" ]] || write_task_index "$dir"
  done
  if [[ "$migrated" -eq 0 ]]; then
    success "Nothing to migrate under $DEV_LOG_BASE"
  elif [[ "$dry" == "true" ]]; then
    log "$migrated task folder(s) would be migrated. Run without --dry-run to move them."
  else
    success "Migrated $migrated task folder(s) under $DEV_LOG_BASE"
  fi
}
