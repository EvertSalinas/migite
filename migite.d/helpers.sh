#!/usr/bin/env bash
# migite.d/helpers.sh — shared output, prompt, and Claude-invocation helpers
#
# Sourced by migite. Expects config vars (LOG_DIR, TIMESTAMP, DATE, MIGITE_PYTHON)
# to already be set. Functions here read $SCRATCHPAD_DIR/$TASK_DIR-family globals
# at call time — they're set by whichever phase is currently running.

# ── Colors ────────────────────────────────────────────────────────────────────
BOLD='\033[1m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
RED='\033[0;31m'
RESET='\033[0m'

# ── Output ────────────────────────────────────────────────────────────────────
log()     { echo -e "${CYAN}▶ $1${RESET}"; }
success() { echo -e "${GREEN}✔ $1${RESET}"; }
warn()    { echo -e "${YELLOW}⚠ $1${RESET}"; }
error()   { echo -e "${RED}✘ $1${RESET}" >&2; exit 1; }
notify()  { osascript -e "display notification \"$2\" with title \"Migite\" subtitle \"$1\" sound name \"Glass\"" 2>/dev/null || true; }

require_cmd() {
  command -v "$1" &>/dev/null || error "$1 is required but not installed"
}

# resolve_path <file> — echoes <file> as an absolute path.
# Used on user-supplied file args before migite cd's to $REPO_ROOT, so paths
# given relative to the invocation directory keep resolving correctly.
resolve_path() {
  local p="$1"
  echo "$(cd "$(dirname "$p")" && pwd)/$(basename "$p")"
}

# detect_base_branch — echoes the repo's default branch for diff scoping.
# origin/HEAD is authoritative when set; otherwise falls back to whichever of
# main/master/develop actually exists locally, so migite works unmodified on
# repos of either convention.
# detect_app_root — sets $APP_ROOT and $APP_REL_PATH.
# Bundler only searches upward from cwd for a Gemfile, never into
# subdirectories. Most repos have it at $REPO_ROOT, but some nest the actual
# Ruby app one level down (e.g. a rails-app/ dir alongside other tooling) —
# without this, every bundle exec call fails with "Could not locate Gemfile".
detect_app_root() {
  if [[ -f "$REPO_ROOT/Gemfile" ]]; then
    APP_ROOT="$REPO_ROOT"
    APP_REL_PATH=""
    return
  fi

  local matches=()
  while IFS= read -r -d '' gemfile; do
    matches+=("$(dirname "$gemfile")")
  done < <(find "$REPO_ROOT" -mindepth 2 -maxdepth 2 -name Gemfile -print0)

  case "${#matches[@]}" in
    0) error "No Gemfile found at $REPO_ROOT or one level down — is this a Ruby project?" ;;
    1)
      APP_ROOT="${matches[0]}"
      APP_REL_PATH="${APP_ROOT#"$REPO_ROOT"/}"
      log "Gemfile not at repo root — using app dir: $APP_REL_PATH"
      ;;
    *) error "Multiple Gemfiles found under $REPO_ROOT (${matches[*]}) — migite doesn't support multi-app monorepos yet" ;;
  esac
}

# strip_app_prefix <files> — rewrites REPO_ROOT-relative paths (as produced by
# `git diff --name-only`) to APP_ROOT-relative paths, so they still resolve
# once bundle/rubocop/rspec run with cwd = APP_ROOT. No-op when the app lives
# at the repo root.
strip_app_prefix() {
  local files="$1"
  if [[ -z "${APP_REL_PATH:-}" ]]; then
    echo "$files"
    return
  fi
  local f stripped=()
  for f in $files; do
    stripped+=("${f#"$APP_REL_PATH"/}")
  done
  echo "${stripped[*]}"
}

# bundle_exec <args...> — runs `bundle exec` with cwd = $APP_ROOT, so it finds
# the Gemfile (and .tool-versions) regardless of where migite was invoked from.
bundle_exec() {
  (cd "$APP_ROOT" && bundle exec "$@")
}

