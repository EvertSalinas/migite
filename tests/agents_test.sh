# tests/agents_test.sh — the bash side of agent backends: claude_print / thinking /
# heal_run through `migite_claude.py run` on each backend, run_phase's interactive
# command line, agent_binary / agent_supports, cfg_model_flags with no default model.
# Fake CLIs (tests/fake-claude, fake-cursor-agent, fake-opencode) are put first on PATH
# under their real names so no real agent is ever invoked.

if ! "$MIGITE_PYTHON" -c 'import sys' &>/dev/null; then
  echo "  · no working Python at $MIGITE_PYTHON — skipping agent backend checks"
  return 0 2>/dev/null || exit 0
fi

ag_dir=$(mktemp -d); CLEANUP_DIRS+=("$ag_dir")
mkdir -p "$ag_dir/bin" "$ag_dir/repo" "$ag_dir/home/.config/migite"
chmod +x "$SCRIPT_DIR"/fake-claude "$SCRIPT_DIR"/fake-cursor-agent "$SCRIPT_DIR"/fake-opencode
ln -sf "$SCRIPT_DIR/fake-claude"       "$ag_dir/bin/claude"
ln -sf "$SCRIPT_DIR/fake-cursor-agent" "$ag_dir/bin/cursor-agent"
ln -sf "$SCRIPT_DIR/fake-opencode"     "$ag_dir/bin/opencode"
_ag_path="$PATH"; PATH="$ag_dir/bin:$PATH"
_ag_home="$HOME"; export HOME="$ag_dir/home" XDG_CONFIG_HOME="$ag_dir/home/.config"
export MIGITE_USAGE_LEDGER="$ag_dir/usage.jsonl" FAKE_AGENT_ARGV="$ag_dir/argv.log"
_ag_repo_root="${REPO_ROOT:-}"          # run.sh's REPO_ROOT is the migite checkout — restore it at the end
REPO_ROOT="$ag_dir/repo"

for backend in claude cursor opencode; do
  export MIGITE_AGENT="$backend"
  case "$backend" in claude) bin=claude ;; cursor) bin=cursor-agent ;; opencode) bin=opencode ;; esac
  check "agent_binary: $backend → $bin" test "$(agent_binary)" = "$bin"
  : > "$FAKE_AGENT_ARGV"
  out=$(printf 'summarise this' | FAKE_CLAUDE_MODE=envelope FAKE_AGENT_MODE=ok claude_print "unit-$backend" --model "" )
  case "$backend" in
    claude)   check "claude_print via $backend returns the result text" bash -c '[[ "$1" == echo:summarise* ]]' _ "$out" ;;
    cursor)   check "claude_print via $backend returns the result text" bash -c '[[ "$1" == cursor:summarise* ]]' _ "$out"
              check "claude_print via cursor: headless flags -p --output-format json --trust, no --model" \
                bash -c 'grep -q -- "-p --output-format json --trust" "$1" && ! grep -q -- "--model" "$1"' _ "$FAKE_AGENT_ARGV" ;;
    opencode) check "claude_print via $backend returns the joined text events" bash -c '[[ "$1" == opencode:summarise*second\ part ]]' _ "$out"
              check "claude_print via opencode: run --format json, no --model" \
                bash -c 'grep -q "run --format json" "$1" && ! grep -q -- "--model" "$1"' _ "$FAKE_AGENT_ARGV" ;;
  esac
  check "ledger records the $backend call" grep -q "\"label\": \"unit-$backend\"" "$MIGITE_USAGE_LEDGER"
done

# capabilities
export MIGITE_AGENT=claude;   check "agent_supports: claude has tool_allowlist"      agent_supports tool_allowlist
export MIGITE_AGENT=cursor;   check "agent_supports: cursor lacks tool_allowlist"    not agent_supports tool_allowlist
                              check "agent_supports: cursor lacks structured_output" not agent_supports structured_output
export MIGITE_AGENT=opencode; check "agent_supports: opencode reports usage"         agent_supports usage

