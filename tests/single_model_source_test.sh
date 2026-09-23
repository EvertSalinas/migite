# tests/single_model_source_test.sh — model ids live in exactly one place.
#
# Each agent's model ids are written down only in its own adapter
# (agents/<name>.py). Everything else names a role: the tools pass roles to the
# gateway, bash passes roles to `migite_agent.py ask`, and the config resolves
# role → tier → the agent's model. There used to be three copies (a config
# table, six scripts' constants, and a bash `case` block) and they drifted on the
# very first model change.

offenders=$(grep -lE 'claude-(haiku|sonnet|opus|fable)-[0-9]' \
  "$REPO_ROOT"/migite "$REPO_ROOT"/lib/*.sh "$REPO_ROOT"/lib/phases/*.sh "$REPO_ROOT"/migite-plan "$REPO_ROOT"/migite-review \
  "$REPO_ROOT"/migite-*.py "$REPO_ROOT"/migite_call.py "$REPO_ROOT"/migite_config.py \
  "$REPO_ROOT"/migite_agent.py "$REPO_ROOT"/migite_paths.py "$REPO_ROOT"/agents/base.py \
  "$REPO_ROOT"/agents/__init__.py "$REPO_ROOT"/agents/cursor.py "$REPO_ROOT"/agents/opencode.py 2>/dev/null \
  | xargs -I{} basename {} | sort | tr '\n' ' ')
check "single model source: no Claude model id outside agents/claude.py (found: ${offenders:-none})" \
  test -z "$offenders"

check "single model source: agents/claude.py names the three tier defaults" \
  bash -c 'grep -q "\"fast\": \"claude-haiku" "$1" && grep -q "\"standard\": \"claude-sonnet" "$1" && grep -q "\"strong\": \"claude-opus" "$1"' _ "$REPO_ROOT/agents/claude.py"

if "$MIGITE_PYTHON" -c 'import sys' &>/dev/null; then
  check "config resolver: every role resolves to one of the agent's tier models" \
    "$MIGITE_PYTHON" -c '
import sys; sys.path.insert(0, "'"$REPO_ROOT"'")
import agents, migite_config as c
for name in agents.names():
    tiers = {agents.info(name).models[t] or "" for t in agents.TIERS}
    assert all(c.default_model(r, name) in tiers for r in c.ROLE_TIERS), f"{name}: role default outside tier table"'
  check "config resolver: the starter template shows the Claude defaults it read from the adapter" \
    "$MIGITE_PYTHON" -c '
import sys; sys.path.insert(0, "'"$REPO_ROOT"'")
import agents, migite_config as c
assert "@" + "FAST@" not in c.STARTER_TEMPLATE
assert agents.info("claude").models["strong"] in c.STARTER_TEMPLATE'
fi
