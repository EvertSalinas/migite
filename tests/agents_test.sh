# tests/agents_test.sh - the bash side of the agent interface: agent_ask and
# agent_think through `python -m migite.agent_cli ask` on each backend, the session command
# run_phase uses, the cached agent description (load_agent_info), neutral
# permission words, named scopes, and doctor's health check. Fake CLIs
# (tests/fake-claude, fake-cursor-agent, fake-kimi, fake-opencode) are put first on PATH under
# their real names so no real agent is ever invoked.

if ! "$MIGITE_PYTHON" -c 'import sys' &>/dev/null; then
  echo "  · no working Python at $MIGITE_PYTHON - skipping agent interface checks"
  return 0 2>/dev/null || exit 0
fi

ag_dir=$(mktemp -d); CLEANUP_DIRS+=("$ag_dir")
mkdir -p "$ag_dir/bin" "$ag_dir/repo" "$ag_dir/home/.config/migite"
chmod +x "$SCRIPT_DIR"/fake-claude "$SCRIPT_DIR"/fake-cursor-agent "$SCRIPT_DIR"/fake-opencode "$SCRIPT_DIR"/fake-kimi
ln -sf "$SCRIPT_DIR/fake-claude"       "$ag_dir/bin/claude"
ln -sf "$SCRIPT_DIR/fake-cursor-agent" "$ag_dir/bin/cursor-agent"
ln -sf "$SCRIPT_DIR/fake-opencode"     "$ag_dir/bin/opencode"
ln -sf "$SCRIPT_DIR/fake-kimi"         "$ag_dir/bin/kimi"
_ag_path="$PATH"; PATH="$ag_dir/bin:$PATH"
_ag_home="$HOME"; _ag_xdg="${XDG_CONFIG_HOME:-}"
export HOME="$ag_dir/home" XDG_CONFIG_HOME="$ag_dir/home/.config"
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

for backend in claude cursor kimi opencode; do
  use_agent "$backend"
  case "$backend" in claude) bin=claude ;; cursor) bin=cursor-agent ;; kimi) bin=kimi ;; opencode) bin=opencode ;; esac
  check "agent info cached: $backend → binary $bin" test "$(agent_binary)" = "$bin"
  check "agent info cached: MIGITE_AGENT_NAME=$backend" test "${MIGITE_AGENT_NAME:-}" = "$backend"
  : > "$FAKE_AGENT_ARGV"
  out=$(printf 'summarise this' | FAKE_CLAUDE_MODE=envelope FAKE_AGENT_MODE=ok agent_ask "unit-$backend" knowledge)
  case "$backend" in
    claude)   check "agent_ask via $backend returns the result text" bash -c '[[ "$1" == echo:summarise* ]]' _ "$out" ;;
    cursor)   check "agent_ask via $backend returns the result text" bash -c '[[ "$1" == cursor:summarise* ]]' _ "$out"
              check "agent_ask via cursor: headless flags -p --output-format json --trust, no --model" \
                bash -c 'grep -q -- "-p --output-format json --trust" "$1" && ! grep -q -- "--model" "$1"' _ "$FAKE_AGENT_ARGV" ;;
    kimi)     check "agent_ask via $backend returns the final assistant text" bash -c '[[ "$1" == kimi:summarise* ]]' _ "$out"
              check "agent_ask via kimi: -p --output-format stream-json, no --model" \
                bash -c 'grep -q -- "-p summarise this --output-format stream-json" "$1" && ! grep -q -- "--model" "$1"' _ "$FAKE_AGENT_ARGV" ;;
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
check "session: claude → env -u CLAUDECODE claude --permission-mode bypassPermissions --model <strong> -- <prompt>" \
  test "$(session_cmd --permission auto)" = "env -u CLAUDECODE claude --permission-mode bypassPermissions --model claude-opus-5-5 -- 'do the thing'"
