# tests/config_test.sh — lib/config.sh: load_migite_config, cfg, cfg_model,
# prompt_path/template_path, use_tmux. Uses a JSON config (no PyYAML needed) in a
# throwaway repo dir, with HOME/XDG pointed at an empty dir so the developer's own
# ~/.config/migite never leaks into the assertions.

if ! "$MIGITE_PYTHON" -c 'import sys' &>/dev/null; then
  echo "  · no working Python at $MIGITE_PYTHON — skipping config checks"
  return 0 2>/dev/null || exit 0
fi

cfg_dir=$(mktemp -d); CLEANUP_DIRS+=("$cfg_dir")
mkdir -p "$cfg_dir/home/.config/migite" "$cfg_dir/repo/.migite/prompts"
_saved_home="$HOME"; _saved_xdg="${XDG_CONFIG_HOME:-}"
export HOME="$cfg_dir/home" XDG_CONFIG_HOME="$cfg_dir/home/.config"
# Every block below runs in a ( subshell ) so the env-var overrides it clears/sets
# never leak into the other test files (LOG_DIR in particular is what run.sh gave us).
_cfg_reset_env() {
  unset EDITOR DEV_LOG_BASE LOG_DIR MAX_HEAL_ATTEMPTS MIGITE_ORG MIGITE_PERMISSION_MODE MIGITE_CONFIG MIGITE_STACK
}

# ── defaults only (no files) ─────────────────────────────────────────────────
(
  _cfg_reset_env
  REPO_ROOT="$cfg_dir/repo"
  load_migite_config "$REPO_ROOT" >/dev/null
  check "load_migite_config: DEV_LOG_BASE defaults to ~/dev-log (expanded)" \
    test "$DEV_LOG_BASE" = "$HOME/dev-log"
  check "load_migite_config: LOG_DIR default expanded" \
    test "$LOG_DIR" = "$HOME/.dev-workflow/logs"
  check "load_migite_config: MAX_HEAL_ATTEMPTS default 3" \
    test "$MAX_HEAL_ATTEMPTS" = "3"
  check "cfg: gate policy default is lenient (pre-config behaviour)" \
    test "$(cfg gates.commit.policy)" = "lenient"
  check "cfg: unknown key returns the given default" \
    test "$(cfg not.a.key fallback)" = "fallback"
  check "resolved model: explore → fast tier (haiku)" \
    test "$MIGITE_CFG_MODEL_EXPLORE" = "claude-haiku-4-5-20251001"
  check "resolved model: verdict → strong tier (opus 5.5)" \
    test "$MIGITE_CFG_MODEL_VERDICT" = "claude-opus-5-5"
  check "resolved model: think → strong tier by default (drafter no weaker than critic)" \
    test "$MIGITE_CFG_MODEL_THINK" = "claude-opus-5-5"
  check "resolved model: heal → standard tier" \
    test "$MIGITE_CFG_MODEL_HEAL" = "claude-sonnet-5"
  check "resolved effort: none configured → empty" \
    test -z "$MIGITE_CFG_EFFORT_KNOWLEDGE"
  check "load_migite_config: caches the agent's description (claude by default)" \
    bash -c '[[ "$1" == claude && "$2" == "Claude Code" && " $3 " == *" scope:jira.read "* ]]' _ \
      "${MIGITE_AGENT_NAME:-}" "${MIGITE_AGENT_DISPLAY:-}" "${MIGITE_AGENT_CAPS:-}"
  check "permissions: neutral defaults (interactive auto, headless none)" \
    bash -c '[[ "$1" == auto && "$2" == none ]]' _ "$(cfg permissions.interactive)" "$(cfg permissions.headless)"
  check "prompt_path: falls back to the repo copy when no override dir" \
    test "$(prompt_path plan)" = "$MIGITE_HOME/prompts/plan.md"
  check "load_migite_config: no files → MIGITE_CFG_FILES empty" \
    test -z "$MIGITE_CFG_FILES"
)

