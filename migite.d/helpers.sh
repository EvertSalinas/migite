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
# notify <subtitle> <message> — desktop notification, best effort. macOS via
# osascript, Linux via notify-send, silent no-op anywhere else. Never fails the run.
notify() {
  [[ "${MIGITE_CFG_UI_NOTIFY:-auto}" == "off" ]] && return 0
  if command -v osascript &>/dev/null; then
    osascript -e "display notification \"$2\" with title \"Migite\" subtitle \"$1\" sound name \"Glass\"" 2>/dev/null || true
  elif command -v notify-send &>/dev/null; then
    notify-send "Migite — $1" "$2" 2>/dev/null || true
  fi
}

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

# ── Stack profiles ────────────────────────────────────────────────────────────
# A stack is a `stack_<name>_detect` / `stack_<name>_app_root` function pair.
# detect_stack() tries STACK_PROFILES in order and dispatches to the first
# match — adding a stack means registering one more pair here, not adding a
# branch to detect_stack() or to any caller. `generic` must stay last — it's
# the catch-all that lets migite run on any project, not just Rails: review.sh
# skips rubocop/rspec entirely when $STACK == "generic", and migite-plan uses
# a generic file-glob set instead of the Rails-MVC EXPLORE_AREAS.
STACK_PROFILES=(rails generic)

# stack_rails_detect — true if a Gemfile exists at $REPO_ROOT or exactly one
# level down. Detection only, no side effects — stack_rails_app_root (below)
# does the actual, more detailed resolution once this stack is selected.
stack_rails_detect() {
  [[ -f "$REPO_ROOT/Gemfile" ]] && return 0
  find "$REPO_ROOT" -mindepth 2 -maxdepth 2 -name Gemfile -print -quit 2>/dev/null | grep -q .
}

