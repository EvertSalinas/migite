# tests/agents_test.sh - the bash side of the agent interface: agent_ask and
# agent_think through `python -m migite.agent_cli ask` on each backend, the session command
# run_phase uses, the cached agent description (load_agent_info), neutral
# permission words, named scopes, and doctor's health check. Fake CLIs
# (tests/fake-claude, fake-cursor-agent, fake-opencode) are put first on PATH under
# their real names so no real agent is ever invoked.

if ! "$MIGITE_PYTHON" -c 'import sys' &>/dev/null; then
  echo "  · no working Python at $MIGITE_PYTHON - skipping agent interface checks"
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
_ag_saved_log_dir="${LOG_DIR:-}"

# use_agent <name> - switch backends the way a run does: env, then reload the config.
use_agent() {
  export MIGITE_AGENT="$1"
  load_migite_config "$REPO_ROOT" > /dev/null 2>&1
  LOG_DIR="$_ag_saved_log_dir"
}

for backend in claude cursor opencode; do
  use_agent "$backend"
  case "$backend" in claude) bin=claude ;; cursor) bin=cursor-agent ;; opencode) bin=opencode ;; esac
  check "agent info cached: $backend → binary $bin" test "$(agent_binary)" = "$bin"
  check "agent info cached: MIGITE_AGENT_NAME=$backend" test "${MIGITE_AGENT_NAME:-}" = "$backend"
  : > "$FAKE_AGENT_ARGV"
  out=$(printf 'summarise this' | FAKE_CLAUDE_MODE=envelope FAKE_AGENT_MODE=ok agent_ask "unit-$backend" knowledge)
  case "$backend" in
    claude)   check "agent_ask via $backend returns the result text" bash -c '[[ "$1" == echo:summarise* ]]' _ "$out" ;;
    cursor)   check "agent_ask via $backend returns the result text" bash -c '[[ "$1" == cursor:summarise* ]]' _ "$out"
              check "agent_ask via cursor: headless flags -p --output-format json --trust, no --model" \
                bash -c 'grep -q -- "-p --output-format json --trust" "$1" && ! grep -q -- "--model" "$1"' _ "$FAKE_AGENT_ARGV" ;;
    opencode) check "agent_ask via $backend returns the joined text events" bash -c '[[ "$1" == opencode:summarise*second\ part ]]' _ "$out"
              check "agent_ask via opencode: run --format json, no --model" \
                bash -c 'grep -q "run --format json" "$1" && ! grep -q -- "--model" "$1"' _ "$FAKE_AGENT_ARGV" ;;
  esac
  check "ledger records the $backend call" grep -q "\"label\": \"unit-$backend\"" "$MIGITE_USAGE_LEDGER"
done

# capabilities, from the cached description
use_agent claude;   check "agent_supports: claude can restrict a call to jira.read" agent_supports scope:jira.read
                    check "agent_supports: claude has structured_output"            agent_supports structured_output
use_agent cursor;   check "agent_supports: cursor cannot restrict to jira.read"     not agent_supports scope:jira.read
                    check "agent_supports: cursor lacks structured_output"          not agent_supports structured_output
use_agent opencode; check "agent_supports: opencode reports usage"                  agent_supports usage
( unset MIGITE_AGENT_NAME MIGITE_AGENT_CAPS
  check "agent_supports: works without a cached description (asks the agent CLI module)" agent_supports usage )

# neutral permission words, and Claude Code's names as aliases, on the headless path
use_agent cursor; : > "$FAKE_AGENT_ARGV"
printf 'x' | FAKE_AGENT_MODE=ok agent_ask "perm" knowledge --permission auto >/dev/null
check "cursor: auto → --force" grep -q -- "--force" "$FAKE_AGENT_ARGV"
: > "$FAKE_AGENT_ARGV"
printf 'x' | FAKE_AGENT_MODE=ok agent_ask "perm" knowledge --permission bypassPermissions >/dev/null
check "cursor: the Claude alias bypassPermissions still → --force" grep -q -- "--force" "$FAKE_AGENT_ARGV"
use_agent opencode; : > "$FAKE_AGENT_ARGV"
printf 'x' | FAKE_AGENT_MODE=ok agent_ask "perm" knowledge --permission edits >/dev/null
check "opencode: edits → --auto" grep -q -- "--auto" "$FAKE_AGENT_ARGV"
rc=0; printf 'x' | agent_ask "perm" knowledge --permission sometimes >/dev/null 2>&1 || rc=$?
check "agent_ask: an unknown permission word is an error, not silently ignored" test "$rc" != "0"

# a scoped call is refused on an agent that can't restrict itself (the Jira fetch path)
use_agent cursor; : > "$FAKE_AGENT_ARGV"
rc=0; printf 'x' | agent_ask "jira" jira --scope jira.read >/dev/null 2>&1 || rc=$?
check "agent_ask: --scope jira.read on cursor is refused (exit 3) rather than run with every tool" test "$rc" = "3"
check "agent_ask: the refused call never started the CLI" test ! -s "$FAKE_AGENT_ARGV"
use_agent claude
FAKE_CLAUDE_ARGV="$ag_dir/claude-argv.log"; export FAKE_CLAUDE_ARGV
printf 'x' | FAKE_CLAUDE_MODE=envelope agent_ask "jira" jira --scope jira.read --permission auto >/dev/null
check "claude: jira.read → the two Atlassian read tools, auto → bypassPermissions" \
  bash -c 'grep -q -- "--allowedTools mcp__claude_ai_Atlassian__getJiraIssue mcp__claude_ai_Atlassian__getAccessibleAtlassianResources" "$1" && grep -q -- "--permission-mode bypassPermissions" "$1"' _ "$FAKE_CLAUDE_ARGV"
