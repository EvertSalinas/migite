#!/usr/bin/env bash
# lib/agent.sh - how bash talks to the configured agent and the Python tools.
#
# Bash never builds an agent CLI's flags or reads its output. Everything goes
# through migite/agent_cli.py (headless asks, interactive sessions, agent info),
# migite/tickets.py (tickets) and migite/gateway.py (JSON envelopes, usage ledger).
# run_phase / spawn_langgraph open the tmux panes the interactive phases and the
# LangGraph tools run in. Expects MIGITE_HOME, MIGITE_PYTHON, LOG_DIR, TIMESTAMP.

# ── The agent interface ───────────────────────────────────────────────────────
# Bash never builds an agent CLI's flags or reads its output. Everything goes
# through migite/agent_cli.py, which picks the agent from agent.backend and hands the
# work to the gateway (migite/gateway.py) and the adapter (migite/agents/<name>.py).
# load_agent_info (config.sh) caches the agent's description as MIGITE_AGENT_*.

# agent_ask <label> <role> [--permission P] [--scope S] [--thinking]
# One headless call on the configured agent: prompt on stdin, result text on
# stdout, one usage line in $MIGITE_USAGE_LEDGER. The role picks the model and
# effort; --permission defaults to permissions.headless. Exit 3 when the agent
# can't honour a --scope (the call is refused, never run with every tool).
agent_ask() {
  local label="$1" role="$2"; shift 2
  "$MIGITE_PYTHON" -m migite.agent_cli --repo-root "${REPO_ROOT:-$PWD}" ask \
    --tool migite --label "$label" --role "$role" "$@"
}

# ticket_cmd <parse|fetch|sources> ... - ticket references and content, from
# whichever source tracker.provider allows (migite/tickets.py). Bash never talks
# to Jira itself.
ticket_cmd() {
  "$MIGITE_PYTHON" -m migite.tickets --repo-root "${REPO_ROOT:-$PWD}" "$@"
}

# agent_field <name> - one field of the agent's description (name, display_name,
# binary, exit_hint, instruction_files, ...), from the cache when loaded.
agent_field() {
  local field="$1" var=""
  case "$field" in
    name) var=MIGITE_AGENT_NAME ;; display_name) var=MIGITE_AGENT_DISPLAY ;;
    binary) var=MIGITE_AGENT_BINARY ;; exit_hint) var=MIGITE_AGENT_EXIT_HINT ;;
    instruction_files) var=MIGITE_AGENT_INSTRUCTIONS ;;
  esac
  if [[ -n "$var" && -n "${!var:-}" ]]; then
    echo "${!var}"
  else
    "$MIGITE_PYTHON" -m migite.agent_cli --repo-root "${REPO_ROOT:-$PWD}" info --field "$field" 2>/dev/null
  fi
}

# agent_binary - the configured agent's executable (or agent.command).
agent_binary() {
  agent_field binary || echo claude
}

# agent_supports <capability> - structured_output | effort | usage | scope:<name>
agent_supports() {
  local cap="$1" caps
  if [[ -n "${MIGITE_AGENT_NAME:-}" ]]; then
    caps="${MIGITE_AGENT_CAPS:-}"
  else
    caps="$("$MIGITE_PYTHON" -m migite.agent_cli --repo-root "${REPO_ROOT:-$PWD}" info --shell 2>/dev/null \
      | sed -n "s/^MIGITE_AGENT_CAPS=//p" | tr -d "'")"
  fi
  [[ " $caps " == *" $cap "* ]]
}

# json_field <file.json> <dotted.key> — prints one value; exit 1 if the file,
# key, or JSON is missing/invalid. Bash-side reader for plan.json/review.json/
# usage.json so no call site has to parse JSON with grep.
json_field() {
  local file="$1" key="$2"
  [[ -f "$file" ]] || return 1
  "$MIGITE_PYTHON" -m migite.gateway field "$file" "$key" 2>/dev/null
}

