#!/usr/bin/env bash
# lib/doctor.sh — `migite doctor`: a read-only, non-mutating health check.
#
# Sourced by migite. run_doctor is intentionally self-contained (parses its own
# --repo flag, resolves REPO_ROOT/ORG/REPO_NAME/DEV_LOG_BASE itself) because it
# must work even in cases the normal pipeline can't tolerate — a repo with no
# Gemfile, a missing agent CLI or `bundle`, an interrupted run leaving orphaned
# state — so it runs before migite's own Setup (`require_cmd`, mkdir, arg
# parsing) rather than assuming any of that already succeeded. Never mutates
# anything it inspects; exits 0 if clean, 1 if it found anything.
#
# Expects MIGITE_HOME, MIGITE_PYTHON, DEV_LOG_BASE and lib/common.sh's
# detect_stack/STACK_PROFILES to already be available (sourced by migite
# before this file).

# doctor_usage - `migite doctor --help`
doctor_usage() {
  cat <<'EOF'
migite doctor - read-only health check for a repo and your migite install

Usage:
  migite doctor [--repo <path>]

Checks the stack detection, the agent CLI and its version, where --jira gets tickets,
git and Python, bundle (rails stack), the configuration, the four phase prompts,
scratchpad and vault drift, orphaned sentinels, and duplicate knowledge entries.
It changes nothing, and exits 1 when it finds an issue.

  --repo <path>   check another repo (default: the current directory)
  -h, --help      show this help
EOF
}