check "session: no --permission → permissions.interactive (auto by default)" \
  test "$(session_cmd)" = "env -u CLAUDECODE claude --permission-mode bypassPermissions --model claude-opus-5-5 -- 'do the thing'"
use_agent cursor
check "session: cursor → cursor-agent --force <prompt>" \
  test "$(session_cmd --permission auto)" = "cursor-agent --force 'do the thing'"
use_agent opencode
check "session: opencode → opencode --prompt <prompt> --auto" \
  test "$(session_cmd --permission edits)" = "opencode --prompt 'do the thing' --auto"
check "session: plan on opencode → its read-only plan agent, no approval flag" \
  test "$(session_cmd --permission plan)" = "opencode --prompt 'do the thing' --agent plan"
use_agent kimi
check "session: kimi runs the phase headless (kimi -p <prompt>, no permission flag)" \
  test "$(session_cmd --permission plan)" = "kimi -p 'do the thing'"

# the config's model reaches the session command — otherwise opencode resumes whatever
# model its last session in this directory used
cat > "$ag_dir/home/.config/migite/config.yml" <<'YAML'
models:
  strong: deepseek/deepseek-flash
YAML
use_agent opencode
check "session: opencode pins the config's model" \
  test "$(session_cmd --permission edits)" = "opencode --prompt 'do the thing' --auto --model deepseek/deepseek-flash"
use_agent kimi
check "session: the pinned model reaches every backend's session" \
  test "$(session_cmd --permission plan)" = "kimi --model deepseek/deepseek-flash -p 'do the thing'"
rm "$ag_dir/home/.config/migite/config.yml"
use_agent kimi
big="$ag_dir/big.txt"; head -c 200000 /dev/zero | tr '\0' 'x' > "$big"
bigcmd=$("$MIGITE_PYTHON" -m migite.agent_cli --repo-root "$REPO_ROOT" session --prompt-file "$big" 2>/dev/null)
check "session: a prompt over ui.prompt_inline_max becomes a pointer to its prompt file" \
  bash -c '[[ "$1" == *"Your task brief is in the file $2"* && ${#1} -lt 2000 ]]' _ "$bigcmd" "$big"

# the exit hint and instruction files come from the adapter
use_agent claude;   check "exit hint: claude → /exit"   test "$(agent_field exit_hint)" = "/exit"
                    check "instruction files: claude → CLAUDE.md" test "$(agent_field instruction_files)" = "CLAUDE.md"
use_agent opencode; check "instruction files: opencode → AGENTS.md" test "$(agent_field instruction_files)" = "AGENTS.md"
use_agent kimi;     check "session mode: kimi → headless" test "$(agent_field session_mode)" = "headless"
                    check "instruction files: kimi → AGENTS.md" test "$(agent_field instruction_files)" = "AGENTS.md"

# run_phase opens a named interactive session (inline here: TMUX unset), and when it ends
# what it spent is read back from the CLI's transcript into the ledger as session:<label>.
# fake-claude writes one Opus turn on two lines: 10 in, 500 out, 1,000 cache read, 2,000
# written for 1h = $0.02624, counted once.
_ag_claude_config="${CLAUDE_CONFIG_DIR-unset}"
export CLAUDE_CONFIG_DIR="$ag_dir/claude-config"
use_agent claude
ip_ledger="$ag_dir/interactive-usage.jsonl" ip_argv="$ag_dir/interactive-argv.txt"
ip_id_file="$LOG_DIR/$TIMESTAMP-session-id-Implementing.txt"
shown=$(cd "$REPO_ROOT" && TMUX='' MIGITE_USAGE_LEDGER="$ip_ledger" FAKE_CLAUDE_ARGV="$ip_argv" \
  run_phase "Implementing" "$ag_dir/ip-notes.md" "build the thing" 2>&1 < /dev/null)
check "run_phase: the session is named, and the id on its command line is the one handed back" \
  bash -c '[[ -s "$1" ]] && grep -q -- "--session-id $(cat "$1") -- " "$2"' _ "$ip_id_file" "$ip_argv"