# run_cost_so_far — "<N> calls, $X.XX" from the run's usage ledger, or exit 1
# when there's no ledger yet. Headless calls only (interactive sessions aren't
# metered — see print_usage_summary).
run_cost_so_far() {
  [[ -n "${MIGITE_USAGE_LEDGER:-}" && -s "$MIGITE_USAGE_LEDGER" ]] || return 1
  local tmp calls cost
  tmp=$(mktemp)
  "$MIGITE_PYTHON" -m migite.gateway summary --ledger "$MIGITE_USAGE_LEDGER" --json "$tmp" >/dev/null 2>&1 || { rm -f "$tmp"; return 1; }
  calls=$(json_field "$tmp" total.calls || echo 0)
  cost=$(json_field "$tmp" total.cost_usd || echo 0)
  rm -f "$tmp"
  printf '%s calls, $%.2f' "$calls" "$cost"
}

# print_usage_summary — end-of-run table of every headless model call (by
# model: calls, tokens, time, cost), written to <scratchpad>/usage.json and
# mirrored to the vault when a task dir is known. Runs from migite's EXIT trap
# so an aborted run still reports what it spent. Guarded to run once.
print_usage_summary() {
  [[ "${_USAGE_SUMMARY_PRINTED:-}" == "1" ]] && return 0
  _USAGE_SUMMARY_PRINTED=1
  [[ -n "${MIGITE_USAGE_LEDGER:-}" && -s "$MIGITE_USAGE_LEDGER" ]] || return 0
  echo ""
  echo -e "${BOLD}── Usage ───────────────────────────────────────${RESET}"
  if [[ -n "${SCRATCHPAD_DIR:-}" && -d "$SCRATCHPAD_DIR" ]]; then
    local out="$SCRATCHPAD_DIR/usage.json"
    "$MIGITE_PYTHON" -m migite.gateway summary --ledger "$MIGITE_USAGE_LEDGER" --json "$out" || true
    [[ -n "${TASK_DIR:-}" ]] && sync_json "$out" "$TASK_DIR/usage.json"
  else
    "$MIGITE_PYTHON" -m migite.gateway summary --ledger "$MIGITE_USAGE_LEDGER" || true
  fi
  echo -e "  Ledger: ${CYAN}$MIGITE_USAGE_LEDGER${RESET}"
  echo -e "${BOLD}────────────────────────────────────────────────${RESET}"
}

# write_prompt <label> <prompt> → writes to $LOG_DIR and prints the path
write_prompt() {
  local label="$1"
  local prompt="$2"
  local prompt_file="$LOG_DIR/$TIMESTAMP-prompt-${label// /-}.txt"
  printf '%s' "$prompt" > "$prompt_file"
  echo "$prompt_file"
}

