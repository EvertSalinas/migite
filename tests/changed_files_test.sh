# tests/changed_files_test.sh — lib/stack.sh:changed_ruby_files / changed_spec_files
#
# A file that hasn't been `git add`-ed yet is invisible to `git diff`, and thus
# to rubocop/rspec — this blind spot recurred at least five times in production
# (docs/improvements.md: 2026-07-23, 2026-08-05, 2026-08-11, 2026-08-12,
# 2026-08-17). Also covers the deleted-file fix from the same history: a
# working-tree delete must not show up as a "changed" file to lint/test.

repo=$(make_fixture_repo)
mkdir -p "$repo/lib" "$repo/spec"
echo 'class Foo; end' > "$repo/lib/foo.rb"
echo 'class Baz; end' > "$repo/lib/baz.rb"
git -C "$repo" add lib/foo.rb lib/baz.rb
git -C "$repo" commit -q -m init

# Tracked modification (not yet staged) + untracked new .rb and _spec.rb files
echo 'class Foo; def bar; end; end' > "$repo/lib/foo.rb"
echo 'class Bar; end' > "$repo/lib/bar.rb"
echo 'describe Bar do; end' > "$repo/spec/bar_spec.rb"
echo '# notes' > "$repo/notes.md"
rm "$repo/lib/baz.rb"

got_ruby=$(cd "$repo" && changed_ruby_files main)
check "changed_ruby_files: includes tracked-but-unstaged modification" \
  bash -c "printf '%s' \"\$1\" | grep -qx 'lib/foo.rb'" _ "$got_ruby"
check "changed_ruby_files: includes untracked new .rb file (the recurring blind spot)" \
  bash -c "printf '%s' \"\$1\" | grep -qx 'lib/bar.rb'" _ "$got_ruby"
check "changed_ruby_files: _spec.rb files also match (end in .rb too)" \
  bash -c "printf '%s' \"\$1\" | grep -qx 'spec/bar_spec.rb'" _ "$got_ruby"
check "changed_ruby_files: excludes a working-tree delete (diff-filter ACMR)" \
  not bash -c "printf '%s' \"\$1\" | grep -qx 'lib/baz.rb'" _ "$got_ruby"
check "changed_ruby_files: excludes non-.rb untracked files" \
  not bash -c "printf '%s' \"\$1\" | grep -qx 'notes.md'" _ "$got_ruby"

got_spec=$(cd "$repo" && changed_spec_files main)
check "changed_spec_files: includes untracked _spec.rb file" \
  bash -c "printf '%s' \"\$1\" | grep -qx 'spec/bar_spec.rb'" _ "$got_spec"
check "changed_spec_files: excludes non-spec .rb files" \
  not bash -c "printf '%s' \"\$1\" | grep -qx 'lib/foo.rb'" _ "$got_spec"

got_source=$(cd "$repo" && changed_source_files main)
check "changed_source_files: includes tracked and untracked non-spec .rb files" \
  bash -c "printf '%s' \"\$1\" | grep -qx 'lib/bar.rb' && printf '%s' \"\$1\" | grep -qx 'lib/foo.rb'" _ "$got_source"
check "changed_source_files: excludes _spec.rb files (heal loop autocorrects source only)" \
  not bash -c "printf '%s' \"\$1\" | grep -qx 'spec/bar_spec.rb'" _ "$got_source"

mkdir -p "$repo/scratchpad/some-task"
echo '# plan' > "$repo/scratchpad/some-task/plan.md"
got_all=$(cd "$repo" && changed_all_files main)
check "changed_all_files: includes non-.rb untracked files (for the diff-vs-notes warning)" \
  bash -c "printf '%s' \"\$1\" | grep -qx 'notes.md'" _ "$got_all"
check "changed_all_files: excludes migite's own scratchpad/" \
  not bash -c "printf '%s' \"\$1\" | grep -q '^scratchpad/'" _ "$got_all"
