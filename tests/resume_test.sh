# tests/resume_test.sh - bin/migite resuming from run.json, end to end. The fake
# agent CLI (tests/fake-claude) stands in for claude, gate answers are piped on
# stdin, and plan.md is written up front so planning never starts migite-plan
# (CI has no langgraph). Run 1 stops at the plan gate; the same command again
# goes straight back to that gate; a run that crashed in implement skips the
# finished phases; a finished run only prints where its files are.

if ! "$MIGITE_PYTHON" -c 'import sys' &>/dev/null; then
  echo "  · no working Python at $MIGITE_PYTHON - skipping end-to-end resume checks"
  return 0 2>/dev/null || exit 0
fi

rs_dir=$(mktemp -d); CLEANUP_DIRS+=("$rs_dir")
rs_repo=$(make_fixture_repo)
git -C "$rs_repo" commit -q --allow-empty -m init
mkdir -p "$rs_dir/bin" "$rs_dir/home/.config" "$rs_dir/logs"
chmod +x "$SCRIPT_DIR/fake-claude"
ln -sf "$SCRIPT_DIR/fake-claude" "$rs_dir/bin/claude"
# Inline phases, no desktop notifications; JSON so the test needs no PyYAML.
printf '{"ui": {"notify": "off", "tmux": "off"}}\n' > "$rs_dir/config.json"

# rs_migite <args...> - bin/migite in the fixture repo, isolated from the real
# config, vault and logs; gate answers come from stdin. RS_CONFIG and RS_EDITOR
# swap in another config file or editor for one run.
rs_migite() {
  ( cd "$rs_repo" && env -u TMUX HOME="$rs_dir/home" XDG_CONFIG_HOME="$rs_dir/home/.config" \
      MIGITE_CONFIG="${RS_CONFIG:-$rs_dir/config.json}" DEV_LOG_BASE="$rs_dir/vault" MIGITE_ORG=Acme \
      LOG_DIR="$rs_dir/logs" EDITOR="${RS_EDITOR:-true}" PATH="$rs_dir/bin:$PATH" \
      bash "$MIGITE_HOME/bin/migite" "$@" 2>&1 )
}

rs_scratch="$rs_repo/scratchpad/add-thing"
rs_manifest="$rs_scratch/00-build/run.json"
mkdir -p "$rs_scratch"
printf '# Add thing\n\n## Scope\n### Models\n- app/models/thing.rb\n' > "$rs_scratch/plan.md"

# Run 1: use the existing plan, then q at the plan gate.
out=$(printf 'u\nq\n' | rs_migite "Add thing" --type feature); rc=$?
check "resume run 1: q at the plan gate exits 0" test "$rc" = "0"
check "resume run 1: a task with no run.json yet asks about its existing plan, as before" \
  bash -c '[[ "$1" == *"EXISTING PLAN FOUND"* ]]' _ "$out"
check "resume run 1: run.json records the plan gate as pending" \
  test "$(json_field "$rs_manifest" phases.plan.status)|$(json_field "$rs_manifest" status)" = "pending_gate|in_progress"
check "resume run 1: run.json is mirrored to the vault" test -f "$rs_dir/vault/Acme/$(basename "$rs_repo")/add-thing/00-build/run.json"

# Run 2: the same command, no --resume. Back to the gate without the plan prompt;
# approve, decline TDD. Implement then fails (the fake session writes no notes).
out=$(printf 'y\nn\n' | rs_migite "Add thing" --type feature); rc=$?
check "resume run 2: the same command resumes from run.json" \
  bash -c '[[ "$1" == *"Resuming add-thing/00-build"* && "$1" == *"Back at the plan gate"* ]]' _ "$out"
check "resume run 2: the gate re-opens without asking use-or-redo again" \
  bash -c '[[ "$1" != *"EXISTING PLAN FOUND"* ]]' _ "$out"