# run_phase <label> <output_file> <prompt> [permission]
# Opens an interactive session on the configured agent, in a tmux pane or inline,
# with the prompt as its first message, and blocks until you exit it. permission
# defaults to `permissions.interactive` (auto unless changed). A prompt too long
# for one command-line argument (ui.prompt_inline_max) is passed as a pointer to
# its prompt file; the gateway decides that, the same way for every agent.
run_phase() {
  local label="$1"
  local outfile="$2"
  local prompt="$3"
  local permission_mode="${4:-${MIGITE_CFG_PERMISSIONS_INTERACTIVE:-auto}}"
  local prompt_file
  prompt_file=$(write_prompt "$label" "$prompt")
  local agent_name exit_hint
  agent_name="$(agent_field display_name || echo "the agent")"
  exit_hint="$(agent_field exit_hint || echo "exit the session")"

  echo ""
  echo -e "${CYAN}  Starting interactive ${agent_name} session: ${BOLD}$label${RESET}"
  echo -e "  ${CYAN}Output file: ${outfile}${RESET}"
  echo -e "  ${CYAN}Permission mode: ${permission_mode}${RESET}"
  echo -e "  ${YELLOW}Type ${exit_hint} when done to return here${RESET}"
  echo ""

  # One shell-quoted command line from the adapter: that CLI's flags for the
  # permission word, the variables it must not inherit, and the first prompt.
  local session_cmd
  session_cmd="$("$MIGITE_PYTHON" -m migite.agent_cli --repo-root "${REPO_ROOT:-$PWD}" session \
    --permission "$permission_mode" --prompt-file "$prompt_file")" \
    || error "Could not build the interactive session command for the configured agent"

  if use_tmux; then
    # Strip everything except alphanumeric and dash — parens/spaces break tmux sh -c parsing
    local safe_label
    safe_label=$(printf '%s' "$label" | tr -cs 'a-zA-Z0-9' '-')
    local channel="migite-${TIMESTAMP}-${safe_label}"
    local wrapper="$LOG_DIR/$TIMESTAMP-wrapper-${safe_label}.sh"
    local orig_pane
    orig_pane=$(tmux display-message -p '#{pane_id}')
    {
      echo "#!/usr/bin/env bash"
      printf '%s\n' "$session_cmd"
      printf 'exit_code=$?\n'
      printf 'if [[ $exit_code -ne 0 ]]; then\n'
      printf '  echo ""\n'
      printf '  echo "  ✘ %s session exited $exit_code, press enter to close this pane"\n' "$agent_name"
      printf '  read -r\n'
      printf 'fi\n'
      printf 'tmux wait-for -S %s\n' "$channel"
    } > "$wrapper"
    chmod +x "$wrapper"
    # Start listening BEFORE the pane opens — avoids losing the signal if the
    # wrapper completes before this line runs (race condition on fast exits)
    tmux wait-for "$channel" &
    local _wait_pid=$!
    local session_pane
    session_pane=$(tmux split-window -P -F '#{pane_id}' -v "bash '$wrapper'")
    while kill -0 "$_wait_pid" 2>/dev/null; do
      if ! tmux list-panes -a -F '#{pane_id}' 2>/dev/null | grep -q "^${session_pane}$"; then
        kill "$_wait_pid" 2>/dev/null || true
        error "Interactive session pane for '$label' was closed — workflow aborted"
      fi
      sleep 1
    done
    wait "$_wait_pid" 2>/dev/null || true
    tmux select-pane -t "$orig_pane" 2>/dev/null || true
    notify "$label" "Session done — continuing workflow"
  else
    eval "$session_cmd"
  fi
}

