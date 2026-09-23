#!/usr/bin/env bash
# lib/gate.sh - the human gates: banner, review verdict, commit context.
#
# Reads the run's globals (REVIEW_FILE, RSPEC_LOG, RUBOCOP_FINAL_*, TOOLING_ERROR)
# at call time; sets $GATE_CHOICE.

# read_gate_choice <banner-line> <prompt-text> — sets $GATE_CHOICE
# Prints the standard gate banner then reads one line of input. Must be called
# directly (not via command substitution) so the interactive prompt stays visible.
read_gate_choice() {
  local banner="$1" prompt="$2"
  echo ""
  echo -e "${BOLD}────────────────────────────────────────${RESET}"
  echo -e "${BOLD}  ${banner}${RESET}"
  echo -e "${BOLD}────────────────────────────────────────${RESET}"
  echo ""
  read -r -p "$(echo -e "${YELLOW}${prompt}${RESET}")" GATE_CHOICE
}

# review_verdict <review.md> — echoes one of: needs_fixes | ready | unknown
# Anchors on the "## Verdict" heading instead of grepping the whole file for
# keywords. The review format puts "## Brakeman: PASS" / "## Rubocop: PASS"
# lines dozens of lines ABOVE the verdict, so `grep -m1 'PASS\|NEEDS FIXES...'`
# matched those first and the commit-gate banner showed APPROVED on reviews
# whose actual verdict was NEEDS FIXES (7 of 20 real reviews in the vault when
# this was found). Handles every shape seen in practice: the verdict on the
# heading line ("## Verdict: NEEDS FIXES", with or without ** bold), or on the
# first non-empty line under a bare "## Verdict" heading, with or without a
# trailing " — contingent on ..." clause.
review_verdict() {
  local file="$1"
  [[ -f "$file" ]] || { echo "unknown"; return; }
  # Prefer the machine-readable envelope migite-review writes beside review.md
  # (a schema-validated enum, not prose). review.sh deletes review.json before
  # every review run and when review.md is hand-edited at the gate, so a
  # present review.json is always current. Fall through to parsing the
  # markdown when it's absent or malformed.
  local json="${file%.md}.json" v
  if [[ -s "$json" ]]; then
    v=$(json_field "$json" verdict || true)
    case "$v" in
      needs_fixes|ready) echo "$v"; return ;;
    esac
  fi
  local line
  line=$(awk '
    !found && /^#+[[:space:]]*[Vv]erdict/ {
      found = 1
      rest = $0
      sub(/^#+[[:space:]]*[Vv]erdict[[:space:]]*:?[[:space:]]*/, "", rest)
      if (rest ~ /[^[:space:]*_]/) { print rest; exit }
      next
    }
    found && NF { print; exit }
  ' "$file")
  # No "Verdict" heading at all — one older shape puts the verdict itself as a
  # heading ("# NEEDS FIXES"). Accept a heading that IS a verdict keyword, but
  # never a keyword buried in prose, which is the trap this helper exists to avoid.
  if [[ -z "$line" ]]; then
    line=$(grep -m1 -E '^#+[[:space:]]*\**(NEEDS (FIXES|CHANGES)|READY TO (COMMIT|MERGE)|APPROVED)' "$file" || true)
  fi
  if echo "$line" | grep -qE 'NEEDS (FIXES|CHANGES)'; then
    echo "needs_fixes"
  elif echo "$line" | grep -qE 'READY TO (COMMIT|MERGE)|APPROVED'; then
    echo "ready"
  else
    echo "unknown"
  fi
}