# ── repo file + env override + prompt override ───────────────────────────────
cat > "$cfg_dir/repo/.migite.json" <<'EOF'
{"vault": {"base": "/vault/from/file", "org": "Acme"},
 "models": {"strong": "file-strong", "roles": {"knowledge": "pinned-knowledge", "explore": "claude-haiku-4-5-20251001"},
            "effort": {"strong": "xhigh", "fast": "high"}, "roles_effort": {"knowledge": "low"}},
 "heal": {"max_attempts": 7, "full_suite_fallback": false},
 "gates": {"commit": {"policy": "strict"}},
 "permissions": {"headless": "acceptEdits", "interactive": "acceptEdits"},
 "prompts": {"dir": ".migite/prompts"},
 "ui": {"editor": "nano", "tmux": "off", "notify": "off"},
 "extra_key": 1}
EOF
echo "custom plan prompt" > "$cfg_dir/repo/.migite/prompts/plan.md"
(
  _cfg_reset_env
  REPO_ROOT="$cfg_dir/repo"
  export MAX_HEAL_ATTEMPTS=2   # env must beat the file's 7
  # Capture via a file, NOT $(...): a command substitution runs in a subshell and
  # would throw away every variable load_migite_config assigns.
  load_migite_config "$REPO_ROOT" > "$cfg_dir/load.out" 2>&1
  out=$(cat "$cfg_dir/load.out")
  check "load_migite_config: repo file value applied (vault.base)" \
    test "$DEV_LOG_BASE" = "/vault/from/file"
  check "load_migite_config: vault.org exported as MIGITE_ORG for the Python tools" \
    test "${MIGITE_ORG:-}" = "Acme"
  check "load_migite_config: env beats file (MAX_HEAL_ATTEMPTS=2 over max_attempts: 7)" \
    test "$MAX_HEAL_ATTEMPTS" = "2"
  check "load_migite_config: ui.editor sets EDITOR when EDITOR was unset" \
    test "${EDITOR:-}" = "nano"
  check "load_migite_config: permissions.headless (Claude alias acceptEdits) normalized to edits and exported" \
    test "${MIGITE_PERMISSION_MODE:-}" = "edits"
  check "cfg: permissions.interactive alias normalized too" \
    test "$(cfg permissions.interactive)" = "edits"
  check "load_migite_config: unknown key surfaces as a warning" \
    bash -c 'printf "%s" "$1" | grep -q "unknown key .extra_key"' _ "$out"
  check "load_migite_config: loaded file is reported" \
    bash -c 'printf "%s" "$1" | grep -q "Config: .*\.migite\.json"' _ "$out"
  check "cfg: strict gate policy read back" \
    test "$(cfg gates.commit.policy)" = "strict"
  check "cfg: boolean false exported as the string 'false'" \
    test "$(cfg heal.full_suite_fallback)" = "false"
  check "resolved model: tier change reaches every role in the tier (critic → file-strong)" \
    test "$MIGITE_CFG_MODEL_CRITIC" = "file-strong"
  check "resolved model: role pin wins over its tier (knowledge)" \
    test "$MIGITE_CFG_MODEL_KNOWLEDGE" = "pinned-knowledge"
  check "resolved model: unpinned role in another tier unchanged (amend → sonnet)" \
    test "$MIGITE_CFG_MODEL_AMEND" = "claude-sonnet-5"
  check "resolved effort: tier effort reaches the role (critic → xhigh)" \
    test "$MIGITE_CFG_EFFORT_CRITIC" = "xhigh"
  check "resolved effort: role effort beats tier (knowledge → low)" \
    test "$MIGITE_CFG_EFFORT_KNOWLEDGE" = "low"
  check "resolved effort: role with no effort anywhere → empty (amend)" \
    test -z "$MIGITE_CFG_EFFORT_AMEND"
  check "prompt_path: prompts.dir override (relative to repo root) wins when the file exists" \
    test "$(prompt_path plan)" = "$cfg_dir/repo/.migite/prompts/plan.md"
  check "prompt_path: override dir without the file falls back to the repo copy" \
    test "$(prompt_path review)" = "$MIGITE_HOME/prompts/review.md"
  check "template_path: no templates.dir → repo copy" \
    test "$(template_path commit)" = "$MIGITE_HOME/templates/commit.md"
  # {org} / {repo} placeholders, substituted from $ORG / $REPO_NAME
  mkdir -p "$cfg_dir/tpl/Acme" "$cfg_dir/tpl/by-repo/widgets"
  echo "acme pr template" > "$cfg_dir/tpl/Acme/commit.md"
  echo "widgets bug template" > "$cfg_dir/tpl/by-repo/widgets/bug.md"
  ORG="Acme" REPO_NAME="widgets"
  MIGITE_CFG_TEMPLATES_DIR="$cfg_dir/tpl/{org}"
  check "template_path: {org} in templates.dir resolves to the org's directory" \
    test "$(template_path commit)" = "$cfg_dir/tpl/Acme/commit.md"
  check "template_path: {org} dir without the file falls back to the repo copy" \
    test "$(template_path feature)" = "$MIGITE_HOME/templates/feature.md"
  MIGITE_CFG_TEMPLATES_DIR="$cfg_dir/tpl/by-repo/{repo}"
  check "template_path: {repo} in templates.dir resolves to the repo's directory" \
    test "$(template_path bug)" = "$cfg_dir/tpl/by-repo/widgets/bug.md"
  ORG="Other"; MIGITE_CFG_TEMPLATES_DIR="$cfg_dir/tpl/{org}"
  check "template_path: an org with no override directory falls back to the repo copy" \
    test "$(template_path commit)" = "$MIGITE_HOME/templates/commit.md"
  TMUX="fake-session" ; check "use_tmux: ui.tmux=off is false even inside tmux" not use_tmux
  check "notify: ui.notify=off is a silent no-op" notify "x" "y"
)

