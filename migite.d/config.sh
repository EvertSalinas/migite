#!/usr/bin/env bash
# migite.d/config.sh — load the layered configuration (migite_config.py) into
# the shell and map it onto the variables the rest of migite already reads.
#
# Sourced by migite. Expects MIGITE_HOME and MIGITE_PYTHON to be set.
#
# load_migite_config <repo_root>
#   eval's `migite_config.py env` → MIGITE_CFG_<KEY> for every leaf and
#   MIGITE_CFG_MODEL_<ROLE> for every resolved model role, then assigns the
#   legacy variables (DEV_LOG_BASE, LOG_DIR, MAX_HEAL_ATTEMPTS, EDITOR, ...)
#   from them, then caches the configured agent's description (load_agent_info). Env vars already beat file values inside the resolver, so a
#   user who never writes a .migite.yml sees exactly the old behaviour.
#   A config error (bad YAML, invalid enum) aborts the run — running on
#   defaults after the user wrote a config would be worse than stopping.

load_migite_config() {
  local repo_root="${1:-}"
  local dump
  local -a rr=()
  [[ -n "$repo_root" ]] && rr=(--repo-root "$repo_root")
  if ! dump="$("$MIGITE_PYTHON" "$MIGITE_HOME/migite_config.py" env ${rr[@]+"${rr[@]}"})"; then
    # The resolver prints an `echo ... >&2; false` line on error — eval it so the message shows, then stop.
    eval "$dump" || true
    error "Fix the configuration above (or unset MIGITE_CONFIG) and re-run"
  fi
  eval "$dump"

  # Legacy variables the phases read directly
  DEV_LOG_BASE="${MIGITE_CFG_VAULT_BASE/#\~/$HOME}"
  LOG_DIR="${MIGITE_CFG_LOGS_DIR/#\~/$HOME}"
  MAX_HEAL_ATTEMPTS="$MIGITE_CFG_HEAL_MAX_ATTEMPTS"
  [[ -n "$MIGITE_CFG_UI_EDITOR" ]] && EDITOR="$MIGITE_CFG_UI_EDITOR"
  [[ -n "$MIGITE_CFG_VAULT_ORG" ]] && export MIGITE_ORG="$MIGITE_CFG_VAULT_ORG"
  # Python agents read this env var for their headless permission mode; keep it in sync
  # so the config value reaches them too (they also load the config themselves).
  [[ "$MIGITE_CFG_PERMISSIONS_HEADLESS" != "none" ]] && export MIGITE_PERMISSION_MODE="$MIGITE_CFG_PERMISSIONS_HEADLESS"
  export DEV_LOG_BASE LOG_DIR

  if [[ -n "${MIGITE_CFG_WARNINGS:-}" ]]; then
    while IFS= read -r w; do [[ -n "$w" ]] && warn "config: $w"; done <<< "$MIGITE_CFG_WARNINGS"
  fi
  [[ -n "${MIGITE_CFG_FILES:-}" ]] && log "Config: $MIGITE_CFG_FILES"
  load_agent_info "$repo_root"
  return 0
}

# load_agent_info [repo_root] - MIGITE_AGENT_NAME, _DISPLAY, _BINARY, _EXIT_HINT,
# _INSTRUCTIONS, and _CAPS (space-separated: structured_output effort usage
# scope:<name>) for the configured agent, from `migite_agent.py info --shell`.
# Bash reads these instead of knowing anything about a particular CLI.
load_agent_info() {
  local repo_root="${1:-${REPO_ROOT:-}}" dump
  local -a rr=()
  [[ -n "$repo_root" ]] && rr=(--repo-root "$repo_root")
  dump="$("$MIGITE_PYTHON" "$MIGITE_HOME/migite_agent.py" ${rr[@]+"${rr[@]}"} info --shell)" \
    || error "Could not describe the configured agent (agent.backend)"
  eval "$dump"
}

# cfg <dotted.key> [default] — one config value as exported by load_migite_config.
cfg() {
  local key="$1" default="${2:-}" var
  var="MIGITE_CFG_$(echo "$key" | tr '[:lower:].' '[:upper:]_')"
  if [[ -n "${!var+set}" ]]; then
    echo "${!var}"
  else
    echo "$default"
  fi
}