check "run_phase: the ended session is in the usage ledger as session:<label>, kind session" \
  bash -c 'grep "\"label\": \"session:Implementing\"" "$1" | grep -q "\"kind\": \"session\""' _ "$ip_ledger"
check "run_phase: the session's tokens are counted once and priced (\$0.02624)" \
  bash -c 'grep -q "\"output_tokens\": 500,.*\"cost_usd\": 0.02624,.*\"turns\": 1," "$1"' _ "$ip_ledger"
check "run_phase: says the session was metered" bash -c '[[ "$1" == *"session:Implementing metered"* ]]' _ "$shown"
rc=0; ( cd "$REPO_ROOT" && TMUX='' MIGITE_USAGE_LEDGER="$ip_ledger" FAKE_CLAUDE_SESSION_EXIT=3 \
  run_phase "Implementing" "$ag_dir/ip-notes.md" "build the thing" >/dev/null 2>&1 < /dev/null ) || rc=$?
check "run_phase: a session that exits non-zero is still metered, and its status comes back" \
  bash -c '[[ "$1" == 3 && "$(grep -c "session:Implementing" "$2")" == 2 ]]' _ "$rc" "$ip_ledger"
use_agent cursor
shown=$( { TMUX='' MIGITE_USAGE_LEDGER="$ag_dir/cursor-usage.jsonl" run_phase "Implementing" "$ag_dir/ip-notes.md" "x"
           TMUX='' MIGITE_USAGE_LEDGER="$ag_dir/cursor-usage.jsonl" run_phase "PR description" "$ag_dir/ip-pr.md" "x"; } 2>&1 < /dev/null)
check "run_phase: an agent that can't report sessions says so once per run" \
  test "$(grep -c "can't report an interactive session's usage" <<< "$shown")" = 1
check "run_phase: ...and records no session line" not test -e "$ag_dir/cursor-usage.jsonl"
check "commit gate banner: the running cost says it leaves sessions out on such an agent" \
  grep -q "so far (headless calls only)" <<< "$(MIGITE_USAGE_LEDGER="$ip_ledger" show_commit_context 2>&1)"
use_agent claude
check "commit gate banner: ...and that it includes them on one that reports them" \
  grep -q "so far (headless calls and sessions)" <<< "$(MIGITE_USAGE_LEDGER="$ip_ledger" show_commit_context 2>&1)"
if [[ "$_ag_claude_config" == unset ]]; then unset CLAUDE_CONFIG_DIR; else export CLAUDE_CONFIG_DIR="$_ag_claude_config"; fi
unset ip_ledger ip_argv ip_id_file _ag_claude_config

# run_phase under --automata: every backend's session is one headless ask on the
# session role, with the CLI's full toolset, in the usage ledger
use_agent claude
ap_argv="$ag_dir/claude-argv.txt" ap_out="$ag_dir/impl-notes.md" ap_ledger="$ag_dir/automata-usage.jsonl"
shown=$(MIGITE_USAGE_LEDGER="$ap_ledger" MIGITE_AUTOMATA=true FAKE_CLAUDE_MODE=envelope FAKE_CLAUDE_ARGV="$ap_argv" run_phase "Implementing" "$ap_out" "build the thing" 2>&1)
check "run_phase --automata: claude runs headless on the session role (--print, bypassPermissions, the strong model)" \
  grep -q -- "--print --output-format json --model claude-opus-5-5 --permission-mode bypassPermissions" "$ap_argv"
check "run_phase --automata: the session keeps the CLI's full toolset (not an isolated call)" \
  not grep -qE -- "--safe-mode|--tools" "$ap_argv"
check "run_phase --automata: the session is in the usage ledger once, as session:<label>, kind session" \
  bash -c '[[ "$(grep -c "\"label\": \"session:Implementing\"" "$1")" == 1 ]] && grep -q "\"kind\": \"session\"" "$1"' _ "$ap_ledger"