show_commit_context() {
  echo ""
  echo -e "${BOLD}── Commit context ──────────────────────────${RESET}"

  # Tooling error (Ruby version unset, git gem not checked out, DB down, load
  # error) — set by run_review from tooling_failed. When set, every result
  # below it is untrustworthy because the tool never actually ran.
  if [[ -n "${TOOLING_ERROR:-}" ]]; then
    echo -e "  ${RED}${BOLD}⚠ ${TOOLING_ERROR} — fix the toolchain before approving.${RESET}"
  fi

  # Review verdict — anchored on the "## Verdict" heading via review_verdict,
  # never a whole-file keyword grep (see that helper for why).
  if [[ -f "${REVIEW_FILE:-}" ]]; then
    case "$(review_verdict "$REVIEW_FILE")" in
      needs_fixes) echo -e "  Verdict: ${RED}${BOLD}NEEDS FIXES${RESET}" ;;
      ready)       echo -e "  Verdict: ${GREEN}${BOLD}READY TO COMMIT${RESET}" ;;
      *)           echo -e "  Verdict: ${YELLOW}unknown — check $REVIEW_FILE${RESET}" ;;
    esac
    # Finding counts from review.json (typed, from the structured verdict call)
    local review_json="${REVIEW_FILE%.md}.json"
    if [[ -s "$review_json" ]]; then
      local n_crit n_warn n_note reason
      n_crit=$(json_field "$review_json" counts.critical || echo "?")
      n_warn=$(json_field "$review_json" counts.warning || echo "?")
      n_note=$(json_field "$review_json" counts.note || echo "?")
      local crit_color="$GREEN"; [[ "$n_crit" != "0" ]] && crit_color="$RED"
      echo -e "  Findings: ${crit_color}${BOLD}${n_crit} critical${RESET} · ${n_warn} warnings · ${n_note} notes"
      reason=$(json_field "$review_json" reason || true)
      [[ -n "$reason" ]] && echo -e "  Reason:  ${reason}" | fold -s -w 96 | sed '2,$s/^/           /'
    fi
  fi

  # Spec failures / tooling failures (via tooling_failed — one pattern list)
  if [[ -f "${RSPEC_LOG:-}" ]]; then
    local failure_line spec_tooling_msg
    failure_line=$(grep -oE '[1-9][0-9]* failure[s]?' "$RSPEC_LOG" | head -1 || echo "")
    if [[ -n "$failure_line" ]]; then
      echo -e "  Specs:   ${RED}${BOLD}⚠ $failure_line — check $RSPEC_LOG before approving${RESET}"
    elif spec_tooling_msg=$(tooling_failed "$RSPEC_LOG"); then
      echo -e "  Specs:   ${RED}${BOLD}⚠ ${spec_tooling_msg}${RESET}"
    elif grep -qE '^(No spec files changed|Generic stack)' "$RSPEC_LOG"; then
      echo -e "  Specs:   ${YELLOW}not run${RESET}"
    else
      echo -e "  Specs:   ${GREEN}all passed${RESET}"
    fi
  fi

  # Rubocop post-review state
  if [[ -n "${RUBOCOP_FINAL_OFFENSES:-}" && "${RUBOCOP_FINAL_OFFENSES}" != "0" ]]; then
    echo -e "  Rubocop: ${RED}${BOLD}$RUBOCOP_FINAL_OFFENSES offense(s) remain${RESET}"
  elif [[ -n "${RUBOCOP_FINAL_LOG:-}" && -f "$RUBOCOP_FINAL_LOG" ]]; then
    echo -e "  Rubocop: ${GREEN}clean${RESET}"
  fi

  # Running total from the usage ledger (headless calls only), with the soft budget cap
  local cost_line
  if cost_line=$(run_cost_so_far); then
    echo -e "  Cost:    ${cost_line} so far (headless calls only)"
    local cap="${MIGITE_CFG_BUDGET_MAX_USD_PER_RUN:-}"
    if [[ -n "$cap" ]]; then
      local spent="${cost_line##*\$}"
      if awk -v s="$spent" -v c="$cap" 'BEGIN { exit !(s > c) }'; then
        echo -e "  ${RED}${BOLD}⚠ Over budget: \$${spent} spent, budget.max_usd_per_run is \$${cap}${RESET}"
      fi
    fi
  fi

  echo -e "${BOLD}────────────────────────────────────────────${RESET}"
  echo ""
}