# prompt_path <name> / template_path <name> — honour prompts.dir / templates.dir
# overrides before falling back to the repo copies. The dir may be relative to
# the repo root and may contain {org} / {repo}, substituted from $ORG /
# $REPO_NAME (so one user-level `templates.dir: ~/.config/migite/templates/{org}`
# gives each organisation its own PR template with no file in any repo).
# Mirrors Config.override_dir() in migite_config.py.
_override_path() {
  local kind="$1" name="$2" dir
  dir="$(cfg "$kind.dir")"
  if [[ -n "$dir" ]]; then
    dir="${dir//\{org\}/${ORG:-}}"
    dir="${dir//\{repo\}/${REPO_NAME:-}}"
    dir="${dir/#\~/$HOME}"
    [[ "$dir" != /* && -n "${REPO_ROOT:-}" ]] && dir="$REPO_ROOT/$dir"
    [[ -f "$dir/$name.md" ]] && { echo "$dir/$name.md"; return; }
  fi
  echo "$MIGITE_HOME/$kind/$name.md"
}
prompt_path()   { _override_path prompts   "$1"; }
template_path() { _override_path templates "$1"; }

# use_tmux — should interactive phases and agents open in a tmux split?
#   ui.tmux = auto → only when already inside tmux ($TMUX set)
#   ui.tmux = on   → same, but warn when not inside tmux (can't create a server for you)
#   ui.tmux = off  → never
use_tmux() {
  case "$(cfg ui.tmux auto)" in
    off) return 1 ;;
    on)
      [[ -n "${TMUX:-}" ]] && return 0
      [[ -z "${_TMUX_WARNED:-}" ]] && { warn "ui.tmux is 'on' but this shell isn't inside tmux — running inline"; _TMUX_WARNED=1; }
      return 1 ;;
    *) [[ -n "${TMUX:-}" ]] ;;
  esac
}

# config_usage - `migite config --help`
config_usage() {
  cat <<'EOF'
migite config - show, create, edit, or check the layered configuration

Precedence, highest first: command-line flags, environment variables, $MIGITE_CONFIG,
<repo>/.migite.yml, ~/.config/migite/config.yml, built-in defaults.

Usage:
  migite config                    the effective configuration for this repo, with the
                                   source of every value and the model each role runs on
  migite config --edit             open <repo>/.migite.yml in your editor (written from the
                                   starter first if it doesn't exist), then validate it
  migite config --edit --user      the same for ~/.config/migite/config.yml, your defaults
                                   for every repo
  migite config --init [--user]    write a starter file with every default and a comment each
  migite config --init --force     overwrite an existing file with the starter
  migite config --validate         exit 1 on errors, print warnings for unknown keys
  migite config --path [--user]    print the file --edit would open
  -h, --help                       show this help

The editor is $EDITOR, else ui.editor from the config, else vim.
Every key: docs/configuration.md
EOF
}

# _config_editor <repo_root> - $EDITOR, else ui.editor, else vim: the same order the
# resolver uses everywhere (env beats files). A config that doesn't parse (often the
# reason you're editing it) falls through to $EDITOR, then vim.
_config_editor() {
  local repo_root="$1" editor=""
  editor="$("$MIGITE_PYTHON" "$MIGITE_HOME/migite_config.py" --repo-root "$repo_root" get ui.editor 2>/dev/null)" || editor=""
  echo "${editor:-${EDITOR:-vim}}"
}

# _config_edit <repo_root> [--user] - `migite config --edit`
_config_edit() {
  local repo_root="$1"; shift
  local -a scope=()
  local hint="migite config --edit"
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --user) scope=(--user); hint="migite config --edit --user"; shift ;;
      -h|--help) config_usage; return 0 ;;
      *) echo "✘ Unknown option for --edit: $1 (see: migite config --help)" >&2; return 1 ;;
    esac
  done
  local file
  file="$("$MIGITE_PYTHON" "$MIGITE_HOME/migite_config.py" --repo-root "$repo_root" path ${scope[@]+"${scope[@]}"})" || return 1
  if [[ ! -f "$file" ]]; then
    echo "No config file at $file yet; writing the starter first."
    "$MIGITE_PYTHON" "$MIGITE_HOME/migite_config.py" --repo-root "$repo_root" init ${scope[@]+"${scope[@]}"} || return 1
  fi
  local -a editor_cmd
  read -r -a editor_cmd <<< "$(_config_editor "$repo_root")"
  echo "Opening $file with ${editor_cmd[0]}..."
  "${editor_cmd[@]}" "$file" || { echo "✘ ${editor_cmd[0]} exited with an error; the file was not checked" >&2; return 1; }
  # Validate what was saved, so a typo shows up now rather than at the next run.
  if "$MIGITE_PYTHON" "$MIGITE_HOME/migite_config.py" --repo-root "$repo_root" validate; then
    return 0
  fi
  echo "✘ $file has an error (above). Fix it with: $hint" >&2
  return 1
}

# run_config_command [...] - `migite config`; see config_usage
run_config_command() {
  local repo_root
  repo_root="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
  case "${1:-}" in
    -h|--help|help)
      config_usage
      ;;
    --edit|edit)
      shift
      _config_edit "$repo_root" "$@"
      ;;
    --path|path)
      shift
      "$MIGITE_PYTHON" "$MIGITE_HOME/migite_config.py" --repo-root "$repo_root" path "$@"
      ;;
    --init|init)
      shift
      "$MIGITE_PYTHON" "$MIGITE_HOME/migite_config.py" --repo-root "$repo_root" init "$@"
      ;;
    --validate|validate)
      "$MIGITE_PYTHON" "$MIGITE_HOME/migite_config.py" --repo-root "$repo_root" validate
      ;;
    "")
      echo ""
      echo "migite config - effective configuration for $repo_root"
      echo ""
      "$MIGITE_PYTHON" "$MIGITE_HOME/migite_config.py" --repo-root "$repo_root" show
      echo ""
      ;;
    *)
      echo "✘ Unknown option: $1" >&2
      echo "" >&2
      config_usage >&2
      return 1
      ;;
  esac
}