unset FAKE_CLAUDE_ARGV

# failures propagate
use_agent cursor
rc=0; printf 'x' | FAKE_AGENT_MODE=error agent_ask "err" knowledge >/dev/null 2>&1 || rc=$?
check "cursor: is_error result → non-zero exit" test "$rc" != "0"
use_agent opencode
rc=0; printf 'x' | FAKE_AGENT_MODE=error agent_ask "err" knowledge >/dev/null 2>&1 || rc=$?
check "opencode: error event → non-zero exit" test "$rc" != "0"

# agent_think: spinner wrapper, --quiet, --permission
use_agent cursor; : > "$FAKE_AGENT_ARGV"
think_out="$ag_dir/think.txt"
shown=$(FAKE_AGENT_MODE=ok agent_think --quiet --permission auto "Auto-heal 1" heal "$think_out" "fix it" 2>&1)
check "agent_think: result lands in the outfile" bash -c '[[ "$(cat "$1")" == cursor:fix\ it* ]]' _ "$think_out"
check "agent_think --quiet: the result is not printed" bash -c '[[ "$1" != *"cursor:fix it"* ]]' _ "$shown"
check "agent_think: names the agent in its status line" bash -c '[[ "$1" == *"Cursor is working"* ]]' _ "$shown"
check "agent_think --permission: reaches the CLI" grep -q -- "--force" "$FAKE_AGENT_ARGV"

# the session command run_phase runs, per backend
pf="$ag_dir/prompt.txt"; printf 'do the thing' > "$pf"
session_cmd() { "$MIGITE_PYTHON" -m migite.agent_cli --repo-root "$REPO_ROOT" session --prompt-file "$pf" "$@"; }
use_agent claude
check "session: claude → env -u CLAUDECODE claude --permission-mode bypassPermissions -- <prompt>" \
  test "$(session_cmd --permission auto)" = "env -u CLAUDECODE claude --permission-mode bypassPermissions -- 'do the thing'"
check "session: no --permission → permissions.interactive (auto by default)" \
  test "$(session_cmd)" = "env -u CLAUDECODE claude --permission-mode bypassPermissions -- 'do the thing'"
use_agent cursor
check "session: cursor → cursor-agent --force <prompt>" \
  test "$(session_cmd --permission auto)" = "cursor-agent --force 'do the thing'"
use_agent opencode
check "session: opencode → opencode --prompt <prompt> --auto" \
  test "$(session_cmd --permission edits)" = "opencode --prompt 'do the thing' --auto"
check "session: plan on opencode → no approval flag" \
  test "$(session_cmd --permission plan)" = "opencode --prompt 'do the thing'"
big="$ag_dir/big.txt"; head -c 200000 /dev/zero | tr '\0' 'x' > "$big"
bigcmd=$("$MIGITE_PYTHON" -m migite.agent_cli --repo-root "$REPO_ROOT" session --prompt-file "$big" 2>/dev/null)
check "session: a prompt over ui.prompt_inline_max becomes a pointer to its prompt file" \
  bash -c '[[ "$1" == *"Your task brief is in the file $2"* && ${#1} -lt 2000 ]]' _ "$bigcmd" "$big"

# the exit hint and instruction files come from the adapter
use_agent claude;   check "exit hint: claude → /exit"   test "$(agent_field exit_hint)" = "/exit"
                    check "instruction files: claude → CLAUDE.md" test "$(agent_field instruction_files)" = "CLAUDE.md"
use_agent opencode; check "instruction files: opencode → AGENTS.md" test "$(agent_field instruction_files)" = "AGENTS.md"

# doctor names the backend and runs the adapter's health check
export MIGITE_AGENT=opencode
check "doctor: reports the configured backend and finds its CLI" \
  bash -c 'cd "$1" && git init -q . && MIGITE_PYTHON="$2" MIGITE_HOME="$3" bash "$3/bin/migite" doctor 2>&1 | grep -q "Agent backend: opencode" && MIGITE_PYTHON="$2" MIGITE_HOME="$3" bash "$3/bin/migite" doctor 2>&1 | grep -q "✔ Agent CLI: OpenCode"' _ "$ag_dir/repo" "$MIGITE_PYTHON" "$MIGITE_HOME"
check "doctor: a missing agent CLI is reported as an issue" \
  bash -c 'cd "$1" && PATH="/usr/bin:/bin" MIGITE_AGENT=cursor MIGITE_PYTHON="$2" MIGITE_HOME="$3" bash "$3/bin/migite" doctor 2>&1 | grep -q "✘ Agent CLI not found: cursor-agent"' _ "$ag_dir/repo" "$MIGITE_PYTHON" "$MIGITE_HOME"

unset MIGITE_AGENT FAKE_AGENT_ARGV MIGITE_USAGE_LEDGER FAKE_AGENT_MODE XDG_CONFIG_HOME
unset MIGITE_AGENT_NAME MIGITE_AGENT_DISPLAY MIGITE_AGENT_BINARY MIGITE_AGENT_EXIT_HINT MIGITE_AGENT_INSTRUCTIONS MIGITE_AGENT_CAPS
unset -f use_agent session_cmd
export HOME="$_ag_home"; PATH="$_ag_path"; REPO_ROOT="$_ag_repo_root"; LOG_DIR="$_ag_saved_log_dir"