check "resume run 2: plan done, TDD skipped, the declined answer recorded" \
  test "$(json_field "$rs_manifest" phases.plan.status)|$(json_field "$rs_manifest" phases.tdd.status)|$(json_field "$rs_manifest" phases.tdd.decided)" = "done|skipped|false"
check "resume run 2: the crash leaves implement running and the run failed" \
  test "$rc|$(json_field "$rs_manifest" phases.implement.status)|$(json_field "$rs_manifest" status)" = "1|running|failed"
check "resume run 2: the plan gate rounds carry over (one per invocation)" test "$(json_field "$rs_manifest" phases.plan.gate_attempts)" = "2"

# Run 3: plan and TDD are finished, so the run starts at implement.
out=$(printf '' | rs_migite "Add thing" --type feature); rc=$?
check "resume run 3: finished phases are skipped" \
  bash -c '[[ "$1" == *"Phase plan already finished"* && "$1" == *"Phase tdd already finished"* ]]' _ "$out"
check "resume run 3: no plan gate, no TDD question; straight to implement" \
  bash -c '[[ "$1" != *"REVIEW GATE: plan"* && "$1" != *"Phase 1.5 (TDD)"* && "$1" == *"Phase 2/4"* ]]' _ "$out"

# Run 4: a finished run.
"$MIGITE_PYTHON" -m migite.runstate update --file "$rs_manifest" \
  $(for p in implement heal review deliver; do printf -- '--set phases.%s.status=done ' "$p"; done) >/dev/null
out=$(printf '' | rs_migite "Add thing"); rc=$?
check "resume run 4: a finished run exits 0, says where its files are, and points at --amend" \
  bash -c '[[ "$1" == 0 && "$2" == *"This run already finished"* && "$2" == *"migite --amend"* && "$2" != *"Phase 1/4"* ]]' _ "$rc" "$out"

out=$(printf '' | rs_migite --resume); rc=$?
check "migite --resume: nothing unfinished in the repo → an error" \
  bash -c '[[ "$1" != 0 && "$2" == *"No unfinished run to resume"* ]]' _ "$rc" "$out"

# ── --automata ───────────────────────────────────────────────────────────────
# No stdin at all. Run 1 answers every prompt itself and the implement session,
# headless through agent_cli ask, fails (FAKE_CLAUDE_MODE=exit1). Run 2 is the
# same command without --automata. Run 3 resumes at a commit gate with a NEEDS
# FIXES review under gates.commit.policy: strict and exits 2. None of the runs
# reaches deliver, whose Phase 4.5 writes to this checkout's docs/improvements.md.
au_scratch="$rs_repo/scratchpad/add-widget"
au_manifest="$au_scratch/00-build/run.json"
mkdir -p "$au_scratch"
printf '# Add widget\n\n## Scope\n### Models\n- app/models/widget.rb\n' > "$au_scratch/plan.md"
printf '#!/usr/bin/env bash\ntouch "%s/editor-opened"\n' "$rs_dir" > "$rs_dir/editor-marker"
chmod +x "$rs_dir/editor-marker"

out=$(RS_EDITOR="$rs_dir/editor-marker" FAKE_CLAUDE_MODE=exit1 FAKE_CLAUDE_ARGV="$rs_dir/claude-argv.txt" \
        rs_migite --automata "Add widget" < /dev/null); rc=$?
check "automata run 1: no --type → planned as a feature, and the intake editor never opens" \
  bash -c '[[ "$1" == *"planning as a feature (--automata)"* ]] && test ! -e "$2/editor-opened"' _ "$out" "$rs_dir"
check "automata run 1: the existing plan is used, the plan gate approved, TDD declined, each marked --automata" \
  bash -c '[[ "$1" == *"Use existing plan or redo?"*"u  "*"(--automata)"* && "$1" == *"REVIEW GATE: plan"*"y  "*"(--automata)"* && "$1" == *"Phase 1.5 (TDD)"*"N  "*"(--automata)"* ]]' _ "$out"
