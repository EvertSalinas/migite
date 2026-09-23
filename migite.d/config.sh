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
#   from them. Env vars already beat file values inside the resolver, so a
#   user who never writes a .migite.yml sees exactly the old behaviour.
#   A config error (bad YAML, invalid enum) aborts the run — running on
#   defaults after the user wrote a config would be worse than stopping.

load_migite_config() {
  local repo_root="${1:-}"
  local dump
  local -a rr=()
  [[ -n "$repo_root" ]] && rr=(--repo-root "$repo_root")
  if ! dump="$("$MIGITE_PYTHON" "$MIGITE_HOME/migite_config.py" env "${rr[@]}")"; then
    # The resolver prints an `echo ... >&2; false` line on error — eval it so the message shows, then stop.
    eval "$dump" || true
    error "Fix the configuration above (or unset MIGITE_CONFIG) and re-run"
  fi
  eval "$dump"

  # Legacy variables the phases read directly
  DEV_LOG_BASE="${MIGITE_CFG_VAULT_BASE/#\~/$HOME}"
  LOG_DIR="${MIGITE_CFG_LOGS_DIR/#\~/$HOME}"
  MAX_HEAL_ATTEMPTS="$MIGITE_CFG_HEAL_MAX_ATTEMPTS"
  MIGITE_PROMPT_INLINE_MAX="$MIGITE_CFG_UI_PROMPT_INLINE_MAX"
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
  return 0
}

# cfg_model <role> — resolved model id for a call-site role (see ROLE_TIERS in
# migite_config.py). When the config hasn't been loaded into this shell (helpers
# exercised standalone, e.g. in tests), ask the resolver for the built-in default
# instead of keeping a second hand-maintained copy of the tier table here — the
# previous `case` block drifted from migite_config.py every time a model changed.
cfg_model() {
  local role="$1" var
  var="MIGITE_CFG_MODEL_$(echo "$role" | tr '[:lower:]' '[:upper:]')"
  if [[ -n "${!var:-}" ]]; then
    echo "${!var}"
    return
  fi
  "$MIGITE_PYTHON" "$MIGITE_HOME/migite_config.py" get "model:$role"
}

# cfg_model_flags <role> — `--model <id>` plus `--effort <level>` when the config
# sets one for the role's tier (models.effort) or the role itself
# (models.roles_effort). Never emits --effort for a Haiku model, which rejects
# the flag. Use this in place of `--model "$(cfg_model role)"` so every bash
# call site gets the effort setting for free.
cfg_model_flags() {
  local role="$1" model level var
  model="$(cfg_model "$role")"
  var="MIGITE_CFG_EFFORT_$(echo "$role" | tr '[:lower:]' '[:upper:]')"
  level="${!var:-}"
  # An empty model means "the backend's own default" (cursor / opencode until you pin
  # a tier): emit no --model at all. --effort only exists on Claude Code.
  local -a out=()
  [[ -n "$model" ]] && out+=(--model "$model")
  if [[ -n "$level" && "$level" != "none" && "$model" != *haiku* && "${MIGITE_CFG_AGENT_BACKEND:-claude}" == "claude" ]]; then
    out+=(--effort "$level")
  fi
  printf -- '%s' "${out[*]}"
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

# run_config_command [--init [--force]] — `migite config`
run_config_command() {
  local repo_root
  repo_root="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
  case "${1:-}" in
    --init|init)
      shift
      "$MIGITE_PYTHON" "$MIGITE_HOME/migite_config.py" --repo-root "$repo_root" init "$@"
      ;;
    --validate|validate)
      "$MIGITE_PYTHON" "$MIGITE_HOME/migite_config.py" --repo-root "$repo_root" validate
      ;;
    "")
      echo ""
      echo "migite config — effective configuration for $repo_root"
      echo ""
      "$MIGITE_PYTHON" "$MIGITE_HOME/migite_config.py" --repo-root "$repo_root" show
      echo ""
      ;;
    *)
      echo "Usage: migite config [--init [--user] [--force] | --validate]" >&2
      echo "  (no args)         effective configuration for this repo, with the source of every value" >&2
      echo "  --init            write a starter <repo>/.migite.yml with every default" >&2
      echo "  --init --user     write a starter ~/.config/migite/config.yml (personal defaults for all repos)" >&2
      echo "  --validate        exit 1 on errors, print warnings" >&2
      return 1
      ;;
  esac
}
