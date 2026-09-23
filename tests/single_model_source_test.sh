# tests/single_model_source_test.sh — model ids live in exactly one place.
#
# migite_config.py's DEFAULTS is the only file allowed to name a model id.
# The tools' module-level constants come from migite_config.default_model(),
# and bash's cfg_model asks the resolver. There used to be three copies (the
# DEFAULTS table, six scripts' constants, and a bash `case` block) and they
# drifted on the very first model change.

offenders=$(grep -lE 'claude-(haiku|sonnet|opus|fable)-[0-9]' \
  "$REPO_ROOT"/migite "$REPO_ROOT"/migite.d/*.sh "$REPO_ROOT"/migite-plan "$REPO_ROOT"/migite-review \
  "$REPO_ROOT"/migite-*.py "$REPO_ROOT"/migite_claude.py "$REPO_ROOT"/migite_paths.py 2>/dev/null \
  | xargs -I{} basename {} | sort | tr '\n' ' ')
check "single model source: no model id outside migite_config.py (found: ${offenders:-none})" \
  test -z "$offenders"

check "single model source: migite_config.py does name the three tier defaults" \
  bash -c 'grep -q "\"fast\": \"claude-haiku" "$1" && grep -q "\"standard\": \"claude-sonnet" "$1" && grep -q "\"strong\": \"claude-opus" "$1"' _ "$REPO_ROOT/migite_config.py"

if "$MIGITE_PYTHON" -c 'import sys' &>/dev/null; then
  # bash fallback (no config loaded) must equal the resolver's built-in default
  ( unset MIGITE_CFG_MODEL_THINK MIGITE_CFG_MODEL_EXPLORE
    check "cfg_model without a loaded config asks the resolver (think → strong default)" \
      test "$(cfg_model think)" = "$("$MIGITE_PYTHON" "$REPO_ROOT/migite_config.py" get model:think)"
    check "cfg_model without a loaded config asks the resolver (explore → fast default)" \
      test "$(cfg_model explore)" = "$("$MIGITE_PYTHON" "$REPO_ROOT/migite_config.py" get model:explore)" )
  check "default_model: every role resolves to one of the three tier defaults" \
    "$MIGITE_PYTHON" -c '
import sys; sys.path.insert(0, "'"$REPO_ROOT"'")
import migite_config as c
tiers = {c.BACKEND_MODEL_DEFAULTS["claude"][t] for t in ("fast", "standard", "strong")}
assert all(c.default_model(r) in tiers for r in c.ROLE_TIERS), "role default outside tier table"'
fi