check "automata run 1: run.json records mode automata, plan done, TDD skipped and declined" \
  test "$(json_field "$au_manifest" mode)|$(json_field "$au_manifest" phases.plan.status)|$(json_field "$au_manifest" phases.tdd.status)|$(json_field "$au_manifest" phases.tdd.decided)" = "automata|done|skipped|false"
check "automata run 1: the implement session ran headless on the session role (--print, bypassPermissions, the strong model)" \
  bash -c 'grep -q -- "--print" "$1" && grep -q -- "--permission-mode bypassPermissions" "$1" && grep -q -- "--model claude-opus-5-5" "$1" && ! grep -q -- "--safe-mode" "$1"' _ "$rs_dir/claude-argv.txt"
check "automata run 1: the session is in the run's usage ledger" grep -q '"label": "session:Implementing"' "$au_scratch/00-build/usage.jsonl"
check "automata run 1: a failed session stops the run with exit 1, implement left running, the run failed" \
  test "$rc|$(json_field "$au_manifest" phases.implement.status)|$(json_field "$au_manifest" status)" = "1|running|failed"

au_before=$(cat "$au_manifest")
out=$(rs_migite "Add widget" < /dev/null); rc=$?
check "automata run 2: resuming it without --automata is refused, saying how to continue" \
  bash -c '[[ "$1" == 1 && "$2" == *"started with --automata"*"again with --automata"* ]]' _ "$rc" "$out"
check "automata run 2: and leaves run.json as it was" test "$(cat "$au_manifest")" = "$au_before"

# Run 3: implement and heal finished, stopped at the commit gate over a NEEDS FIXES
# review of this very tree, so the gate re-opens on the same review.
printf '# Review: Add widget\n\n## Verdict: NEEDS FIXES\n\n- Critical: the widget is never saved\n' > "$au_scratch/00-build/review.md"
printf '{"verdict": "needs_fixes", "counts": {"critical": 1, "warning": 0, "note": 0}, "reason": "one critical"}\n' > "$au_scratch/00-build/review.json"
au_fp=$(cd "$rs_repo" && tree_fingerprint "$(json_field "$au_manifest" repo.base_branch)")
"$MIGITE_PYTHON" -m migite.runstate update --file "$au_manifest" --set phases.implement.status=done \
  --set phases.heal.status=done --set phases.review.status=pending_gate --set "phases.review.tree_fingerprint=$au_fp" >/dev/null
printf '{"ui": {"notify": "off", "tmux": "off"}, "gates": {"commit": {"policy": "strict"}}}\n' > "$rs_dir/strict.json"
out=$(RS_CONFIG="$rs_dir/strict.json" FAKE_CLAUDE_MODE=exit1 rs_migite --automata "Add widget" < /dev/null); rc=$?
check "automata run 3: back at the commit gate on the review it stopped at" \
  bash -c '[[ "$1" == *"Phase implement already finished"* && "$1" == *"reusing"*"review.md"* ]]' _ "$out"
check "automata run 3: strict with blockers exits 2 and prints them" \
  bash -c '[[ "$1" == 2 && "$2" == *"--automata stops at the commit gate"* && "$2" == *"- review verdict is NEEDS FIXES"* ]]' _ "$rc" "$out"
check "automata run 3: the gate stays pending, the run unfinished (not failed), the blocker in run.json" \
  test "$(json_field "$au_manifest" phases.review.status)|$(json_field "$au_manifest" status)|$(json_field "$au_manifest" phases.review.blockers.0)" = "pending_gate|in_progress|review verdict is NEEDS FIXES"
check "automata run 3: deliver never ran" \
  bash -c '[[ "$1" != *"Phase 3.5/4"* && "$2" == pending ]]' _ "$out" "$(json_field "$au_manifest" phases.deliver.status)"

unset -f rs_migite
unset rs_dir rs_repo rs_scratch rs_manifest au_scratch au_manifest au_before au_fp
