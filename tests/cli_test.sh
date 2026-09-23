# tests/cli_test.sh - --help on every command, unknown options, and `migite config
# --edit` / `--path`. Help must work anywhere: outside a git repo and before any
# Python or agent CLI check, so those runs use a Python that doesn't exist.

cli_dir=$(mktemp -d); CLEANUP_DIRS+=("$cli_dir")
mkdir -p "$cli_dir/repo" "$cli_dir/home/.config" "$cli_dir/outside"
( cd "$cli_dir/repo" && git init -q . )

# help_ok <label> <expected text> <command...> - exit 0 and the text on stdout, from
# outside any repo, with no working Python.
help_ok() {
  local label="$1" want="$2"; shift 2
  local out rc=0
  out=$(cd "$cli_dir/outside" && MIGITE_PYTHON=/nonexistent/python "$@" 2>&1) || rc=$?
  check "$label" bash -c '[[ "$1" == 0 && "$2" == *"$3"* ]]' _ "$rc" "$out" "$want"
}

help_ok "migite --help"             "migite --jira <key-or-url>"          bash "$MIGITE_HOME/migite" --help
help_ok "migite -h"                 "Task options:"                       bash "$MIGITE_HOME/migite" -h
help_ok "migite help"               "Amend options:"                      bash "$MIGITE_HOME/migite" help
help_ok "migite config --help"      "migite config --edit --user"         bash "$MIGITE_HOME/migite" config --help
help_ok "migite doctor --help"      "migite doctor [--repo <path>]"       bash "$MIGITE_HOME/migite" doctor --help
help_ok "migite-ticket --help"      "migite-ticket sources"               bash "$MIGITE_HOME/migite-ticket" --help
help_ok "migite-explore --help"     "--from-exploration <file>"           bash "$MIGITE_HOME/migite-explore" --help
help_ok "migite-blueprint --help"   "--from-blueprint <file>"             bash "$MIGITE_HOME/migite-blueprint" --help
help_ok "migite-audit --help"       "--focus <area>"                      bash "$MIGITE_HOME/migite-audit" --help
help_ok "migite-pr-review -h"       "--branch <branch>"                   bash "$MIGITE_HOME/migite-pr-review" -h

# every flag the entrypoint parses is in its help
flags=$(grep -oE '^    --[a-z-]+\)' "$MIGITE_HOME/migite" | tr -d ' )' | sort -u)
usage=$(MIGITE_PYTHON=/nonexistent bash "$MIGITE_HOME/migite" --help)
missing=""; for f in $flags; do [[ "$usage" == *"$f"* ]] || missing+=" $f"; done
check "migite --help documents every flag the entrypoint parses (missing:${missing:- none})" test -z "$missing"

# unknown options fail loudly instead of becoming a task description
out=$(cd "$cli_dir/repo" && bash "$MIGITE_HOME/migite" --jria BB-1 2>&1); rc=$?
check "migite: an unknown option is an error that points at --help" \
  bash -c '[[ "$1" != 0 && "$2" == *"Unknown option: --jria"*"migite --help"* ]]' _ "$rc" "$out"
out=$(cd "$cli_dir/repo" && bash "$MIGITE_HOME/migite" config --bogus 2>&1); rc=$?
check "migite config: an unknown option exits 1 and shows the help" \
  bash -c '[[ "$1" == 1 && "$2" == *"Unknown option: --bogus"*"migite config --edit"* ]]' _ "$rc" "$out"
out=$(cd "$cli_dir/repo" && bash "$MIGITE_HOME/migite" doctor --bogus 2>&1); rc=$?
check "migite doctor: an unknown argument points at --help" \
  bash -c '[[ "$1" != 0 && "$2" == *"migite doctor --help"* ]]' _ "$rc" "$out"
out=$(cd "$cli_dir/repo" && bash "$MIGITE_HOME/migite-pr-review" 2>&1); rc=$?
check "migite-pr-review: no --branch → exit 1 with the help" \
  bash -c '[[ "$1" == 1 && "$2" == *"--branch is required"*"Options:"* ]]' _ "$rc" "$out"

# ── migite config --edit / --path ────────────────────────────────────────────
printf '#!/usr/bin/env bash\nperl -pi -e "s/^stack: auto/stack: generic/" "$1"\n' > "$cli_dir/editor-ok"
printf '#!/usr/bin/env bash\nperl -pi -e "s/policy: lenient/policy: yolo/" "$1"\n' > "$cli_dir/editor-bad"
printf '#!/usr/bin/env bash\nexit 3\n' > "$cli_dir/editor-fails"
chmod +x "$cli_dir"/editor-*
cfg_run() {   # cfg_run <editor> <args...> - migite config in the scratch repo, isolated HOME
  local editor="$1"; shift
  ( cd "$cli_dir/repo" && HOME="$cli_dir/home" XDG_CONFIG_HOME="$cli_dir/home/.config" EDITOR="$editor" \
      bash "$MIGITE_HOME/migite" config "$@" )
}

check "migite config --path: the repo file --edit would open" \
  bash -c '[[ "$(cd "$1/repo" && bash "$2/migite" config --path)" == *"/repo/.migite.yml" ]]' _ "$cli_dir" "$MIGITE_HOME"

out=$(cfg_run "$cli_dir/editor-ok" --edit 2>&1); rc=$?
check "migite config --edit: no file yet → writes the starter, opens it, validates it" \
  bash -c '[[ "$1" == 0 && "$2" == *"writing the starter first"* && "$2" == *"config valid"* ]]' _ "$rc" "$out"
check "migite config --edit: the editor's change is saved" grep -q '^stack: generic' "$cli_dir/repo/.migite.yml"

out=$(cfg_run "$cli_dir/editor-ok" --edit --user 2>&1); rc=$?
check "migite config --edit --user: edits ~/.config/migite/config.yml" \
  bash -c '[[ "$1" == 0 ]] && grep -q "^stack: generic" "$2/home/.config/migite/config.yml"' _ "$rc" "$cli_dir"

out=$(cfg_run "$cli_dir/editor-bad" --edit 2>&1); rc=$?
check "migite config --edit: a broken edit exits 1, names the key, and says how to fix it" \
  bash -c '[[ "$1" == 1 && "$2" == *"gates.commit.policy"* && "$2" == *"Fix it with: migite config --edit" ]]' _ "$rc" "$out"

out=$(cfg_run "$cli_dir/editor-ok" --edit 2>&1); rc=$?
check "migite config --edit: opens a broken file anyway, so it can be fixed" \
  bash -c '[[ "$1" == 1 && "$2" == *"Opening"* ]]' _ "$rc" "$out"   # editor-ok doesn't fix the policy, so it still fails

out=$(cfg_run "$cli_dir/editor-fails" --edit 2>&1); rc=$?
check "migite config --edit: an editor that fails is reported, not ignored" \
  bash -c '[[ "$1" == 1 && "$2" == *"exited with an error"* ]]' _ "$rc" "$out"

out=$(cfg_run "$cli_dir/editor-ok" --edit --bogus 2>&1); rc=$?
check "migite config --edit: an unknown option is refused" \
  bash -c '[[ "$1" == 1 && "$2" == *"Unknown option for --edit: --bogus"* ]]' _ "$rc" "$out"

unset -f help_ok cfg_run