check "changed_all_files: excludes a working-tree delete" \
  not bash -c "printf '%s' \"\$1\" | grep -qx 'lib/baz.rb'" _ "$got_all"

# ── Frontend files, and a diff with no Ruby at all ──────────────────────────
# A frontend-only diff (views + Stimulus, no .rb, no specs) used to abort the
# whole run: grep found nothing, exited 1, and set -euo pipefail killed
# `X=$(changed_spec_files ...)` in the heal loop.
fe_repo=$(make_fixture_repo)
mkdir -p "$fe_repo/app/views/items" "$fe_repo/app/javascript/controllers" "$fe_repo/vendor/javascript" \
  "$fe_repo/app/assets/builds" "$fe_repo/spec/system" "$fe_repo/spec/models"
echo '# readme' > "$fe_repo/README.md"
git -C "$fe_repo" add README.md
git -C "$fe_repo" commit -q -m init
echo '<%= turbo_frame_tag "items" %>' > "$fe_repo/app/views/items/index.html.erb"
echo 'export default class extends Controller {}' > "$fe_repo/app/javascript/controllers/modal_controller.js"
echo '// pinned' > "$fe_repo/vendor/javascript/stimulus.js"
echo '// built' > "$fe_repo/app/assets/builds/application.js"
echo 'export default []' > "$fe_repo/eslint.config.js"

got_fe=$(cd "$fe_repo" && changed_frontend_files main)
check "changed_frontend_files: includes a changed view template" \
  bash -c "printf '%s' \"\$1\" | grep -qx 'app/views/items/index.html.erb'" _ "$got_fe"
check "changed_frontend_files: includes a new Stimulus controller" \
  bash -c "printf '%s' \"\$1\" | grep -qx 'app/javascript/controllers/modal_controller.js'" _ "$got_fe"
check "changed_frontend_files: excludes vendored and built JavaScript" \
  not bash -c "printf '%s' \"\$1\" | grep -qE 'vendor/|assets/builds/'" _ "$got_fe"
check "changed_frontend_files: a root JS config file is tooling, not frontend" \
  not bash -c "printf '%s' \"\$1\" | grep -qx 'eslint.config.js'" _ "$got_fe"

check "changed_ruby_files: a diff with no .rb files survives set -euo pipefail" \
  bash -c 'set -euo pipefail; source "$1/lib/config.sh"; source "$1/lib/stack.sh"; cd "$2"; x=$(changed_ruby_files main); y=$(changed_spec_files main); test -z "$x$y"' \
  _ "$REPO_ROOT" "$fe_repo"
check "changed_frontend_files: a backend-only diff survives set -euo pipefail" \
  bash -c 'set -euo pipefail; source "$1/lib/config.sh"; source "$1/lib/stack.sh"; cd "$2"; x=$(changed_frontend_files main); test -z "$x"' \
  _ "$REPO_ROOT" "$repo"

echo 'describe "items", type: :system do; end' > "$fe_repo/spec/system/items_spec.rb"
echo 'describe Item do; end' > "$fe_repo/spec/models/item_spec.rb"
got_specs_on=$(cd "$fe_repo" && changed_spec_files main)
got_specs_off=$(cd "$fe_repo" && MIGITE_CFG_FRONTEND_SYSTEM_SPECS=off changed_spec_files main)
check "changed_spec_files: system specs run by default (frontend.system_specs: on)" \
  bash -c "printf '%s' \"\$1\" | grep -qx 'spec/system/items_spec.rb'" _ "$got_specs_on"
check "changed_spec_files: frontend.system_specs: off drops spec/system" \
  not bash -c "printf '%s' \"\$1\" | grep -q 'spec/system/'" _ "$got_specs_off"
check "changed_spec_files: frontend.system_specs: off keeps every other spec" \
  bash -c "printf '%s' \"\$1\" | grep -qx 'spec/models/item_spec.rb'" _ "$got_specs_off"