# permission mapping on the headless path
export MIGITE_AGENT=cursor; : > "$FAKE_AGENT_ARGV"
printf 'x' | FAKE_AGENT_MODE=ok claude_print "perm" --permission-mode bypassPermissions >/dev/null
check "cursor: bypassPermissions → --force" grep -q -- "--force" "$FAKE_AGENT_ARGV"
export MIGITE_AGENT=opencode; : > "$FAKE_AGENT_ARGV"
printf 'x' | FAKE_AGENT_MODE=ok claude_print "perm" --permission-mode acceptEdits >/dev/null
check "opencode: acceptEdits → --auto" grep -q -- "--auto" "$FAKE_AGENT_ARGV"

# a tool-enabled call is refused on a backend without an allowlist (the Jira fetch path)
export MIGITE_AGENT=cursor
rc=0; printf 'x' | claude_print "jira" --allowedTools "mcp__x" >/dev/null 2>&1 || rc=$?
check "claude_print: --allowedTools on cursor is refused (exit 3) rather than silently running with all tools" test "$rc" = "3"

# failures propagate
export MIGITE_AGENT=cursor
rc=0; printf 'x' | FAKE_AGENT_MODE=error claude_print "err" >/dev/null 2>&1 || rc=$?
check "cursor: is_error result → non-zero exit" test "$rc" != "0"
export MIGITE_AGENT=opencode
rc=0; printf 'x' | FAKE_AGENT_MODE=error claude_print "err" >/dev/null 2>&1 || rc=$?
check "opencode: error event → non-zero exit" test "$rc" != "0"

# interactive command line per backend
pf="$ag_dir/prompt.txt"; printf 'do the thing' > "$pf"
export MIGITE_AGENT=claude
check "interactive: claude → env -u CLAUDECODE claude --permission-mode X -- <prompt>" \
  test "$("$MIGITE_PYTHON" "$MIGITE_HOME/migite_agent.py" --repo-root "$REPO_ROOT" interactive --permission-mode bypassPermissions --prompt-file "$pf")" = "env -u CLAUDECODE claude --permission-mode bypassPermissions -- 'do the thing'"
export MIGITE_AGENT=cursor
check "interactive: cursor → cursor-agent --force <prompt>" \
  test "$("$MIGITE_PYTHON" "$MIGITE_HOME/migite_agent.py" --repo-root "$REPO_ROOT" interactive --permission-mode bypassPermissions --prompt-file "$pf")" = "cursor-agent --force 'do the thing'"
export MIGITE_AGENT=opencode
check "interactive: opencode → opencode --prompt <prompt> --auto" \
  test "$("$MIGITE_PYTHON" "$MIGITE_HOME/migite_agent.py" --repo-root "$REPO_ROOT" interactive --permission-mode acceptEdits --prompt-file "$pf")" = "opencode --prompt 'do the thing' --auto"

# cfg_model_flags with no default model for the backend
( export MIGITE_AGENT=cursor; load_migite_config "$REPO_ROOT" >/dev/null
  check "cfg_model_flags: cursor with no pinned tier → no flags at all" test -z "$(cfg_model_flags think)"
  check "cfg_model: cursor tier unset → empty" test -z "$(cfg_model critic)"
  MIGITE_CFG_MODEL_THINK="gpt-5"; MIGITE_CFG_EFFORT_THINK="xhigh"
  check "cfg_model_flags: pinned model on cursor → --model only, never --effort" test "$(cfg_model_flags think)" = "--model gpt-5" )
( export MIGITE_AGENT=claude; load_migite_config "$REPO_ROOT" >/dev/null
  check "cfg_model_flags: claude keeps --model and --effort" bash -c '[[ "$1" == "--model claude-opus-5-5"* ]]' _ "$(cfg_model_flags think)" )

# doctor names the backend binary
export MIGITE_AGENT=opencode
check "doctor: reports the configured backend binary" \
  bash -c 'cd "$1" && git init -q . && MIGITE_PYTHON="$2" MIGITE_HOME="$3" bash "$3/migite" doctor 2>&1 | grep -q "Agent backend: opencode"' _ "$ag_dir/repo" "$MIGITE_PYTHON" "$MIGITE_HOME"

unset MIGITE_AGENT FAKE_AGENT_ARGV MIGITE_USAGE_LEDGER FAKE_AGENT_MODE XDG_CONFIG_HOME
export HOME="$_ag_home"; PATH="$_ag_path"; REPO_ROOT="$_ag_repo_root"
