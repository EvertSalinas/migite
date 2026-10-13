# tests/smoke_check_test.sh - Phase 3.2, run_smoke_check (lib/phases/smoke.sh) through
# migite.smoke: off writes nothing; on plans once (tests/fake-claude on PATH, so no real
# model call) and sends the calls to an HTTP server on 127.0.0.1; a fix round reuses the
# plan; a service that is down or a failed tool degrades the run instead of ending it; the
# commit banner and the strict policy read SMOKE_RESULT.

if ! "$MIGITE_PYTHON" -c 'import sys' &>/dev/null; then
  echo "  · no working Python at $MIGITE_PYTHON — skipping smoke check checks"
  return 0 2>/dev/null || exit 0
fi
# shellcheck source=../lib/phases/smoke.sh
source "$REPO_ROOT/lib/phases/smoke.sh"

sc_dir=$(mktemp -d); CLEANUP_DIRS+=("$sc_dir")
_sc_saved_agent="${MIGITE_AGENT:-}" _sc_saved_xdg="${XDG_CONFIG_HOME:-}" _sc_saved_path="$PATH"
export MIGITE_AGENT=claude XDG_CONFIG_HOME="$sc_dir/config"
mkdir -p "$XDG_CONFIG_HOME" "$sc_dir/bin"
chmod +x "$SCRIPT_DIR/fake-claude"
ln -sf "$SCRIPT_DIR/fake-claude" "$sc_dir/bin/claude"
PATH="$sc_dir/bin:$PATH"

# A loopback server: /up answers 200, or 500 when $SC_UP_STATUS says so; the port it picked
# goes to a file.
cat > "$sc_dir/server.py" <<'EOF'
import os, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        self.send_response(int(os.environ.get("SC_UP_STATUS", "200")) if self.path == "/up" else 404)
        self.end_headers(); self.wfile.write(b"ok")
srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
open(sys.argv[1], "w").write(str(srv.server_address[1]))
srv.serve_forever()
EOF
"$MIGITE_PYTHON" "$sc_dir/server.py" "$sc_dir/port" & sc_server_pid=$!
SC_UP_STATUS=500 "$MIGITE_PYTHON" "$sc_dir/server.py" "$sc_dir/port-bad" & sc_bad_pid=$!
for _ in 1 2 3 4 5 6 7 8 9 10; do [[ -s "$sc_dir/port" && -s "$sc_dir/port-bad" ]] && break; sleep 0.2; done
sc_url="http://127.0.0.1:$(cat "$sc_dir/port")"
sc_bad_url="http://127.0.0.1:$(cat "$sc_dir/port-bad")"
sc_closed_port=$("$MIGITE_PYTHON" -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()')

sc_repo=$(make_fixture_repo)
mkdir -p "$sc_repo/app/controllers" "$sc_repo/config"
echo 'Rails.application.routes.draw {}' > "$sc_repo/config/routes.rb"
git -C "$sc_repo" add -A
git -C "$sc_repo" commit -q -m init
echo 'Rails.application.routes.draw { get "/up", to: "health#show" }' > "$sc_repo/config/routes.rb"   # tracked change
echo 'class HealthController; end' > "$sc_repo/app/controllers/health_controller.rb"               # untracked new file

# sc_setup <case> <mode> <base_url> [stack] - a fresh scratchpad, vault, ledger and repo config.
# shellcheck disable=SC2034  # the globals are read by run_smoke_check and show_commit_context
sc_setup() {
  SCRATCHPAD_DIR="$sc_dir/s-$1"; TASK_DIR="$sc_dir/v-$1"
  set_run_paths "00-build"
  REPO_ROOT="$sc_repo" BASE_BRANCH=main STACK="${4:-rails}"
  printf '# Testing Plan\n1. GET /up\n' > "$TESTING_PLAN_FILE"
  printf '{"smoke": {"check": "%s", "base_url": "%s"}}\n' "$2" "$3" > "$sc_repo/.migite.json"
  export MIGITE_CFG_SMOKE_CHECK="$2" MIGITE_CFG_UI_NOTIFY=off
  MIGITE_USAGE_LEDGER="$sc_dir/ledger-$1.jsonl"
  : > "$MIGITE_USAGE_LEDGER"
  export MIGITE_USAGE_LEDGER FAKE_CLAUDE_MODE=smoke_plan
  unset SMOKE_RESULT SMOKE_REASON
}
sc_ledger_lines() { wc -l < "$MIGITE_USAGE_LEDGER" | tr -d ' '; }

# ── off: nothing happens ────────────────────────────────────────────────────
(
  sc_setup off off "$sc_url"
  ( cd "$sc_repo" && run_smoke_check ) > "$sc_dir/out-off.txt" 2>&1
  check "run_smoke_check: smoke.check: off writes nothing" \
    bash -c '[[ ! -e "$1" && ! -e "$2" ]]' _ "$SMOKE_PLAN_FILE" "$SMOKE_CHECK_FILE"
  check "run_smoke_check: smoke.check: off makes no model call" test ! -s "$MIGITE_USAGE_LEDGER"
)

# ── on, service up: one planning call, the calls sent, both reports written ──
(
  sc_setup on on "$sc_url"
  export FAKE_CLAUDE_PROMPT="$sc_dir/prompt-on.txt"
  ( cd "$sc_repo" && run_smoke_check; echo "result=$SMOKE_RESULT" ) > "$sc_dir/out-on.txt" 2>&1
  check "run_smoke_check: on writes smoke-plan.json with the validated calls and the dropped ones" \
    bash -c 'grep -q "\"id\": \"up\"" "$1" && grep -q "smoke.allow_writes" "$1" && grep -q "absolute URL" "$1"' _ "$SMOKE_PLAN_FILE"
  check "run_smoke_check: the report says PASS and names the URL" \
    bash -c 'grep -q "^Result: PASS" "$1" && grep -q "^URL: $2" "$1"' _ "$SMOKE_CHECK_FILE" "$sc_url"
  check "run_smoke_check: smoke-check.json carries the envelope and the counts" \
    test "$(json_field "$SMOKE_CHECK_JSON" tool)|$(json_field "$SMOKE_CHECK_JSON" result)|$(json_field "$SMOKE_CHECK_JSON" counts.pass)" = "migite-smoke|pass|1"
  check "run_smoke_check: SMOKE_RESULT is pass" grep -q "^result=pass$" "$sc_dir/out-on.txt"
  check "run_smoke_check: one model call, labelled smoke_plan, on the standard tier" \
    bash -c '[[ "$(wc -l < "$1" | tr -d " ")" == 1 ]] && grep -q "\"label\": \"smoke_plan\"" "$1" && grep -q "\"model\": \"claude-sonnet-5\"" "$1"' _ "$MIGITE_USAGE_LEDGER"
  check "run_smoke_check: the prompt carries the diff, the testing plan and the base URL" \
    bash -c 'grep -q "health#show" "$1" && grep -q "GET /up" "$1" && grep -q "$2" "$1"' _ "$FAKE_CLAUDE_PROMPT" "$sc_url"
  check "run_smoke_check: the plan and both reports are mirrored to the vault" \
    bash -c '[[ -s "$1/smoke-plan.json" && -s "$1/smoke-check.md" && -s "$1/smoke-check.json" ]]' _ "$RUN_VAULT_DIR"
  # A fix round reuses the plan: no new call, the calls are sent again
  rm -f "$SMOKE_CHECK_FILE"
  ( cd "$sc_repo" && run_smoke_check --rerun ) > "$sc_dir/out-rerun.txt" 2>&1
  check "run_smoke_check --rerun: reuses the plan for the same changed files (no model call)" \
    bash -c '[[ "$(wc -l < "$1" | tr -d " ")" == 1 ]]' _ "$MIGITE_USAGE_LEDGER"
  check "run_smoke_check --rerun: sends the calls again" grep -q "^Result: PASS" "$SMOKE_CHECK_FILE"
  # Until the changed files differ
  echo 'class ItemsController; end' > "$sc_repo/app/controllers/items_controller.rb"
  ( cd "$sc_repo" && run_smoke_check --rerun ) > "$sc_dir/out-rerun2.txt" 2>&1
  check "run_smoke_check --rerun: plans again when the changed files differ" \
    bash -c '[[ "$(wc -l < "$1" | tr -d " ")" == 2 ]]' _ "$MIGITE_USAGE_LEDGER"
  rm -f "$sc_repo/app/controllers/items_controller.rb"
)

# ── a failing call: fail, the run goes on, the banner and the strict policy see it ─
(
  sc_setup fail on "$sc_bad_url"
  out=$( (set -euo pipefail; cd "$sc_repo"; run_smoke_check; echo "result=$SMOKE_RESULT reason=$SMOKE_REASON") 2>&1 )
  check "run_smoke_check: a failed call is SMOKE_RESULT=fail with the reason, under set -euo pipefail" \
    bash -c '[[ "$1" == *"result=fail reason=1 of 1 calls failed"* ]]' _ "$out"
  check "run_smoke_check: the report says FAIL with the status that missed" \
    bash -c 'grep -q "^Result: FAIL" "$1" && grep -q "status 500 not in \[200\]" "$1"' _ "$SMOKE_CHECK_FILE"
  banner=$(SMOKE_RESULT=fail SMOKE_REASON="1 of 1 calls failed" show_commit_context 2>&1 || true)
  check "show_commit_context: a Smoke line with the FAIL result" \
    bash -c '[[ "$1" == *"Smoke:"*"FAIL - 1 of 1 calls failed"* ]]' _ "$banner"
  banner=$(SMOKE_RESULT=pass SMOKE_CHECK_FILE="$sc_dir/none" show_commit_context 2>&1 || true)
  check "show_commit_context: a Smoke line with the PASS result" \
    bash -c '[[ "$1" == *"Smoke:"*"PASS"* ]]' _ "$banner"
  banner=$(SMOKE_RESULT=skipped SMOKE_REASON="nothing listens" SMOKE_CHECK_FILE="$sc_dir/none" show_commit_context 2>&1 || true)
  check "show_commit_context: a skipped check shows the reason" \
    bash -c '[[ "$1" == *"Smoke:"*"skipped - nothing listens"* ]]' _ "$banner"
  banner=$(SMOKE_RESULT="" show_commit_context 2>&1 || true)
  check "show_commit_context: no Smoke line when the check did not run" \
    bash -c '[[ "$1" != *"Smoke:"* ]]' _ "$banner"
)

# ── the strict policy: a smoke FAIL blocks y, unless require_smoke_pass is off ─
# _commit_gate_blockers is nested in run_review; the same check, standalone.
(
  sc_setup policy on "$sc_url"
  STACK=generic
  sc_blockers() {
    local -a b=()
    [[ "$(cfg gates.commit.require_smoke_pass true)" == "true" && "${SMOKE_RESULT:-}" == "fail" ]] \
      && b+=("smoke check failed: ${SMOKE_REASON:-see smoke-check.md}")
    printf '%s\n' "${b[@]+"${b[@]}"}"
  }
  check "strict policy: smoke check failed is a blocker (require_smoke_pass default)" \
    test "$(SMOKE_RESULT=fail SMOKE_REASON="2 of 3 calls failed" sc_blockers)" = "smoke check failed: 2 of 3 calls failed"
  check "strict policy: skipped and not run never block" \
    test -z "$(SMOKE_RESULT=skipped sc_blockers)$(SMOKE_RESULT="not run" sc_blockers)"
  check "strict policy: gates.commit.require_smoke_pass: false drops the blocker" \
    test -z "$(MIGITE_CFG_GATES_COMMIT_REQUIRE_SMOKE_PASS=false SMOKE_RESULT=fail sc_blockers)"
  check "review.sh: the blocker is wired into _commit_gate_blockers" \
    grep -q 'smoke check failed:' "$MIGITE_HOME/lib/phases/review.sh"
  check "review.sh: the smoke report reaches migite-review (--smoke-check) on both review runs" \
    test "$(grep -c 'SMOKE_REVIEW_ARGS\[@\]+' "$MIGITE_HOME/lib/phases/review.sh")" = 2
  check "review.sh: a fix round re-runs the smoke check" grep -q 'run_smoke_check --rerun' "$MIGITE_HOME/lib/phases/review.sh"
)

# ── the service is down: k skips with no model call, w checks again ──────────
(
  sc_setup down on "http://127.0.0.1:$sc_closed_port"
  read_gate_choice() { echo "$2" >> "$sc_dir/gate-down.txt"; GATE_CHOICE=k; }
  out=$( (set -euo pipefail; cd "$sc_repo"; run_smoke_check; echo "result=$SMOKE_RESULT") 2>&1 )
  check "run_smoke_check: service down, k skips with the reason and no model call" \
    bash -c '[[ "$1" == *"result=skipped"* && ! -s "$2" ]] && grep -q "No service answers at http://127.0.0.1:$3" "$4"' \
    _ "$out" "$MIGITE_USAGE_LEDGER" "$sc_closed_port" "$sc_dir/gate-down.txt"
  check "run_smoke_check: service down writes no report" test ! -e "$SMOKE_CHECK_FILE"
  # w: the user starts the server (here: the config now points at the running one) and migite looks again
  read_gate_choice() { GATE_CHOICE=w; printf '{"smoke": {"check": "on", "base_url": "%s"}}\n' "$sc_url" > "$sc_repo/.migite.json"; }
  out=$( (set -euo pipefail; cd "$sc_repo"; run_smoke_check; echo "result=$SMOKE_RESULT") 2>&1 )
  check "run_smoke_check: service down, w detects again and goes on" \
    bash -c '[[ "$1" == *"result=pass"* ]]' _ "$out"
)

# ── ask: N skips before any call; y runs ─────────────────────────────────────
(
  sc_setup ask ask "$sc_url"
  read_gate_choice() { echo "$1:$2" >> "$sc_dir/gate-ask.txt"; GATE_CHOICE=N; }
  ( cd "$sc_repo" && run_smoke_check; echo "result=[$SMOKE_RESULT]" ) > "$sc_dir/out-ask.txt" 2>&1
  check "run_smoke_check: ask answered N skips with no model call and no report" \
    bash -c 'grep -q "result=\[\]" "$1" && [[ ! -s "$2" && ! -e "$3" ]] && grep -q "SMOKE CHECK:Send real requests to $4" "$5"' \
    _ "$sc_dir/out-ask.txt" "$MIGITE_USAGE_LEDGER" "$SMOKE_CHECK_FILE" "$sc_url" "$sc_dir/gate-ask.txt"
  read_gate_choice() { GATE_CHOICE=y; }
  ( cd "$sc_repo" && run_smoke_check; echo "result=[$SMOKE_RESULT]" ) > "$sc_dir/out-ask-y.txt" 2>&1
  check "run_smoke_check: ask answered y plans and sends" grep -q "result=\[pass\]" "$sc_dir/out-ask-y.txt"
)

# ── nothing a request can reach: no call on rails; a profile always runs ─────
(
  rm -f "$sc_repo/.migite.json"
  git -C "$sc_repo" stash -q -u     # the fixture's own route and controller change, set aside for this case
  sc_setup reach on "$sc_url"
  mkdir -p "$sc_repo/app"
  echo 'class Item; end' > "$sc_repo/app/item.rb"
  ( cd "$sc_repo" && run_smoke_check; echo "result=[$SMOKE_RESULT]" ) > "$sc_dir/out-reach.txt" 2>&1
  check "run_smoke_check: a rails diff with no route, controller or view skips with no model call" \
    bash -c 'grep -q "nothing to call" "$1" && [[ ! -s "$2" ]]' _ "$sc_dir/out-reach.txt" "$MIGITE_USAGE_LEDGER"
  ( cd "$sc_repo" && STACK=generic run_smoke_check; echo "result=[$SMOKE_RESULT]" ) > "$sc_dir/out-reach-generic.txt" 2>&1
  check "run_smoke_check: on another stack the check runs whatever the diff touches" \
    grep -q "result=\[pass\]" "$sc_dir/out-reach-generic.txt"
  rm -f "$sc_repo/app/item.rb" "$sc_repo/.migite.json"
  git -C "$sc_repo" stash pop -q
)
sc_reachable() {   # the fixture's tracked route change and untracked controller, nothing else
  local out
  out=$(cd "$sc_repo" && changed_reachable_files main) || return 1
  [[ "$out" == $'app/controllers/health_controller.rb\nconfig/routes.rb' ]]
}
check "changed_reachable_files: routes and controllers count, tracked and untracked alike" sc_reachable
mkdir -p "$sc_repo/app/models" "$sc_repo/spec/models" "$sc_repo/db/migrate" "$sc_repo/lib/tasks"
touch "$sc_repo/app/models/item.rb" "$sc_repo/spec/models/item_spec.rb" "$sc_repo/db/migrate/1_x.rb" "$sc_repo/lib/tasks/x.rake"
sc_reachable_more() {
  local out
  out=$(cd "$sc_repo" && changed_reachable_files main) || return 1
  [[ "$out" == *"lib/tasks/x.rake"* && "$out" != *"app/models"* && "$out" != *"spec/"* && "$out" != *"db/migrate"* ]]
}
check "changed_reachable_files: lib/ counts; models, specs and migrations don't" sc_reachable_more
rm -rf "$sc_repo/app/models" "$sc_repo/spec" "$sc_repo/db" "$sc_repo/lib"
unset -f sc_reachable sc_reachable_more

# ── a failed planning call degrades the run ──────────────────────────────────
(
  for _sc_mode in exit1 empty; do
    sc_setup "fail-$_sc_mode" on "$sc_url"
    export FAKE_CLAUDE_MODE="$_sc_mode"
    out=$( (set -euo pipefail; cd "$sc_repo"; run_smoke_check; echo "result=[$SMOKE_RESULT]") 2>&1 )
    check "run_smoke_check: a '$_sc_mode' planning reply warns, is 'not run', and completes under set -euo pipefail" \
      bash -c '[[ "$1" == *"no call list could be planned"* && "$1" == *"result=[not run]"* ]]' _ "$out"
    check "run_smoke_check: a '$_sc_mode' planning reply leaves no plan or report behind" \
      bash -c '[[ ! -e "$1" && ! -e "$2" ]]' _ "$SMOKE_PLAN_FILE" "$SMOKE_CHECK_FILE"
  done
)

kill "$sc_server_pid" "$sc_bad_pid" 2>/dev/null || true
wait "$sc_server_pid" "$sc_bad_pid" 2>/dev/null || true
unset -f sc_setup sc_ledger_lines
PATH="$_sc_saved_path"
if [[ -n "$_sc_saved_agent" ]]; then export MIGITE_AGENT="$_sc_saved_agent"; else unset MIGITE_AGENT; fi
if [[ -n "$_sc_saved_xdg" ]]; then export XDG_CONFIG_HOME="$_sc_saved_xdg"; else unset XDG_CONFIG_HOME; fi
unset MIGITE_CFG_SMOKE_CHECK FAKE_CLAUDE_MODE FAKE_CLAUDE_PROMPT