run_doctor() {
  local doc_repo_arg="$PWD"
  while [[ $# -gt 0 ]]; do
    case "${1:-}" in
      -h|--help)
        doctor_usage
        return 0
        ;;
      --repo)
        doc_repo_arg="${2:-}"
        [[ -z "$doc_repo_arg" ]] && { echo "Provide a path after --repo" >&2; return 1; }
        shift 2
        ;;
      *)
        echo "Unknown doctor argument: $1 (see: migite doctor --help)" >&2
        return 1
        ;;
    esac
  done

  echo ""
  echo "migite doctor"
  echo ""

  if ! (cd "$doc_repo_arg" 2>/dev/null && git rev-parse --is-inside-work-tree &>/dev/null); then
    echo "✘ Not a git repo: $doc_repo_arg"
    return 1
  fi

  local issues=0
  local REPO_ROOT REPO_NAME ORG
  REPO_ROOT="$(cd "$doc_repo_arg" && git rev-parse --show-toplevel)"
  REPO_NAME="$(basename "$REPO_ROOT")"
  ORG="$("$MIGITE_PYTHON" -m migite.paths detect-org --repo-root "$REPO_ROOT" 2>/dev/null || echo "unknown")"
  local doc_dev_log_base="${DEV_LOG_BASE:-$HOME/dev-log}"

  # ── Stack detection — read-only, never errors (generic is the catch-all
  # profile, see STACK_PROFILES in lib/stack.sh).
  detect_stack
  echo "✔ Stack: $STACK — app dir: ${APP_REL_PATH:-.}"

  # ── Tool resolution ────────────────────────────────────────────────────────
  local doc_tool doc_agent_name
  doc_agent_name="$("$MIGITE_PYTHON" -m migite.agent_cli --repo-root "$REPO_ROOT" info --field name 2>/dev/null || echo claude)"
  echo "ℹ Agent backend: $doc_agent_name"
  # The adapter's own health check: binary on PATH, and its version when it answers.
  if ! "$MIGITE_PYTHON" -m migite.agent_cli --repo-root "$REPO_ROOT" check; then
    issues=$((issues + 1))
  fi
  # Where --jira gets the ticket's content from (tracker.provider), and why.
  echo "ℹ Ticket source for --jira:"
  "$MIGITE_PYTHON" -m migite.tickets --repo-root "$REPO_ROOT" sources 2>&1 | sed 's/^/    /'
  for doc_tool in git "$MIGITE_PYTHON"; do
    if command -v "$doc_tool" &>/dev/null; then
      echo "✔ Tool resolves: $doc_tool"
    else
      echo "✘ Tool does not resolve: $doc_tool"
      issues=$((issues + 1))
    fi
  done
  if [[ "$STACK" == "rails" ]]; then
    if command -v bundle &>/dev/null; then
      echo "✔ Tool resolves: bundle"
    else
      echo "✘ Tool does not resolve: bundle (required for the rails stack)"
      issues=$((issues + 1))
    fi
  fi

  # ── Configuration ──────────────────────────────────────────────────────────
  # Validates the layered config for this repo (bad YAML / invalid enum → issue;
  # unknown keys → warning) and shows which files were loaded.
  local cfg_out cfg_rc=0
  cfg_out=$("$MIGITE_PYTHON" -m migite.config --repo-root "$REPO_ROOT" validate 2>&1) || cfg_rc=$?
  if [[ $cfg_rc -eq 0 ]]; then
    printf '%s\n' "$cfg_out" | sed 's/^/  /' | sed '1s/^  ✔/✔/'
  else
    echo "✘ Config: $cfg_out"
    issues=$((issues + 1))
  fi

  # ── Phase prompts ──────────────────────────────────────────────────────────
  # plan.md / implement.md / review.md / architecture_critic.md ship in the
  # repo's prompts/ dir. migite, migite-plan and migite-review all hard-fail
  # without them; doctor surfaces the gap before a run does.
  local doc_prompt
  for doc_prompt in plan implement review architecture_critic; do
    if [[ -s "$MIGITE_HOME/prompts/$doc_prompt.md" ]]; then
      echo "✔ Prompt present: prompts/$doc_prompt.md"
    else
      echo "✘ Prompt missing or empty: prompts/$doc_prompt.md"
      issues=$((issues + 1))
    fi
  done

  # ── Scratchpad / vault sync drift ──────────────────────────────────────────
  # Every scratchpad/<slug>/*.md should have a same-or-newer counterpart under
  # the vault mirror — a mismatch means a sync_artifact call didn't happen
  # (crashed run, or a bug in a phase that forgot to sync).
  local scratchpad_root="$REPO_ROOT/scratchpad"
  local vault_root="$doc_dev_log_base/$ORG/$REPO_NAME"
  local drift=()
  if [[ -d "$scratchpad_root" ]]; then
    local task_dir
    for task_dir in "$scratchpad_root"/*/; do
      [[ -d "$task_dir" ]] || continue
      local slug fname vault_f f
      slug=$(basename "$task_dir")
      for f in "$task_dir"*.md; do
        [[ -f "$f" ]] || continue
        fname=$(basename "$f")
        vault_f="$vault_root/$slug/$fname"
        if [[ ! -f "$vault_f" ]]; then
          drift+=("$slug/$fname — no vault counterpart")
        elif [[ "$f" -nt "$vault_f" ]]; then
          drift+=("$slug/$fname — scratchpad is newer than vault (sync_artifact may not have run)")
        fi
      done
    done
  fi
  if [[ ${#drift[@]} -eq 0 ]]; then
    echo "✔ Scratchpad/vault sync: no drift found"
  else
    echo "⚠ Scratchpad/vault drift (${#drift[@]}):"
    local d
    for d in "${drift[@]}"; do
      echo "  - $d"
    done
    issues=$((issues + ${#drift[@]}))
  fi

  # ── Orphaned sentinels ──────────────────────────────────────────────────────
  # A sentinel (.plan.done / .review.done) with no corresponding non-empty
  # output file means a LangGraph run was interrupted after touching the
  # sentinel but before (or without) writing real content.
  local orphans=()
  if [[ -d "$scratchpad_root" ]]; then
    local task_dir
    for task_dir in "$scratchpad_root"/*/; do
      [[ -d "$task_dir" ]] || continue
      local slug
      slug=$(basename "$task_dir")
      if [[ -f "$task_dir/.plan.done" && ! -s "$task_dir/plan.md" ]]; then
        orphans+=("$slug/.plan.done — plan.md missing or empty")
      fi
      if [[ -f "$task_dir/.review.done" && ! -s "$task_dir/review.md" ]]; then
        orphans+=("$slug/.review.done — review.md missing or empty")
      fi
    done
  fi
  if [[ ${#orphans[@]} -eq 0 ]]; then
    echo "✔ Sentinels: none orphaned"
  else
    echo "⚠ Orphaned sentinels (${#orphans[@]}):"
    local o
    for o in "${orphans[@]}"; do
      echo "  - $o"
    done
    issues=$((issues + ${#orphans[@]}))
  fi

  # ── Knowledge duplicate-entry heuristic ─────────────────────────────────────
  # knowledge.md accumulates one bullet per run, extracted independently each
  # time — nothing stops the same lesson landing twice. Flags entries whose
  # normalized text (wikilink stripped, so different runs' citations don't
  # mask the match) repeats an earlier one. A warning, not an automatic dedup —
  # knowledge.md is meant to be eyeballed before it's trusted.
  local knowledge_file="$vault_root/knowledge.md"
  local dupes=()
  if [[ -f "$knowledge_file" ]]; then
    local seen=() line normalized prior found
    while IFS= read -r line; do
      [[ "$line" == "- "* ]] || continue
      normalized=$(echo "${line#- }" \
        | sed -E 's/\[\[[^]]*\]\]//g' \
        | tr '[:upper:]' '[:lower:]' \
        | sed -E 's/[^a-z0-9 ]//g' \
        | tr -s ' ' \
        | sed -E 's/^ +| +$//g')
      [[ -z "$normalized" ]] && continue
      found=""
      for prior in "${seen[@]}"; do
        if [[ "$prior" == "$normalized" ]]; then
          found=1
          break
        fi
      done
      if [[ -n "$found" ]]; then
        dupes+=("\"${line#- }\" repeats an earlier entry")
      else
        seen+=("$normalized")
      fi
    done < "$knowledge_file"
  fi
  if [[ ${#dupes[@]} -eq 0 ]]; then
    echo "✔ Knowledge duplicates: none found"
  else
    echo "⚠ Possible duplicate knowledge entries (${#dupes[@]}):"
    local dpe
    for dpe in "${dupes[@]}"; do
      echo "  - $dpe"
    done
    issues=$((issues + ${#dupes[@]}))
  fi

  # ── bash lib size watch (informational) ─────────────────────────────────────
  # The helpers used to be one 845-line file; they're split by concern under
  # lib/ now. Surface the largest file so a new grab-bag doesn't grow unnoticed.
  # Never adds to $issues; this can't fail doctor, only inform it.
  local lib_file lib_lines lib_total=0 lib_count=0 lib_largest="" lib_largest_lines=0
  for lib_file in "$MIGITE_HOME"/lib/*.sh "$MIGITE_HOME"/lib/phases/*.sh; do
    [[ -f "$lib_file" ]] || continue
    lib_lines=$(wc -l < "$lib_file" | tr -d ' ')
    lib_total=$((lib_total + lib_lines)); lib_count=$((lib_count + 1))
    if [[ "$lib_lines" -gt "$lib_largest_lines" ]]; then
      lib_largest_lines=$lib_lines; lib_largest="${lib_file#"$MIGITE_HOME"/}"
    fi
  done
  if [[ "$lib_largest_lines" -ge 600 ]]; then
    echo "⚠ $lib_largest is $lib_largest_lines lines — past the ~600-line split trigger, worth a look"
  else
    echo "ℹ bash lib: $lib_total lines in $lib_count files (largest: $lib_largest, $lib_largest_lines; split trigger: ~600)"
  fi

  echo ""
  if [[ $issues -eq 0 ]]; then
    echo "0 issues found."
    return 0
  else
    echo "$issues issue(s) found."
    return 1
  fi
}