detect_base_branch() {
  local remote_head
  remote_head=$(git symbolic-ref --short -q refs/remotes/origin/HEAD 2>/dev/null || true)
  if [[ -n "$remote_head" ]]; then
    echo "${remote_head#origin/}"
    return
  fi
  local branch
  for branch in main master develop; do
    if git show-ref --verify --quiet "refs/heads/$branch"; then
      echo "$branch"
      return
    fi
  done
  echo "main"
}

slugify() {
  echo "$1" \
    | tr '[:upper:]' '[:lower:]' \
    | sed 's/[^a-z0-9]/-/g' \
    | sed 's/--*/-/g' \
    | sed 's/^-//' \
    | sed 's/-$//' \
    | cut -c1-50
}

# write_prompt <label> <prompt> → writes to $LOG_DIR and prints the path
write_prompt() {
  local label="$1"
  local prompt="$2"
  local prompt_file="$LOG_DIR/$TIMESTAMP-prompt-${label// /-}.txt"
  printf '%s' "$prompt" > "$prompt_file"
  echo "$prompt_file"
}

# run_phase <label> <output_file> <prompt> [permission_mode]
# Opens an interactive Claude Code session in a split pane with the prompt pre-loaded.
# permission_mode defaults to bypassPermissions.
# When done, type /exit to close the pane and return to the workflow.
run_phase() {
  local label="$1"
  local outfile="$2"
  local prompt="$3"
  local permission_mode="${4:-bypassPermissions}"
  local prompt_file
  prompt_file=$(write_prompt "$label" "$prompt")

  echo ""
  echo -e "${CYAN}  Starting interactive session: ${BOLD}$label${RESET}"
  echo -e "  ${CYAN}Output file: ${outfile}${RESET}"
  echo -e "  ${CYAN}Permission mode: ${permission_mode}${RESET}"
  echo -e "  ${YELLOW}Type /exit when done to return here${RESET}"
  echo ""

  if [[ -n "${TMUX:-}" ]]; then
    # Strip everything except alphanumeric and dash — parens/spaces break tmux sh -c parsing
    local safe_label
    safe_label=$(printf '%s' "$label" | tr -cs 'a-zA-Z0-9' '-')
    local channel="migite-${TIMESTAMP}-${safe_label}"
    local wrapper="$LOG_DIR/$TIMESTAMP-wrapper-${safe_label}.sh"
    local orig_pane
    orig_pane=$(tmux display-message -p '#{pane_id}')
    {
      echo "#!/usr/bin/env bash"
      printf 'claude --permission-mode %s -- "$(cat '"'"'%s'"'"')"\n' "$permission_mode" "$prompt_file"
      printf 'exit_code=$?\n'
      printf 'if [[ $exit_code -ne 0 ]]; then\n'
      printf '  echo ""\n'
      printf '  echo "  ✘ Claude session exited $exit_code — press enter to close this pane"\n'
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
    claude --permission-mode "$permission_mode" -- "$(cat "$prompt_file")"
  fi
}

# thinking <label> <output_file> <prompt>
# Runs `claude --print` in the background with a spinner (text-only, no tool use).
# Prompt is passed via stdin to avoid ARG_MAX limits.
thinking() {
  local label="$1"
  local outfile="$2"
  local prompt="$3"
  local extra_flags="${4:-}"
  local prompt_file
  prompt_file=$(write_prompt "$label" "$prompt")
  local frames=('⠋' '⠙' '⠹' '⠸' '⠼' '⠴' '⠦' '⠧' '⠇' '⠏')
  local i=0

  echo ""
  echo -e "${CYAN}  Claude is thinking: ${BOLD}$label${RESET}"
  echo -e "  ${CYAN}Prompt log: ${prompt_file}${RESET}"

  # shellcheck disable=SC2086
  printf '%s' "$prompt" | claude --print $extra_flags > "$outfile" &
  local pid=$!

  while kill -0 "$pid" 2>/dev/null; do
    printf "\r  ${CYAN}${frames[$i]}${RESET}  working..."
    i=$(( (i + 1) % ${#frames[@]} ))
    sleep 0.1
  done
  printf "\r\033[K"  # clear spinner line

  wait "$pid"
  local exit_code=$?

  cat "$outfile"
  return $exit_code
}

# heal_run <label> <outfile> <prompt>
# Non-interactive Claude call with bypassPermissions — used by the auto-heal loop to
# fix rubocop/rspec failures automatically, without blocking for human input.
heal_run() {
  local label="$1"
  local outfile="$2"
  local prompt="$3"
  local prompt_file
  prompt_file=$(write_prompt "$label" "$prompt")
  local frames=('⠋' '⠙' '⠹' '⠸' '⠼' '⠴' '⠦' '⠧' '⠇' '⠏')
  local i=0

  echo ""
  echo -e "${CYAN}  Auto-healing: ${BOLD}$label${RESET}"

  printf '%s' "$prompt" | claude --print --permission-mode bypassPermissions > "$outfile" &
  local pid=$!

  while kill -0 "$pid" 2>/dev/null; do
    printf "\r  ${CYAN}${frames[$i]}${RESET}  healing..."
    i=$(( (i + 1) % ${#frames[@]} ))
    sleep 0.1
  done
  printf "\r\033[K"

  wait "$pid"
  return $?
}

# spawn_langgraph <label> <channel-suffix> <script> [args...]
# Runs a Python LangGraph script and blocks until it exits.
# All output (stdout + stderr) is teed to a timestamped log file.
# In tmux: opens a new pane (split below); keeps it open on failure so you can read the error.
# Outside tmux: runs inline, output streams to the terminal.
spawn_langgraph() {
  local label="$1"
  local suffix="$2"
  local script="$3"
  shift 3

  [[ -f "$script" ]] || error "LangGraph script not found: $script"
  [[ -x "$MIGITE_PYTHON" ]] || error "Python not found at $MIGITE_PYTHON — set MIGITE_PYTHON"

  # Preflight: verify langgraph + anthropic are installed in the target Python
  if ! "$MIGITE_PYTHON" -c "import langgraph, anthropic" 2>/dev/null; then
    error "Python dependencies missing. Run: $MIGITE_PYTHON -m pip install langgraph anthropic"
  fi

  local agent_log="$LOG_DIR/$TIMESTAMP-${suffix}-agent.log"

  echo ""
  echo -e "${CYAN}  Starting LangGraph agent: ${BOLD}$label${RESET}"
  echo -e "  ${CYAN}Log: $agent_log${RESET}"

  if [[ -n "${TMUX:-}" ]]; then
    local channel="migite-${TIMESTAMP}-${suffix}"
    local wrapper="$LOG_DIR/$TIMESTAMP-wrapper-${suffix}.sh"
    local orig_pane
    orig_pane=$(tmux display-message -p '#{pane_id}')
    {
      echo "#!/usr/bin/env bash"
      local cmd
      cmd="$(printf '%q' "$MIGITE_PYTHON") $(printf '%q' "$script")"
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
    "$MIGITE_PYTHON" "$script" "$@" 2>&1 | tee "$agent_log" || true
  fi
}

# stamp_file <file> — prepend created/updated frontmatter, or bump updated if already present
stamp_file() {
  local file="$1"
  [[ -f "$file" ]] || return
  if grep -q "^created:" "$file" 2>/dev/null; then
    sed -i '' "s/^updated: .*/updated: $DATE/" "$file"
  else
    local tmp
    tmp=$(mktemp)
    { printf -- '---\ncreated: %s\nupdated: %s\n---\n\n' "$DATE" "$DATE"; cat "$file"; } > "$tmp"
    mv "$tmp" "$file"
  fi
}

# sync_artifact <file> <vault-dest-path>
# Stamps <file> (the scratchpad-primary copy) with frontmatter, then copies it to
# <vault-dest-path>. Used after every scratchpad-side artifact write (plan/review/
# implementation/PR/knowledge/amendments) so the vault mirror (for reading/browsing,
# e.g. in Obsidian) stays current with the scratchpad (source of truth for the run).
sync_artifact() {
  local file="$1" vault_dest="$2"
  stamp_file "$file"
  mkdir -p "$(dirname "$vault_dest")"
  cp "$file" "$vault_dest"
}

# resume_from_vault <scratchpad-path> <vault-path>
# If the scratchpad copy is missing but a vault copy exists (scratchpad was cleaned,
# fresh clone, different machine), pulls the vault copy in so resuming a task doesn't
# silently start over. No-op if the scratchpad copy already exists or there's nothing
# in the vault to recover.
resume_from_vault() {
  local scratch="$1" vault="$2"
  [[ -f "$scratch" || ! -f "$vault" ]] && return 0
  cp "$vault" "$scratch"
}

# build_knowledge_injection <knowledge-file>
# Renders the "repository conventions" block injected into Plan/Implement/Amend prompts.
# Echoes nothing if the file doesn't exist yet.
build_knowledge_injection() {
  local file="$1"
  [[ -f "$file" ]] || return 0
  printf '## Repository conventions and past lessons\n\nThe following lessons were captured from previous tasks in this repo. Adhere to them strictly before planning or implementing anything.\n\n%s\n\n---\n\n' "$(cat "$file")"
}

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

# run_rubocop_check <files> <log> [autocorrect=false]
# Runs rubocop over <files> (space-separated, from git diff) and tees to <log>;
# writes a fallback message instead of running anything if <files> is empty.
# Callers own the exit-code handling — some swallow failures, some branch on them.
run_rubocop_check() {
  local files="$1" log="$2" autocorrect="${3:-false}"
  if [[ -z "$files" ]]; then
    echo "No Ruby files changed." > "$log"
    return 0
  fi
  local app_files
  app_files=$(strip_app_prefix "$files")
  if [[ "$autocorrect" == "true" ]]; then
    # shellcheck disable=SC2086
    bundle_exec rubocop -a $app_files --format progress 2>&1 | tee "$log"
  else
    # shellcheck disable=SC2086
    bundle_exec rubocop $app_files --format progress 2>&1 | tee "$log"
  fi
}

# run_rspec_check <files> <log>
# Runs rspec over <files> and tees to <log>; writes a fallback message if <files> is empty.
run_rspec_check() {
  local files="$1" log="$2"
  if [[ -z "$files" ]]; then
    echo "No spec files changed." > "$log"
    return 0
  fi
  local app_files
  app_files=$(strip_app_prefix "$files")
  # shellcheck disable=SC2086
  bundle_exec rspec $app_files 2>&1 | tee "$log"
}

show_commit_context() {
  echo ""
  echo -e "${BOLD}── Commit context ──────────────────────────${RESET}"

  # Ruby version / tool-version errors (surfaced from rubocop or rspec runs)
  if [[ "${RUBY_VERSION_ERROR:-false}" == "true" ]]; then
    echo -e "  ${RED}${BOLD}⚠ Ruby version error — bundle exec could not run. Fix .tool-versions before approving.${RESET}"
  fi

  # Review verdict
  if [[ -f "${REVIEW_FILE:-}" ]]; then
    local verdict_line
    verdict_line=$(grep -m1 'NEEDS FIXES\|APPROVED\|PASS\|READY TO COMMIT' "$REVIEW_FILE" 2>/dev/null || echo "")
    if echo "$verdict_line" | grep -q 'NEEDS FIXES'; then
      echo -e "  Verdict: ${RED}${BOLD}NEEDS FIXES${RESET}"
    elif echo "$verdict_line" | grep -qE 'APPROVED|PASS|READY TO COMMIT'; then
      echo -e "  Verdict: ${GREEN}${BOLD}APPROVED${RESET}"
    else
      echo -e "  Verdict: ${YELLOW}unknown — check $REVIEW_FILE${RESET}"
    fi
  fi

  # Spec failures / DB connection issues
  if [[ -f "${RSPEC_LOG:-}" ]]; then
    local failure_line
    failure_line=$(grep -oE '[0-9]+ failure[s]?' "$RSPEC_LOG" | head -1 || echo "")
    if [[ -n "$failure_line" ]]; then
      echo -e "  Specs:   ${RED}${BOLD}⚠ $failure_line — check $RSPEC_LOG before approving${RESET}"
    elif grep -q '0 examples' "$RSPEC_LOG" && grep -qi 'connection\|ConnectionBad' "$RSPEC_LOG"; then
      echo -e "  Specs:   ${RED}${BOLD}⚠ DB connection failed — 0 examples ran, no coverage verified${RESET}"
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

  echo -e "${BOLD}────────────────────────────────────────────${RESET}"
  echo ""
}
