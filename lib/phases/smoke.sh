#!/usr/bin/env bash
# lib/phases/smoke.sh — Phase 3.2, the API smoke check (smoke.check: off | ask | on).
#
# Sourced by migite; run_review (lib/phases/review.sh) calls run_smoke_check after
# the browser check and before the reviewers, and again with --rerun after every
# commit-gate fix round. Everything that touches the network is python -m
# migite.smoke (detect, plan, run); bash only decides whether to run it, asks,
# and reads the results back. migite never starts a server: when nothing answers
# it asks you to start one or skips.
#
# Expects REPO_ROOT, BASE_BRANCH, STACK, RUN_VAULT_DIR and the paths set_run_paths
# sets (SMOKE_PLAN_FILE, SMOKE_CHECK_FILE, SMOKE_CHECK_JSON). Sets SMOKE_RESULT
# (pass | fail | skipped | not run | "") and SMOKE_REASON, read by the commit
# banner (show_commit_context) and the strict policy (_commit_gate_blockers,
# gates.commit.require_smoke_pass). A failure of the tool itself leaves a warning
# and "not run": the review goes on without it.
#
# smoke-plan.json is written once per diff: a fix round reuses it when the
# changed-file list is the same, and plans again (one call) when it isn't.
# Delete it to plan again by hand. The report files are rewritten every round.

# run_smoke_check [--rerun]
run_smoke_check() {
  local rerun=false
  [[ "${1:-}" == "--rerun" ]] && rerun=true
  SMOKE_RESULT="" SMOKE_REASON=""
  local mode
  mode="$(cfg smoke.check off)"
  [[ "$mode" == "off" ]] && return 0
  if [[ "$STACK" == "rails" ]]; then
    local reachable
    reachable=$(changed_reachable_files "$BASE_BRANCH" || true)
    if [[ -z "$reachable" ]]; then
      log "Smoke check: the diff touches no route, controller, view, lib/ or initializer - nothing to call"
      return 0
    fi
  fi

  local vault_plan="$RUN_VAULT_DIR/smoke-plan.json"
  resume_from_vault "$SMOKE_PLAN_FILE" "$vault_plan"
  local changed_file detect_file rc=0
  changed_file=$(mktemp)
  detect_file=$(mktemp)
  changed_all_files "$BASE_BRANCH" > "$changed_file" || true
  local -a py=("$MIGITE_PYTHON" -m migite.smoke --repo-root "${REPO_ROOT:-$PWD}")

  # 1. Where is the service, and is it up and current? (no model call)
  "${py[@]}" detect --changed-files "$changed_file" --out "$detect_file" > /dev/null || rc=$?
  if [[ $rc -ne 0 ]]; then
    warn "Smoke check: the service could not be detected; reviewing without it"
    SMOKE_RESULT="not run" SMOKE_REASON="detection failed"
    rm -f "$changed_file" "$detect_file"
    return 0
  fi
  local state base_url reason
  state=$(json_field "$detect_file" state || echo unknown)
  base_url=$(json_field "$detect_file" base_url || true)
  reason=$(json_field "$detect_file" reason || true)

  if [[ "$mode" == "ask" && "$rerun" == false ]]; then
    read_gate_choice "SMOKE CHECK" "Send real requests to $base_url to check the change before the review? [y/N]: " N
    if [[ ! "${GATE_CHOICE:-}" =~ ^[yY]$ ]]; then
      log "Smoke check skipped"
      rm -f "$changed_file" "$detect_file"
      return 0
    fi
  fi
  while [[ "$state" == "down" ]]; do
    read_gate_choice "SMOKE CHECK" "No service answers at $base_url (${reason:-not running}). [w/k] (w=I started it, check again; k=skip): " k
    case "${GATE_CHOICE:-k}" in
      w|W)
        "${py[@]}" detect --changed-files "$changed_file" --out "$detect_file" > /dev/null || true
        state=$(json_field "$detect_file" state || echo unknown)
        reason=$(json_field "$detect_file" reason || true)
        ;;
      *)
        log "Smoke check skipped: nothing answers at $base_url"
        SMOKE_RESULT="skipped" SMOKE_REASON="${reason:-no service at $base_url}"
        rm -f "$changed_file" "$detect_file"
        return 0
        ;;
    esac
  done
  if [[ "$state" == "stale" ]]; then
    warn "Smoke check skipped: $reason"
    SMOKE_RESULT="skipped" SMOKE_REASON="$reason"
    rm -f "$changed_file" "$detect_file"
    return 0
  fi

  # 2. The calls to send: one headless call, reused while the changed files are the same.
  local diff_file plan_out
  diff_file=$(mktemp)
  plan_out=$(mktemp -u)
  prompt_diff "$BASE_BRANCH" > "$diff_file"
  local -a plan_args=(--diff "$diff_file" --detect "$detect_file" --changed-files "$changed_file" --out "$plan_out")
  [[ -s "$TESTING_PLAN_FILE" ]] && plan_args+=(--testing-plan "$TESTING_PLAN_FILE")
  [[ -s "$SMOKE_PLAN_FILE" ]] && plan_args+=(--existing "$SMOKE_PLAN_FILE")
  log "Phase 3.2 - Smoke check against $base_url"
  rc=0
  "${py[@]}" plan "${plan_args[@]}" || rc=$?
  rm -f "$diff_file"
  if [[ $rc -eq 0 && -s "$plan_out" ]]; then
    mv "$plan_out" "$SMOKE_PLAN_FILE"
    sync_json "$SMOKE_PLAN_FILE" "$vault_plan"
  elif [[ $rc -ne 0 || ! -s "$SMOKE_PLAN_FILE" ]]; then
    rm -f "$plan_out"
    warn "Smoke check: no call list could be planned; reviewing without it"
    SMOKE_RESULT="not run" SMOKE_REASON="no call list"
    rm -f "$changed_file" "$detect_file"
    return 0
  fi
  rm -f "$plan_out"

  # 3. Send them (no model call); exit 0 pass, 2 fail, 3 skipped, else the tool failed.
  rc=0
  "${py[@]}" run --plan "$SMOKE_PLAN_FILE" --detect "$detect_file" \
    --out-md "$SMOKE_CHECK_FILE" --out-json "$SMOKE_CHECK_JSON" || rc=$?
  rm -f "$changed_file" "$detect_file"
  case $rc in
    0) SMOKE_RESULT="pass" ;;
    2) SMOKE_RESULT="fail" ;;
    3) SMOKE_RESULT="skipped" ;;
    *) warn "Smoke check: the calls could not be sent; reviewing without it"
       SMOKE_RESULT="not run" SMOKE_REASON="run failed"
       return 0 ;;
  esac
  SMOKE_REASON=$(json_field "$SMOKE_CHECK_JSON" reason || true)
  sync_artifact "$SMOKE_CHECK_FILE" "$RUN_VAULT_DIR/smoke-check.md"
  sync_json "$SMOKE_CHECK_JSON" "$RUN_VAULT_DIR/smoke-check.json"
  case "$SMOKE_RESULT" in
    pass) success "Smoke check passed - $SMOKE_CHECK_FILE" ;;
    fail) warn "Smoke check: $SMOKE_REASON - $SMOKE_CHECK_FILE" ;;
    *)    warn "Smoke check skipped: $SMOKE_REASON" ;;
  esac
  return 0
}
