# tests/tmux_panes_test.sh - agent panes open in migite's own tmux window, not in
# whichever window has focus when a phase starts. A fake tmux (tests/fake-tmux)
# records every call; no tmux server is touched.

tm_dir=$(mktemp -d); CLEANUP_DIRS+=("$tm_dir")
mkdir -p "$tm_dir/bin" "$tm_dir/repo" "$tm_dir/home/.config"
chmod +x "$SCRIPT_DIR/fake-tmux" "$SCRIPT_DIR/fake-claude"
ln -sf "$SCRIPT_DIR/fake-tmux"   "$tm_dir/bin/tmux"
ln -sf "$SCRIPT_DIR/fake-claude" "$tm_dir/bin/claude"
_tm_path="$PATH"; _tm_home="$HOME"; _tm_xdg="${XDG_CONFIG_HOME:-}"; _tm_repo_root="$REPO_ROOT"
_tm_tmux="${TMUX:-}"; _tm_tmux_pane="${TMUX_PANE:-}"
PATH="$tm_dir/bin:$PATH"
export HOME="$tm_dir/home" XDG_CONFIG_HOME="$tm_dir/home/.config"
export FAKE_TMUX_LOG="$tm_dir/tmux.log" FAKE_TMUX_FOCUSED="%1"
export TMUX="/tmp/fake-tmux-socket,1,0" MIGITE_CFG_UI_TMUX=auto MIGITE_CFG_UI_NOTIFY=off
REPO_ROOT="$tm_dir/repo"

# migite_pane: the pane migite runs in, else the focused one
export TMUX_PANE="%7"
check "migite_pane: TMUX_PANE names migite's own pane" test "$(migite_pane)" = "%7"
check "migite_pane: a pane that no longer exists falls back to the focused pane" \
  test "$(FAKE_TMUX_GONE="%7" migite_pane)" = "%1"
check "migite_pane: no TMUX_PANE falls back to the focused pane" \
  bash -c 'unset TMUX_PANE; source "$1/lib/agent.sh"; [[ "$(migite_pane)" == "%1" ]]' _ "$MIGITE_HOME"

if "$MIGITE_PYTHON" -c 'import sys' &>/dev/null; then
  # run_phase with focus on another window (%1): the split targets migite's pane (%7)
  : > "$FAKE_TMUX_LOG"
  run_phase "PR description" "$tm_dir/pr.md" "write it" > /dev/null 2>&1
  check "run_phase: the session pane is split from migite's pane, not the focused window" \
    grep -q -- "^split-window .*-t %7 " "$FAKE_TMUX_LOG"
  check "run_phase: focus returns to migite's pane afterwards" grep -qx -- "select-pane -t %7" "$FAKE_TMUX_LOG"
else
  echo "  · no working Python at $MIGITE_PYTHON - skipping run_phase pane checks"
fi

unset FAKE_TMUX_LOG FAKE_TMUX_FOCUSED MIGITE_CFG_UI_TMUX MIGITE_CFG_UI_NOTIFY
if [[ -n "$_tm_tmux" ]]; then export TMUX="$_tm_tmux"; else unset TMUX; fi
if [[ -n "$_tm_tmux_pane" ]]; then export TMUX_PANE="$_tm_tmux_pane"; else unset TMUX_PANE; fi
if [[ -n "$_tm_xdg" ]]; then export XDG_CONFIG_HOME="$_tm_xdg"; else unset XDG_CONFIG_HOME; fi
export HOME="$_tm_home"; PATH="$_tm_path"; REPO_ROOT="$_tm_repo_root"
