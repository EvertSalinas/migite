# tests/automata_test.sh - what --automata changes in the shared helpers: the
# gates and prompts take their automata answer without reading stdin, a gate
# with no answer stops the run instead of hanging, the commit gate's decision
# (a fix round while blockers remain, then strict with blockers stops with
# status 2), and no tmux pane or desktop
# notification. The headless sessions are in agents_test.sh; a whole automata
# run, its run.json mode and the strict exit code from bin/migite are in
# resume_test.sh.

au_dir=$(mktemp -d); CLEANUP_DIRS+=("$au_dir")
au_reply=""   # read_answer's target

# ── read_gate_choice / read_answer ───────────────────────────────────────────
GATE_CHOICE=""
out=$(MIGITE_AUTOMATA=true read_gate_choice "REVIEW GATE: plan" "Proceed? " y <<< "q"; echo "choice=$GATE_CHOICE")
check "read_gate_choice --automata: takes its answer, not the q on stdin" \
  bash -c '[[ "$1" == *"REVIEW GATE: plan"* && "$1" == *"Proceed? "*"y  "*"(--automata)"* && "$1" == *"choice=y" ]]' _ "$out"

out=$( (MIGITE_AUTOMATA=true read_gate_choice "NEW GATE" "Proceed? " <<< "y") 2>&1 ); rc=$?
check "read_gate_choice --automata: a gate with no automata answer stops the run" \
  bash -c '[[ "$1" != 0 && "$2" == *"The '"'"'NEW GATE'"'"' gate has no --automata answer"* ]]' _ "$rc" "$out"

out=$(MIGITE_AUTOMATA=false read_gate_choice "REVIEW GATE: plan" "Proceed? " y <<< "n"; echo "choice=$GATE_CHOICE")
check "read_gate_choice interactive: still reads the answer from stdin" bash -c '[[ "$1" == *"choice=n" ]]' _ "$out"

out=$(MIGITE_AUTOMATA=true read_answer au_reply "Anything worth remembering? " "" <<< "a note"; echo "reply=[$au_reply]")
check "read_answer --automata: an empty answer leaves the variable empty, stdin unread" \
  bash -c '[[ "$1" == *"Anything worth remembering? (Enter)  "*"(--automata)"* && "$1" == *"reply=[]" ]]' _ "$out"

out=$(MIGITE_AUTOMATA=true read_answer au_reply "Use existing plan? " u <<< "r"; echo "reply=[$au_reply]")
check "read_answer --automata: sets the answer it was given" bash -c '[[ "$1" == *"reply=[u]" ]]' _ "$out"

out=$(MIGITE_AUTOMATA=false read_answer au_reply "Use existing plan? " u <<< "r"; echo "reply=[$au_reply]")
check "read_answer interactive: reads stdin" bash -c '[[ "$1" == *"reply=[r]" ]]' _ "$out"

# ── the commit gate's decision ───────────────────────────────────────────────
au_blockers=$'review verdict is NEEDS FIXES\n2 failures in rspec'
out=$(automata_commit_gate lenient "$au_blockers" 2>&1); rc=$?
check "automata_commit_gate: lenient approves over blockers and lists them" \
  bash -c '[[ "$1" == 0 && "$2" == *"lenient"*"exit 3"* && "$2" == *"- review verdict is NEEDS FIXES"* && "$2" == *"- 2 failures in rspec"* ]]' _ "$rc" "$out"
out=$(automata_commit_gate strict "" 2>&1); rc=$?
check "automata_commit_gate: strict with nothing blocking approves" bash -c '[[ "$1" == 0 && -z "$2" ]]' _ "$rc" "$out"
out=$(automata_commit_gate strict "$au_blockers" 2>&1); rc=$?
check "automata_commit_gate: strict with blockers returns 2 and prints every blocker" \
  bash -c '[[ "$1" == 2 && "$2" == *"strict"* && "$2" == *"- review verdict is NEEDS FIXES"* && "$2" == *"- 2 failures in rspec"* ]]' _ "$rc" "$out"

check "automata_fix_round_due: blockers and a round left → the gate answers f" \
  automata_fix_round_due "$au_blockers" 0 1
check "automata_fix_round_due: the rounds are spent → the policy decides" \
  not automata_fix_round_due "$au_blockers" 1 1
check "automata_fix_round_due: gates.commit.automata_fix_rounds 0 → never" \
  not automata_fix_round_due "$au_blockers" 0 0
check "automata_fix_round_due: nothing blocks → no round" \
  not automata_fix_round_due "" 0 1
check "automata_fix_round_due: only a tooling error → no round (a fix session can't repair the toolchain)" \
  not automata_fix_round_due "tooling error: bundler missing" 0 1
check "automata_fix_round_due: a tooling error next to a code blocker → a round" \
  automata_fix_round_due $'tooling error: bundler missing\nreview verdict is NEEDS FIXES' 0 1

# ── exit statuses ────────────────────────────────────────────────────────────
( migite_exit 2 ); rc=$?
check "migite_exit: exits with the status it was given" test "$rc" = "2"
au_automata_with() { MIGITE_AUTOMATA="$1" automata; }
check "automata: on for MIGITE_AUTOMATA=true" au_automata_with true
check "automata: off for any other value" not au_automata_with yes

# ── no tmux, no notifications ────────────────────────────────────────────────
au_tmux() { MIGITE_AUTOMATA="$1" MIGITE_CFG_UI_TMUX=on TMUX=/tmp/fake,1,0 use_tmux; }
check "use_tmux --automata: never, even inside tmux with ui.tmux: on" not au_tmux true
check "use_tmux interactive: inside tmux with ui.tmux: on (control)" au_tmux false

mkdir -p "$au_dir/bin"
for au_tool in osascript notify-send; do
  printf '#!/usr/bin/env bash\necho called >> "%s/notified"\n' "$au_dir" > "$au_dir/bin/$au_tool"
  chmod +x "$au_dir/bin/$au_tool"
done
( PATH="$au_dir/bin:$PATH" MIGITE_CFG_UI_NOTIFY=auto MIGITE_AUTOMATA=true notify "Phase 1" "Plan ready" )
check "notify --automata: no desktop notification" test ! -e "$au_dir/notified"
( PATH="$au_dir/bin:$PATH" MIGITE_CFG_UI_NOTIFY=auto MIGITE_AUTOMATA=false notify "Phase 1" "Plan ready" )
check "notify interactive: notifies (control: the stub works)" test -e "$au_dir/notified"

unset -f au_automata_with au_tmux
unset au_dir au_blockers au_tool au_reply