# stack_rails_app_root — sets $APP_ROOT and $APP_REL_PATH.
# Bundler only searches upward from cwd for a Gemfile, never into
# subdirectories. Most repos have it at $REPO_ROOT, but some nest the actual
# Ruby app one level down (e.g. a rails-app/ dir alongside other tooling) —
# without this, every bundle exec call fails with "Could not locate Gemfile".
stack_rails_app_root() {
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

# stack_generic_detect — always matches. The catch-all for any repo that
# isn't a recognized stack; must stay last in STACK_PROFILES.
stack_generic_detect() { return 0; }

# stack_generic_app_root — no Gemfile-style subdirectory nesting to resolve;
# the app root is always the repo root.
stack_generic_app_root() {
  APP_ROOT="$REPO_ROOT"
  APP_REL_PATH=""
}

# detect_stack — sets $STACK, then dispatches to the matched profile's
# _app_root to set $APP_ROOT/$APP_REL_PATH. Honors an explicit $STACK_OVERRIDE
# (set via migite's --stack flag) before trying STACK_PROFILES in order.
detect_stack() {
  if [[ -n "${STACK_OVERRIDE:-}" ]]; then
    if [[ ! " ${STACK_PROFILES[*]} " == *" $STACK_OVERRIDE "* ]]; then
      error "Unknown stack '$STACK_OVERRIDE' — supported: ${STACK_PROFILES[*]}"
    fi
    STACK="$STACK_OVERRIDE"
    "stack_${STACK}_app_root"
    return
  fi

  local s
  for s in "${STACK_PROFILES[@]}"; do
    if "stack_${s}_detect"; then
      STACK="$s"
      "stack_${s}_app_root"
      return
    fi
  done
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

# changed_ruby_files/changed_spec_files <base_branch> — echo changed .rb /
# _spec.rb files, tracked (git diff --diff-filter=ACMR) union untracked (git
# ls-files --others). A file that hasn't been `git add`-ed yet is invisible to
# `git diff`, and thus to rubocop/rspec — this exact blind spot has recurred
# at least five times in production (migite-improvements.md: 2026-07-23,
# 2026-08-05, 2026-08-11, 2026-08-12, 2026-08-17), each time fixed at one call
# site and not the others. One shared function, used everywhere the two are
# needed, so it can't drift out of sync again.
changed_ruby_files() {
  local base_branch="$1"
  { git diff "$base_branch" --name-only --diff-filter=ACMR
    git ls-files --others --exclude-standard
  } | grep '\.rb$' | sort -u
}

changed_spec_files() {
  local base_branch="$1"
  { git diff "$base_branch" --name-only --diff-filter=ACMR
    git ls-files --others --exclude-standard
  } | grep '_spec\.rb$' | sort -u
}

# changed_source_files <base_branch> — changed .rb files that are NOT specs.
# The heal loop autocorrects only source files (specs are rspec's job there).
changed_source_files() {
  local base_branch="$1"
  changed_ruby_files "$base_branch" | grep -v '_spec\.rb$' || true
}

# changed_all_files <base_branch> — every changed path, any extension, tracked
# union untracked, minus migite's own scratchpad/. Used for the diff-vs-notes
# cross-reference warning before review.
changed_all_files() {
  local base_branch="$1"
  { git diff "$base_branch" --name-only --diff-filter=ACMR
    git ls-files --others --exclude-standard
  } | grep -v '^scratchpad/' | sort -u
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

# slugify <text> — lowercase, every run of non-[a-z0-9] becomes one "-",
# no leading/trailing "-", max 50 chars, and no trailing "-" left by the cut.
# This is the CANONICAL slug definition: migite_paths.slugify (Python) is
# kept byte-for-byte compatible and tests/slugify_test.sh checks parity, so
# vault folders created by `migite` (bash) and looked up by the standalone
# tools (Python) always agree. There used to be four implementations that
# disagreed on "_" and "+".
slugify() {
  echo "$1" \
    | tr '[:upper:]' '[:lower:]' \
    | sed 's/[^a-z0-9]/-/g' \
    | sed 's/--*/-/g' \
    | sed 's/^-//' \
    | sed 's/-$//' \
    | cut -c1-50 \
    | sed 's/-$//'
}

# recent_task_dirs <parent> [n=10] — basenames of the n most recently modified
# subdirectories of <parent>, newest first. `ls -td` is the portable way to
# sort by mtime; the previous `find -exec stat -f '%m %N'` was BSD-only
# (GNU stat uses -c) and broke the amend picker on Linux.
recent_task_dirs() {
  local parent="$1" n="${2:-10}"
  [[ -d "$parent" ]] || return 0
  # shellcheck disable=SC2012
  ls -1td "$parent"/*/ 2>/dev/null | head -n "$n" | while IFS= read -r d; do
    basename "${d%/}"
  done
}

# write_prompt <label> <prompt> → writes to $LOG_DIR and prints the path
write_prompt() {
  local label="$1"
  local prompt="$2"
  local prompt_file="$LOG_DIR/$TIMESTAMP-prompt-${label// /-}.txt"
  printf '%s' "$prompt" > "$prompt_file"
  echo "$prompt_file"
}

# Every `claude` launch below runs under `env -u CLAUDECODE`. Claude Code sets
# that variable in its own sessions, and a nested `claude` refuses to start
# while it's set — the Python agents and the Jira fetch already strip it, but
# run_phase/heal_run/thinking didn't, so knowledge capture, self-improvement,
# amendments, and the heal loop all failed whenever migite was launched from
# inside a Claude Code terminal. One wrapper, used everywhere, so it can't
# drift again.
claude_cmd() {
  env -u CLAUDECODE claude "$@"
}

# claude_print <label> [claude flags...] — headless call, prompt on stdin,
# result TEXT on stdout. Always runs `--output-format json` under the hood and
# pipes the envelope through migite_claude.py `extract`, which prints the
# `result` and appends one usage line (tokens, cost, duration, model) to
# $MIGITE_USAGE_LEDGER. Callers must NOT pass --output-format themselves.
# Falls back to passing stdout through untouched if the CLI didn't return the
# JSON envelope (older CLI, plain-text error), so nothing downstream changes.
claude_print() {
  local label="$1"; shift
  local raw rc=0 xrc=0
  raw=$(mktemp)
  # permissions.headless from the config applies unless the caller passed its own
  # --permission-mode (the Jira fetch does, with a scoped tool allowlist).
  local -a perm=()
  local headless="${MIGITE_CFG_PERMISSIONS_HEADLESS:-none}"
  if [[ "$headless" != "none" && " $* " != *" --permission-mode "* ]]; then
    perm=(--permission-mode "$headless")
  fi
  claude_cmd --print --output-format json "${perm[@]}" "$@" > "$raw" || rc=$?
  "$MIGITE_PYTHON" "$MIGITE_HOME/migite_claude.py" extract --tool migite --label "$label" --exit-code "$rc" < "$raw" || xrc=$?
  rm -f "$raw"
  return "$xrc"
}

# json_field <file.json> <dotted.key> — prints one value; exit 1 if the file,
# key, or JSON is missing/invalid. Bash-side reader for plan.json/review.json/
# usage.json so no call site has to parse JSON with grep.
json_field() {
  local file="$1" key="$2"
  [[ -f "$file" ]] || return 1
  "$MIGITE_PYTHON" "$MIGITE_HOME/migite_claude.py" field "$file" "$key" 2>/dev/null
}

# sync_json <file.json> <vault-dest> — mirror a JSON artifact to the vault.
# NOT sync_artifact: stamp_file would prepend markdown frontmatter and corrupt it.
sync_json() {
  local file="$1" vault_dest="$2"
  [[ -f "$file" ]] || return 0
  mkdir -p "$(dirname "$vault_dest")"
  cp "$file" "$vault_dest"
}

# run_cost_so_far — "<N> calls, $X.XX" from the run's usage ledger, or exit 1
# when there's no ledger yet. Headless calls only (interactive sessions aren't
# metered — see print_usage_summary).
run_cost_so_far() {
  [[ -n "${MIGITE_USAGE_LEDGER:-}" && -s "$MIGITE_USAGE_LEDGER" ]] || return 1
  local tmp calls cost
  tmp=$(mktemp)
  "$MIGITE_PYTHON" "$MIGITE_HOME/migite_claude.py" summary --ledger "$MIGITE_USAGE_LEDGER" --json "$tmp" >/dev/null 2>&1 || { rm -f "$tmp"; return 1; }
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
    "$MIGITE_PYTHON" "$MIGITE_HOME/migite_claude.py" summary --ledger "$MIGITE_USAGE_LEDGER" --json "$out" || true
    [[ -n "${TASK_DIR:-}" ]] && sync_json "$out" "$TASK_DIR/usage.json"
  else
    "$MIGITE_PYTHON" "$MIGITE_HOME/migite_claude.py" summary --ledger "$MIGITE_USAGE_LEDGER" || true
  fi
  echo -e "  Ledger: ${CYAN}$MIGITE_USAGE_LEDGER${RESET}"
  echo -e "${BOLD}────────────────────────────────────────────────${RESET}"
}

# Interactive prompts above this many bytes are handed to Claude as a pointer
# to the prompt file instead of inline on the command line. Linux caps a
# single argv string at 128 KB; the implement prompt is knowledge.md + plan +
# command file and knowledge.md grows without bound, so this will be hit on a
# long-lived repo. Below the cap the prompt stays inline, where it shows up as
# the session's first message and is easier to eyeball.
MIGITE_PROMPT_INLINE_MAX="${MIGITE_PROMPT_INLINE_MAX:-100000}"

# run_phase <label> <output_file> <prompt> [permission_mode]
# Opens an interactive Claude Code session in a split pane with the prompt pre-loaded.
# permission_mode defaults to `permissions.interactive` from the config
# (bypassPermissions unless changed). When done, type /exit to return to the workflow.
run_phase() {
  local label="$1"
  local outfile="$2"
  local prompt="$3"
  local permission_mode="${4:-${MIGITE_CFG_PERMISSIONS_INTERACTIVE:-bypassPermissions}}"
  local prompt_file
  prompt_file=$(write_prompt "$label" "$prompt")

  # What actually goes on the command line: the prompt itself, or a pointer to it.
  local prompt_arg_file="$prompt_file"
  if [[ ${#prompt} -gt $MIGITE_PROMPT_INLINE_MAX ]]; then
    prompt_arg_file="$LOG_DIR/$TIMESTAMP-prompt-${label// /-}-pointer.txt"
    printf 'Your task brief for this session is in the file %s (%d bytes — too large to pass inline). Read that file IN FULL before doing anything else, then follow its instructions exactly as if they had been given to you directly.' \
      "$prompt_file" "${#prompt}" > "$prompt_arg_file"
    warn "Prompt is ${#prompt} bytes (> MIGITE_PROMPT_INLINE_MAX=$MIGITE_PROMPT_INLINE_MAX) — passing as a file pointer"
  fi

  echo ""
  echo -e "${CYAN}  Starting interactive session: ${BOLD}$label${RESET}"
  echo -e "  ${CYAN}Output file: ${outfile}${RESET}"
  echo -e "  ${CYAN}Permission mode: ${permission_mode}${RESET}"
  echo -e "  ${YELLOW}Type /exit when done to return here${RESET}"
  echo ""

  local -a perm_flag=()
  [[ "$permission_mode" != "none" ]] && perm_flag=(--permission-mode "$permission_mode")

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
      printf 'env -u CLAUDECODE claude %s -- "$(cat '"'"'%s'"'"')"\n' "${perm_flag[*]}" "$prompt_arg_file"
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
    claude_cmd "${perm_flag[@]}" -- "$(cat "$prompt_arg_file")"
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
  printf '%s' "$prompt" | claude_print "$label" $extra_flags > "$outfile" &
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

  local heal_perm="${MIGITE_CFG_PERMISSIONS_HEAL:-bypassPermissions}"
  local -a heal_flag=()
  [[ "$heal_perm" != "none" ]] && heal_flag=(--permission-mode "$heal_perm")
  printf '%s' "$prompt" | claude_print "$label" "${heal_flag[@]}" > "$outfile" &
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

  # Preflight: verify langgraph is installed in the target Python. (The
  # `anthropic` SDK is NOT required — every model call shells out to `claude`.)
  if ! "$MIGITE_PYTHON" -c "import langgraph" 2>/dev/null; then
    error "Python dependencies missing. Run: $MIGITE_PYTHON -m pip install langgraph"
  fi

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
  # `return 0`, not bare `return`: a bare return inherits the failed [[ -f ]]
  # status (1), which under migite's `set -e` would abort the whole run.
  [[ -f "$file" ]] || return 0
  if grep -q "^created:" "$file" 2>/dev/null; then
    # tmp + mv rather than `sed -i`: BSD sed wants `-i ''`, GNU sed wants `-i`
    # with no argument, and there is no spelling both accept.
    local tmp
    tmp=$(mktemp)
    sed "s/^updated: .*/updated: $DATE/" "$file" > "$tmp" && mv "$tmp" "$file"
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

# build_attachments_block — reads each path in the global ATTACH_FILES array
# (populated by migite's --attach flag) and renders it as a heading + raw
# content block, so it can be folded into plain prompt text. Attachments feed
# directly into every plan model call (synthesize/critic/refine, all headless
# `claude --print` — see docs/troubleshooting.md), so a huge file multiplies
# token cost per call, not just once; truncated at a safety cap accordingly.
# Echoes nothing if ATTACH_FILES is empty or unset.
build_attachments_block() {
  # ${ATTACH_FILES+set} (not ${#ATTACH_FILES[@]}) so this is safe under `set -u`
  # whether the array was never declared or declared empty — both mean "no
  # attachments" here, and only the former would otherwise raise "unbound variable".
  [[ -z "${ATTACH_FILES+set}" ]] && return 0
  local max_chars=50000
  local f content
  for f in "${ATTACH_FILES[@]}"; do
    content=$(cat "$f")
    if [[ ${#content} -gt $max_chars ]]; then
      content="${content:0:$max_chars}"$'\n\n'"[... truncated, file is larger than the ${max_chars}-char cap ...]"
      # Callers capture this function's stdout via command substitution — the
      # warning must not land in that string, so it goes to stderr instead.
      warn "Attachment $(basename "$f") truncated to $max_chars chars" >&2
    fi
    printf '## Attachment: %s\n\n%s\n\n' "$(basename "$f")" "$content"
  done
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

# tooling_failed <log> — checks a rubocop/rspec output log for the failure
# patterns migite has had to add detection for one at a time in production
# (migite-improvements.md): a Ruby version not selected for `bundle exec`, a
# git-sourced gem not checked out, or an rspec run that produced 0 examples
# because of a DB connection failure or a load error. Echoes a short
# description and returns 0 (failed) on a match, 1 (clean) otherwise — every
# caller that inspects a log for these patterns should call this rather than
# repeating the grep list, which is exactly how the git-error and load-error
# patterns went missing from some call sites but not others.
tooling_failed() {
  local log="$1"
  [[ -f "$log" ]] || return 1
  if grep -q 'No version is set for command' "$log"; then
    echo "Ruby version not set — bundle exec could not run"
    return 0
  fi
  if grep -q 'Bundler::GitError\|not yet checked out' "$log"; then
    echo "Bundler::GitError — a git-sourced gem isn't checked out (run bundle install)"
    return 0
  fi
  if grep -q '0 examples' "$log" && grep -qi 'connection\|ConnectionBad' "$log"; then
    echo "DB connection failed — 0 examples ran, no coverage verified"
    return 0
  fi
  if grep -q '0 examples' "$log" && grep -qi 'error occurred while loading\|LoadError' "$log"; then
    echo "Load errors — 0 examples ran, spec coverage unverified"
    return 0
  fi
  return 1
}

# fill_intake_field <file> <regex> <value> — replaces the first match of
# <regex> (awk extended regex) in <file> with <value>, taken LITERALLY.
# Replaces the old `sed -i "s|<placeholder>|$TASK|"`, which spliced user text
# straight into a sed expression: a task description containing `|` aborted
# the run, and one containing `&` or `\` silently wrote garbage into the
# intake's Title field. The value travels through the environment, not `-v`,
# because awk -v processes backslash escapes and would mangle it too.
fill_intake_field() {
  local file="$1" pattern="$2" value="$3"
  local tmp
  tmp=$(mktemp)
  MIGITE_FILL_VALUE="$value" awk -v pat="$pattern" '
    !done && match($0, pat) {
      $0 = substr($0, 1, RSTART - 1) ENVIRON["MIGITE_FILL_VALUE"] substr($0, RSTART + RLENGTH)
      done = 1
    }
    { print }
  ' "$file" > "$tmp" && mv "$tmp" "$file"
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