# ── use_tmux modes ───────────────────────────────────────────────────────────
( MIGITE_CFG_UI_TMUX=auto; TMUX=""; check "use_tmux: auto + not in tmux → false" not use_tmux )
( MIGITE_CFG_UI_TMUX=auto; TMUX="s"; check "use_tmux: auto + in tmux → true" use_tmux )
( MIGITE_CFG_UI_TMUX=on;   TMUX="";  check "use_tmux: on + not in tmux → false (with a warning, not a crash)" not use_tmux 2>/dev/null )
( MIGITE_CFG_UI_TMUX=on;   TMUX="s"; check "use_tmux: on + in tmux → true" use_tmux )

# ── invalid config aborts instead of silently running on defaults ────────────
echo '{"gates": {"commit": {"policy": "whatever"}}}' > "$cfg_dir/repo/.migite.json"
rc=0
( _cfg_reset_env; REPO_ROOT="$cfg_dir/repo"; load_migite_config "$REPO_ROOT" >/dev/null 2>&1 ) || rc=$?
check "load_migite_config: an invalid enum value aborts (exit 1)" test "$rc" = "1"

# ── run_config_command (`migite config`) ─────────────────────────────────────
echo '{"models": {"strong": "shown-strong"}}' > "$cfg_dir/repo/.migite.json"
show_out=$(cd "$cfg_dir/repo" && git init -q . && run_config_command)
check "migite config: shows the effective value and its source file" \
  bash -c 'printf "%s" "$1" | grep -q "shown-strong" && printf "%s" "$1" | grep -q "\.migite\.json"' _ "$show_out"
check "migite config: lists resolved models by role" \
  bash -c 'printf "%s" "$1" | grep -q "critic .*shown-strong"' _ "$show_out"
rm "$cfg_dir/repo/.migite.json"
(cd "$cfg_dir/repo" && run_config_command --init >/dev/null)
check "migite config --init: writes a starter .migite.yml" test -s "$cfg_dir/repo/.migite.yml"
rc=0; (cd "$cfg_dir/repo" && run_config_command --init >/dev/null 2>&1) || rc=$?
check "migite config --init: refuses to overwrite without --force" test "$rc" != "0"

export HOME="$_saved_home"
if [[ -n "$_saved_xdg" ]]; then export XDG_CONFIG_HOME="$_saved_xdg"; else unset XDG_CONFIG_HOME; fi