check "run_phase --automata: a session that wrote no output file leaves its reply there" \
  bash -c '[[ "$(cat "$1")" == echo:build\ the\ thing* && "$2" == *"its final reply is saved there"* ]]' _ "$ap_out" "$shown"
check "run_phase --automata: says it runs headless, with no exit hint to type" \
  bash -c '[[ "$1" == *"headless (--automata)"* && "$1" != *"/exit"* ]]' _ "$shown"
printf 'notes the session wrote\n' > "$ap_out"
MIGITE_AUTOMATA=true FAKE_CLAUDE_MODE=envelope run_phase "Implementing" "$ap_out" "build the thing" >/dev/null 2>&1
check "run_phase --automata: an output file the session wrote is kept" test "$(cat "$ap_out")" = "notes the session wrote"
out=$( (MIGITE_AUTOMATA=true FAKE_CLAUDE_MODE=exit1 run_phase "Implementing" "$ap_out" "build the thing") 2>&1 ); rc=$?
check "run_phase --automata: a failed session stops the run" \
  bash -c '[[ "$1" == 1 && "$2" == *"The '"'"'Implementing'"'"' session failed"* ]]' _ "$rc" "$out"
use_agent kimi; : > "$FAKE_AGENT_ARGV"
MIGITE_AUTOMATA=true FAKE_AGENT_MODE=ok run_phase "Implementing" "$ag_dir/kimi-notes.md" "build the thing" >/dev/null 2>&1
check "run_phase --automata: kimi goes through its headless ask too (-p ... --output-format stream-json)" \
  grep -q -- "-p build the thing --output-format stream-json" "$FAKE_AGENT_ARGV"
unset ap_argv ap_out ap_ledger

# doctor names the backend and runs the adapter's health check
export MIGITE_AGENT=opencode
check "doctor: reports the configured backend and finds its CLI" \
  bash -c 'cd "$1" && git init -q . && MIGITE_PYTHON="$2" MIGITE_HOME="$3" bash "$3/bin/migite" doctor 2>&1 | grep -q "Agent backend: opencode" && MIGITE_PYTHON="$2" MIGITE_HOME="$3" bash "$3/bin/migite" doctor 2>&1 | grep -q "✔ Agent CLI: OpenCode"' _ "$ag_dir/repo" "$MIGITE_PYTHON" "$MIGITE_HOME"
check "doctor: a missing agent CLI is reported as an issue" \
  bash -c 'cd "$1" && PATH="/usr/bin:/bin" MIGITE_AGENT=cursor MIGITE_PYTHON="$2" MIGITE_HOME="$3" bash "$3/bin/migite" doctor 2>&1 | grep -q "✘ Agent CLI not found: cursor-agent"' _ "$ag_dir/repo" "$MIGITE_PYTHON" "$MIGITE_HOME"

unset MIGITE_AGENT FAKE_AGENT_ARGV MIGITE_USAGE_LEDGER FAKE_AGENT_MODE
unset MIGITE_AGENT_NAME MIGITE_AGENT_DISPLAY MIGITE_AGENT_BINARY MIGITE_AGENT_EXIT_HINT MIGITE_AGENT_INSTRUCTIONS MIGITE_AGENT_CAPS
unset MIGITE_AGENT_SESSION_MODE
unset -f use_agent session_cmd
# Restore the caller's config isolation — unsetting it here let every later test
# file fall through to the developer's real ~/.config/migite/config.yml.
if [[ -n "${_ag_xdg:-}" ]]; then export XDG_CONFIG_HOME="$_ag_xdg"; else unset XDG_CONFIG_HOME; fi
export HOME="$_ag_home"; PATH="$_ag_path"; REPO_ROOT="$_ag_repo_root"; LOG_DIR="$_ag_saved_log_dir"
