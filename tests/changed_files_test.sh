# tests/changed_files_test.sh — helpers.sh:changed_ruby_files / changed_spec_files
#
# A file that hasn't been `git add`-ed yet is invisible to `git diff`, and thus
# to rubocop/rspec — this blind spot recurred at least five times in production
# (migite-improvements.md: 2026-07-23, 2026-08-05, 2026-08-11, 2026-08-12,
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