# agent_think [--quiet] [--permission P] <label> <role> <output_file> <prompt>
# agent_ask in the background with a spinner. The prompt goes in on stdin, the
# result lands in output_file and is printed unless --quiet. Returns the call's
# exit status. The auto-heal loop uses it with --quiet --permission from
# permissions.heal; knowledge, amendments, and testing-plan updates without.
agent_think() {
  local quiet=false
  local -a extra=()
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --quiet) quiet=true; shift ;;
      --permission) extra+=(--permission "$2"); shift 2 ;;
      *) break ;;
    esac
  done
  local label="$1" role="$2" outfile="$3" prompt="$4"
  local prompt_file
  prompt_file=$(write_prompt "$label" "$prompt")
  local frames=('⠋' '⠙' '⠹' '⠸' '⠼' '⠴' '⠦' '⠧' '⠇' '⠏')
  local i=0

  echo ""
  echo -e "${CYAN}  $(agent_field display_name || echo "The agent") is working: ${BOLD}$label${RESET}"
  echo -e "  ${CYAN}Prompt log: ${prompt_file}${RESET}"

  printf '%s' "$prompt" | agent_ask "$label" "$role" ${extra[@]+"${extra[@]}"} > "$outfile" &
  local pid=$!

  while kill -0 "$pid" 2>/dev/null; do
    printf "\r  ${CYAN}${frames[$i]}${RESET}  working..."
    i=$(( (i + 1) % ${#frames[@]} ))
    sleep 0.1
  done
  printf "\r\033[K"  # clear spinner line

  wait "$pid"
  local exit_code=$?

  [[ "$quiet" == "true" ]] || cat "$outfile"
  return $exit_code
}

# spawn_langgraph <label> <channel-suffix> <module> [args...]
# Runs a LangGraph tool (a module under migite/tools/, e.g. migite.tools.plan)
# and blocks until it exits.
# All output (stdout + stderr) is teed to a timestamped log file.
# In tmux: opens a new pane (split below); keeps it open on failure so you can read the error.
# Outside tmux: runs inline, output streams to the terminal.
spawn_langgraph() {
  local label="$1"
  local suffix="$2"
  local module="$3"
  shift 3

  [[ -f "$MIGITE_HOME/${module//.//}.py" ]] || error "LangGraph module not found: $module"
  require_python
  require_langgraph

  local agent_log="$LOG_DIR/$TIMESTAMP-${suffix}-agent.log"

  echo ""
  echo -e "${CYAN}  Starting LangGraph agent: ${BOLD}$label${RESET}"
  echo -e "  ${CYAN}Log: $agent_log${RESET}"

  if use_tmux; then
    local channel="migite-${TIMESTAMP}-${suffix}"
    local wrapper="$LOG_DIR/$TIMESTAMP-wrapper-${suffix}.sh"
    local orig_pane
    orig_pane=$(tmux display-message -p '#{pane_id}')
    {
      echo "#!/usr/bin/env bash"
      # tmux panes inherit the tmux SERVER's environment, not this shell's —
      # re-export the ledger path so the agent's usage lands in this run's file.
      [[ -n "${MIGITE_USAGE_LEDGER:-}" ]] && printf 'export MIGITE_USAGE_LEDGER=%q\n' "$MIGITE_USAGE_LEDGER"
      printf 'export PYTHONPATH=%q\n' "$PYTHONPATH"
      local cmd
      cmd="$(printf '%q' "$MIGITE_PYTHON") -m $(printf '%q' "$module")"
      local arg
      for arg in "$@"; do
        cmd+=" $(printf '%q' "$arg")"
      done
      # Merge stderr into stdout and tee to log — output survives window close
      printf '%s 2>&1 | tee %s\n' "$cmd" "$(printf '%q' "$agent_log")"
      # Keep window open on failure so the error is readable
      printf 'exit_code=${PIPESTATUS[0]}\n'
      printf 'if [[ $exit_code -ne 0 ]]; then\n'
      printf '  echo ""\n'
      printf '  echo "  ✘ Agent exited $exit_code — press enter to close this pane"\n'
      printf '  read -r\n'
      printf 'fi\n'
      printf 'tmux wait-for -S %s\n' "$channel"
    } > "$wrapper"
    chmod +x "$wrapper"
    # Start listening BEFORE the pane opens to avoid missing the signal on fast exits
    tmux wait-for "$channel" &
    local _wait_pid=$!
    local agent_pane
    agent_pane=$(tmux split-window -P -F '#{pane_id}' -v "bash '$wrapper'")
    while kill -0 "$_wait_pid" 2>/dev/null; do
      if ! tmux list-panes -a -F '#{pane_id}' 2>/dev/null | grep -q "^${agent_pane}$"; then
        kill "$_wait_pid" 2>/dev/null || true
        error "Agent pane for '$label' was closed before completing — workflow aborted"
      fi
      sleep 1
    done
    wait "$_wait_pid" 2>/dev/null || true
    tmux select-pane -t "$orig_pane" 2>/dev/null || true
    notify "$label" "Agent done — gate coming up"
  else
    # Inline: merge stderr, tee to log; || true lets sentinel check own the failure
    "$MIGITE_PYTHON" -m "$module" "$@" 2>&1 | tee "$agent_log" || true
  fi
}
