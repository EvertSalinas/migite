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

help_ok "migite --help"             "migite --jira <key-or-url>"          bash "$MIGITE_HOME/bin/migite" --help
help_ok "migite -h"                 "Task options:"                       bash "$MIGITE_HOME/bin/migite" -h
help_ok "migite help"               "Amend options:"                      bash "$MIGITE_HOME/bin/migite" help
help_ok "migite config --help"      "migite config --edit --user"         bash "$MIGITE_HOME/bin/migite" config --help
help_ok "migite doctor --help"      "migite doctor [--repo <path>]"       bash "$MIGITE_HOME/bin/migite" doctor --help
help_ok "migite migrate-vault --help" "migite migrate-vault [--dry-run]"  bash "$MIGITE_HOME/bin/migite" migrate-vault --help
help_ok "migite-ticket --help"      "migite-ticket sources"               bash "$MIGITE_HOME/bin/migite-ticket" --help
help_ok "migite-explore --help"     "--from-exploration <file>"           bash "$MIGITE_HOME/bin/migite-explore" --help
help_ok "migite-blueprint --help"   "--from-blueprint <file>"             bash "$MIGITE_HOME/bin/migite-blueprint" --help
help_ok "migite-audit --help"       "--focus <area>"                      bash "$MIGITE_HOME/bin/migite-audit" --help
help_ok "migite-pr-review -h"       "--branch <branch>"                   bash "$MIGITE_HOME/bin/migite-pr-review" -h

# every flag the entrypoint parses is in its help
flags=$(grep -oE '^    --[a-z-]+\)' "$MIGITE_HOME/bin/migite" | tr -d ' )' | sort -u)
usage=$(MIGITE_PYTHON=/nonexistent bash "$MIGITE_HOME/bin/migite" --help)
missing=""; for f in $flags; do [[ "$usage" == *"$f"* ]] || missing+=" $f"; done
check "migite --help documents every flag the entrypoint parses (missing:${missing:- none})" test -z "$missing"

# unknown options fail loudly instead of becoming a task description
out=$(cd "$cli_dir/repo" && bash "$MIGITE_HOME/bin/migite" --jria BB-1 2>&1); rc=$?
check "migite: an unknown option is an error that points at --help" \
  bash -c '[[ "$1" != 0 && "$2" == *"Unknown option: --jria"*"migite --help"* ]]' _ "$rc" "$out"
out=$(cd "$cli_dir/repo" && bash "$MIGITE_HOME/bin/migite" --resume --amend 2>&1); rc=$?
check "migite --resume --amend: refused, with how to resume an amend run" \
  bash -c '[[ "$1" != 0 && "$2" == *"can'"'"'t be combined"*"migite --resume <its run.json>"* ]]' _ "$rc" "$out"
out=$(cd "$cli_dir/repo" && bash "$MIGITE_HOME/bin/migite" --resume missing/run.json 2>&1); rc=$?
check "migite --resume <file>: a manifest that doesn't exist is an error" \
  bash -c '[[ "$1" != 0 && "$2" == *"Run manifest not found: missing/run.json"* ]]' _ "$rc" "$out"
out=$(cd "$cli_dir/repo" && bash "$MIGITE_HOME/bin/migite" config --bogus 2>&1); rc=$?
check "migite config: an unknown option exits 1 and shows the help" \
  bash -c '[[ "$1" == 1 && "$2" == *"Unknown option: --bogus"*"migite config --edit"* ]]' _ "$rc" "$out"
out=$(cd "$cli_dir/repo" && bash "$MIGITE_HOME/bin/migite" doctor --bogus 2>&1); rc=$?
check "migite doctor: an unknown argument points at --help" \
  bash -c '[[ "$1" != 0 && "$2" == *"migite doctor --help"* ]]' _ "$rc" "$out"
out=$(cd "$cli_dir/repo" && bash "$MIGITE_HOME/bin/migite-pr-review" 2>&1); rc=$?
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
      bash "$MIGITE_HOME/bin/migite" config "$@" )
}

check "migite config --path: the repo file --edit would open" \
  bash -c '[[ "$(cd "$1/repo" && bash "$2/bin/migite" config --path)" == *"/repo/.migite.yml" ]]' _ "$cli_dir" "$MIGITE_HOME"

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

# ── migite migrate-vault ─────────────────────────────────────────────────────
# Outside any repo, isolated HOME, vault.base from DEV_LOG_BASE.
mv_vault="$cli_dir/vault"
mkdir -p "$mv_vault/Acme/api/bb-1"
printf '# BB-1: Export\n' > "$mv_vault/Acme/api/bb-1/plan.md"
echo "review" > "$mv_vault/Acme/api/bb-1/review.md"
mv_run() {
  ( cd "$cli_dir/outside" && HOME="$cli_dir/home-mv" XDG_CONFIG_HOME="$cli_dir/home-mv/.config" \
      DEV_LOG_BASE="$mv_vault" bash "$MIGITE_HOME/bin/migite" migrate-vault "$@" 2>&1 )
}
out=$(mv_run --bogus); rc=$?
check "migite migrate-vault: an unknown option points at --help" \
  bash -c '[[ "$1" != 0 && "$2" == *"migite migrate-vault --help"* ]]' _ "$rc" "$out"
out=$(mv_run --dry-run); rc=$?
check "migite migrate-vault --dry-run: lists the move and leaves the file in place" \
  bash -c '[[ "$1" == 0 && "$2" == *"would move review.md -> 00-build/review.md"* ]] && test -f "$3/review.md"' _ "$rc" "$out" "$mv_vault/Acme/api/bb-1"
out=$(mv_run); rc=$?
check "migite migrate-vault: moves the task into run folders and writes its index" \
  bash -c '[[ "$1" == 0 ]] && test -f "$2/00-build/review.md" -a -f "$2/index.md"' _ "$rc" "$mv_vault/Acme/api/bb-1"
out=$(mv_run); rc=$?
check "migite migrate-vault: a second run finds nothing to migrate" \
  bash -c '[[ "$1" == 0 && "$2" == *"Nothing to migrate"* ]]' _ "$rc" "$out"

unset -f help_ok cfg_run mv_run
unset mv_vault
