#!/usr/bin/env python3
"""tests/test_smoke.py - migite.smoke, the Phase 3.2 API smoke check: the pure helpers,
the safety rules on a planned call list, the `plan` call (the agent mocked), `run` against
an HTTP server on 127.0.0.1 (no other network), and `detect` with docker / lsof / ps shims.
Run: python3 -m unittest tests/test_smoke.py"""

import json
import os
import socket
import stat
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from migite import config  # noqa: E402
from migite import smoke   # noqa: E402

SECRET = "s3cr3t-token-value"


class _Handler(BaseHTTPRequestHandler):
    """A few routes a smoke plan can hit; /echo returns the request headers as JSON."""
    log: list[str] = []

    def log_message(self, *args):  # silence
        pass

    def _send(self, status: int, body: bytes = b"", headers: dict | None = None) -> None:
        self.send_response(status)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        _Handler.log.append(f"GET {self.path}")
        if self.path == "/up":
            return self._send(200, b"ok")
        if self.path.startswith("/api/items/0"):
            return self._send(500, f"RuntimeError boom {SECRET}".encode())
        if self.path.startswith("/api/items"):
            return self._send(200, json.dumps({"data": [{"id": 1}]}).encode(), {"Content-Type": "application/json"})
        if self.path == "/echo":
            return self._send(200, json.dumps(dict(self.headers)).encode(), {"Content-Type": "application/json"})
        if self.path == "/redir":
            return self._send(302, b"", {"Location": "http://example.com/"})
        if self.path == "/slow":
            time.sleep(2)
            return self._send(200, b"late")
        return self._send(404, b"nope")

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        return self._send(201, body, {"Content-Type": "application/json"})


class _ServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        cls.port = cls.server.server_address[1]
        cls.base_url = f"http://127.0.0.1:{cls.port}"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        _Handler.log.clear()


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class HelpersTest(unittest.TestCase):
    def test_loopback_urls_only(self):
        for url in ("http://127.0.0.1:3000", "http://localhost", "https://[::1]:8443/x"):
            self.assertTrue(smoke.is_loopback(url), url)
        for url in ("http://10.0.0.1:3000", "http://example.com", "http://127.0.0.1@evil.com", "ftp://127.0.0.1", "", "127.0.0.1:3000"):
            self.assertFalse(smoke.is_loopback(url), url)

    def test_port_parsers(self):
        self.assertEqual(smoke.parse_port_from_puma('port ENV.fetch("PORT") { 3001 }\n'), 3001)
        self.assertEqual(smoke.parse_port_from_puma("port ENV.fetch('PORT', 3002)\n"), 3002)
        self.assertEqual(smoke.parse_port_from_puma("port 3003\n"), 3003)
        self.assertIsNone(smoke.parse_port_from_puma("threads 5, 5\n"))
        self.assertEqual(smoke.parse_port_from_procfile("web: bin/rails server -p 3004\ncss: x\n"), 3004)
        self.assertEqual(smoke.parse_port_from_procfile("web: PORT=3005 bin/rails s\n"), 3005)
        self.assertIsNone(smoke.parse_port_from_procfile("js: yarn build --watch\n"))
        self.assertEqual(smoke.parse_port_from_dotenv("RAILS_ENV=development\nPORT=3006\n"), 3006)
        self.assertEqual(smoke.parse_port_from_dotenv("export PORT='3007'\n"), 3007)

    def test_local_port_precedence_and_default(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self.assertEqual(smoke.local_port(root), (3000, "default"))
            (root / "config").mkdir()
            (root / "config" / "puma.rb").write_text('port ENV.fetch("PORT") { 4000 }\n')
            self.assertEqual(smoke.local_port(root), (4000, "puma"))
            (root / "Procfile.dev").write_text("web: bin/rails s -p 4100\n")
            self.assertEqual(smoke.local_port(root), (4100, "procfile"))
            (root / ".env").write_text("PORT=4200\n")
            self.assertEqual(smoke.local_port(root), (4200, "dotenv"))
            self.assertEqual(smoke.local_port(root, 4300), (4300, "config"))

    def test_restart_sensitive_files_and_staleness(self):
        changed = ["app/models/x.rb", "config/routes.rb", "config/initializers/cors.rb", "Gemfile.lock",
                   ".env.development", "rails-app/config/environments/development.rb"]
        self.assertEqual(smoke.restart_sensitive(changed),
                         ["config/initializers/cors.rb", "Gemfile.lock", ".env.development",
                          "rails-app/config/environments/development.rb"])
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "Gemfile.lock").write_text("x")
            now = time.time()
            self.assertIsNone(smoke.stale_by_mtime(root, ["Gemfile.lock", "missing"], now + 60))
            reason = smoke.stale_by_mtime(root, ["Gemfile.lock"], now - 3600)
            self.assertIn("Gemfile.lock changed", reason or "")

    def test_json_has_resolves_dotted_paths_with_list_indexes(self):
        obj = {"data": [{"id": 1}], "meta": {"total": 0}}
        self.assertTrue(smoke.json_has(obj, "data.0.id"))
        self.assertTrue(smoke.json_has(obj, "meta.total"))
        self.assertFalse(smoke.json_has(obj, "data.1.id"))
        self.assertFalse(smoke.json_has(obj, "meta.missing"))

    def test_truncate_keeps_head_and_tail(self):
        text = "a" * 600 + "b" * 400
        out = smoke.truncate(text, 100)
        self.assertTrue(out.startswith("a" * 60))
        self.assertTrue(out.endswith("b" * 40))
        self.assertIn("bytes elided", out)
        self.assertEqual(smoke.truncate("short", 100), "short")

    def test_expand_headers_from_env_and_missing_vars(self):
        env = {"TOK": SECRET, "SHORT": "ab"}
        headers, secrets, missing = smoke.expand_headers(
            {"Authorization": "Bearer ${TOK}", "X-Small": "${SHORT}", "X-Plain": "literal", "X-Gone": "v ${NOPE}"}, env)
        self.assertEqual(headers, {"Authorization": f"Bearer {SECRET}", "X-Small": "ab", "X-Plain": "literal"})
        self.assertEqual(secrets, {"TOK": SECRET})            # two-char values are not worth redacting
        self.assertEqual(missing, {"X-Gone": "NOPE"})

    def test_redactor_covers_plain_url_encoded_and_base64_forms(self):
        import base64
        import urllib.parse
        value = "p@ss word/1"
        redact = smoke.Redactor({"PW": value, "LONG": value + "-longer"})
        text = f"a {value} b {urllib.parse.quote(value, safe='')} c {base64.b64encode(value.encode()).decode()} d {value}-longer"
        out = redact(text)
        self.assertNotIn(value, out)
        self.assertNotIn(base64.b64encode(value.encode()).decode(), out)
        self.assertIn("[redacted ${PW}]", out)
        self.assertIn("[redacted ${LONG}]", out)                # the longer secret wins where both match
        self.assertEqual(smoke.Redactor({})("unchanged"), "unchanged")


class ValidatePlanTest(unittest.TestCase):
    def call(self, **kw):
        base = {"id": "ok", "why": "w", "method": "GET", "path": "/x", "auth": False, "expect": {"status": 200}}
        base.update(kw)
        return base

    def validate(self, calls, allow_writes=False, max_calls=12):
        return smoke.validate_plan({"calls": calls}, allow_writes=allow_writes, max_calls=max_calls)

    def test_not_a_plan_at_all(self):
        self.assertIsNone(smoke.validate_plan({"summary": "x"}, allow_writes=False, max_calls=5))
        self.assertIsNone(smoke.validate_plan("nope", allow_writes=False, max_calls=5))

    def test_absolute_urls_and_hosts_in_paths_are_dropped(self):
        calls, dropped = self.validate([
            self.call(id="abs", path="http://example.com/"),
            self.call(id="proto", path="//example.com/"),
            self.call(id="at", path="/x@example.com/"),
            self.call(id="sp", path="/x y"),
            self.call(id="good", path="/x?y=z@q"),
        ])
        self.assertEqual([c["id"] for c in calls], ["good"])
        self.assertEqual({d["id"] for d in dropped}, {"abs", "proto", "at", "sp"})

    def test_write_methods_need_allow_writes(self):
        calls, dropped = self.validate([self.call(id="w", method="post", body={"a": 1})])
        self.assertEqual(calls, [])
        self.assertIn("smoke.allow_writes", dropped[0]["reason"])
        calls, dropped = self.validate([self.call(id="w", method="post", body={"a": 1})], allow_writes=True)
        self.assertEqual(calls[0]["method"], "POST")
        self.assertEqual(calls[0]["body"], {"a": 1})
        self.assertEqual(dropped, [])

    def test_a_body_on_a_get_and_env_refs_in_headers_are_dropped(self):
        calls, dropped = self.validate([
            self.call(id="b", body={"a": 1}),
            self.call(id="h", headers={"X-Token": "${TOK}"}),
            self.call(id="r", headers={"Authorization": "x"}),
            self.call(id="ok", headers={"X-Request-Id": "abc"}),
        ])
        self.assertEqual([c["id"] for c in calls], ["ok"])
        self.assertEqual(calls[0]["headers"], {"X-Request-Id": "abc"})
        self.assertEqual({d["id"] for d in dropped}, {"b", "h", "r"})

    def test_ids_are_slugs_and_unique_and_the_list_is_capped(self):
        calls, dropped = self.validate([
            self.call(id="Bad Id"), self.call(id="dup"), self.call(id="dup", path="/again"),
            self.call(id="a"), self.call(id="b"), self.call(id="c"),
        ], max_calls=3)
        self.assertEqual([c["id"] for c in calls], ["dup", "a", "b"])
        self.assertEqual(calls[0]["path"], "/x")                       # the first one is kept
        reasons = [(d["id"], d["reason"]) for d in dropped]
        self.assertIn("lowercase", reasons[0][1])
        self.assertEqual(reasons[1], ("dup", "duplicate id"))
        self.assertIn("max_calls", reasons[2][1])

    def test_expectations_are_normalised(self):
        calls, _ = self.validate([
            self.call(id="s", expect={"status": [200, "x", 999, True], "json_has": ["data.0.id", "bad path"], "body_contains": "ok"}),
            self.call(id="d", expect={"status": "nope"}),
            self.call(id="n", expect="garbage"),
        ])
        self.assertEqual(calls[0]["expect"], {"status": [200], "json_has": ["data.0.id"], "body_contains": "ok"})
        self.assertEqual(calls[1]["expect"], {"status": [200]})
        self.assertEqual(calls[2]["expect"], {"status": [200]})


class PlanCommandTest(unittest.TestCase):
    """`plan` with the gateway mocked: what the prompt carries, what the file holds, exit codes."""

    REPLY = {"summary": "the health route answers", "calls": [
        {"id": "up", "why": "answers", "method": "GET", "path": "/up", "auth": True, "expect": {"status": 200}},
        {"id": "remote", "why": "x", "method": "GET", "path": "http://example.com/", "auth": False, "expect": {"status": 200}},
    ]}

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        (self.tmp / "diff.txt").write_text("+++ b/app/controllers/items_controller.rb\n+  def index\n")
        (self.tmp / "testing-plan.md").write_text("# Testing Plan\n1. curl /api/items\n")
        (self.tmp / "changed.txt").write_text("app/controllers/items_controller.rb\nconfig/routes.rb\n")
        self.cfg = mock.Mock()
        self.cfg_values = {"smoke.allow_writes": False, "smoke.max_calls": 12,
                           "smoke.headers": {"Authorization": "Bearer ${TOK}"}, "smoke.base_url": None}
        self.cfg.get.side_effect = lambda k, d=None: self.cfg_values.get(k, d)
        self.cfg.prompt_path.return_value = ROOT / "prompts" / "smoke_plan.md"
        for target in (mock.patch.object(smoke.config, "load", return_value=self.cfg),
                       mock.patch.object(smoke.gateway, "configure_from"),
                       mock.patch.dict(os.environ, {"TOK": SECRET})):
            target.start()
            self.addCleanup(target.stop)

    def run_plan(self, reply=None, *, structured=True, raises=None, extra=()):
        text = json.dumps(reply if reply is not None else self.REPLY)
        res = SimpleNamespace(text="```json\n" + text + "\n```" if not structured else "", structured=reply if structured and reply is not None else (self.REPLY if structured else None))
        fake = mock.Mock(side_effect=raises) if raises else mock.Mock(return_value=res)
        out = self.tmp / "smoke-plan.json"
        with mock.patch.object(smoke.gateway, "call_agent", fake), \
             mock.patch.object(smoke.gateway, "supports", return_value=structured), \
             mock.patch.object(smoke.gateway, "AGENT", SimpleNamespace(name="fake")), \
             mock.patch("builtins.print"):
            rc = smoke.main(["--repo-root", str(self.tmp), "plan", "--diff", str(self.tmp / "diff.txt"),
                             "--testing-plan", str(self.tmp / "testing-plan.md"), "--changed-files", str(self.tmp / "changed.txt"),
                             "--base-url", "http://127.0.0.1:3000", "--out", str(out), *extra])
        return rc, fake, out

    def test_role_label_tool_and_schema(self):
        rc, fake, out = self.run_plan()
        self.assertEqual(rc, 0)
        self.assertEqual(fake.call_args.args[1], "smoke_plan")
        self.assertEqual(fake.call_args.kwargs["label"], "smoke_plan")
        self.assertEqual(fake.call_args.kwargs["tool"], "migite")
        self.assertIs(fake.call_args.kwargs["schema"], smoke.SMOKE_PLAN_SCHEMA)
        self.assertEqual(config.ROLE_TIERS["smoke_plan"], "standard")
        self.assertEqual(config.ROLE_TOOLS["smoke_plan"], "read")

    def test_the_prompt_carries_the_inputs_and_header_names_but_never_a_value(self):
        _, fake, _ = self.run_plan()
        prompt = fake.call_args.args[0]
        for needle in ("def index", "curl /api/items", "config/routes.rb", "http://127.0.0.1:3000", "Authorization",
                       "Only GET, HEAD and OPTIONS are sent", "at most 12 calls"):
            self.assertIn(needle, prompt)
        self.assertNotIn(SECRET, prompt)

    def test_the_file_holds_the_validated_calls_the_dropped_ones_and_the_changed_files(self):
        rc, _, out = self.run_plan()
        plan = json.loads(out.read_text())
        self.assertEqual(plan["tool"], "migite-smoke")
        self.assertEqual([c["id"] for c in plan["calls"]], ["up"])
        self.assertEqual(plan["dropped"], [{"id": "remote", "reason": "absolute URL or host in path"}])
        self.assertEqual(plan["changed_files"], ["app/controllers/items_controller.rb", "config/routes.rb"])
        self.assertEqual(plan["auth_header_names"], ["Authorization"])
        self.assertEqual(plan["summary"], "the health route answers")

    def test_without_structured_output_the_text_is_parsed(self):
        rc, fake, out = self.run_plan(structured=False)
        self.assertEqual(rc, 0)
        self.assertIsNone(fake.call_args.kwargs["schema"])
        self.assertEqual(json.loads(out.read_text())["calls"][0]["id"], "up")

    def test_a_failed_call_or_an_unusable_reply_leaves_out_untouched(self):
        rc, _, out = self.run_plan(raises=smoke.gateway.AgentError("boom"))
        self.assertEqual(rc, 1)
        self.assertFalse(out.exists())
        rc, _, out = self.run_plan({"nothing": True})
        self.assertEqual(rc, 1)
        self.assertFalse(out.exists())
        rc, _, out = self.run_plan({"summary": "x", "calls": [self.REPLY["calls"][1]]})
        self.assertEqual(rc, 2)
        self.assertFalse(out.exists())

    def test_an_existing_plan_for_the_same_changed_files_is_reused_without_a_call(self):
        rc, _, out = self.run_plan()
        before = out.read_text()
        rc, fake, _ = self.run_plan(extra=["--existing", str(out)])
        self.assertEqual(rc, 0)
        fake.assert_not_called()
        self.assertEqual(out.read_text(), before)
        (self.tmp / "changed.txt").write_text("app/controllers/other_controller.rb\n")
        rc, fake, _ = self.run_plan(extra=["--existing", str(out)])
        fake.assert_called_once()


class RunCommandTest(_ServerTest):
    """`run` against the loopback server; the config comes from a .migite.json in the repo."""

    def setUp(self):
        super().setUp()
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        (self.tmp / "log").mkdir()
        self.log_file = self.tmp / "log" / "development.log"
        self.log_file.write_text("earlier line\n")
        self.write_config({"smoke": {"headers": {"Authorization": "Bearer ${SMOKE_TEST_TOKEN}"}, "timeout_seconds": 1}})
        self._env = dict(os.environ)
        os.environ["SMOKE_TEST_TOKEN"] = SECRET
        os.environ["XDG_CONFIG_HOME"] = str(self.tmp / "xdg")
        os.environ["HOME"] = str(self.tmp / "home")
        config._CACHE.clear()
        self.addCleanup(self._restore_env)

    def _restore_env(self):
        os.environ.clear()
        os.environ.update(self._env)
        config._CACHE.clear()

    def write_config(self, data):
        (self.tmp / ".migite.json").write_text(json.dumps(data))

    def write_plan(self, calls, dropped=()):
        p = self.tmp / "smoke-plan.json"
        p.write_text(json.dumps({"calls": calls, "dropped": list(dropped)}))
        return p

    def write_detect(self, **over):
        p = self.tmp / "detect.json"
        info = {"base_url": self.base_url, "source": "config", "state": "up", "reason": "", "compose_service": None,
                "started_at": None}
        info.update(over)
        p.write_text(json.dumps(info))
        return p

    def run_cmd(self, plan, detect=None, base_url=None):
        argv = ["--repo-root", str(self.tmp), "run", "--plan", str(plan), "--out-md", str(self.tmp / "smoke-check.md"),
                "--out-json", str(self.tmp / "smoke-check.json")]
        if detect:
            argv += ["--detect", str(detect)]
        if base_url:
            argv += ["--base-url", base_url]
        with mock.patch("builtins.print"):
            rc = smoke.main(argv)
        md = self.tmp / "smoke-check.md"
        js = self.tmp / "smoke-check.json"
        return rc, (md.read_text() if md.exists() else None), (json.loads(js.read_text()) if js.exists() else None)

    def call(self, id, path, status=200, **kw):
        c = {"id": id, "why": "w", "method": "GET", "path": path, "auth": False, "headers": {}, "expect": {"status": [status]}}
        c.update(kw)
        return c

    def test_every_call_passing_is_exit_0_and_a_pass_report(self):
        plan = self.write_plan([self.call("up", "/up"), self.call("items", "/api/items", expect={"status": [200], "json_has": ["data.0.id"]})])
        rc, md, js = self.run_cmd(plan, self.write_detect())
        self.assertEqual(rc, 0)
        self.assertIn("Result: PASS\n", md)
        self.assertIn(f"URL: {self.base_url}", md)
        self.assertEqual(js["result"], "pass")
        self.assertEqual(js["counts"], {"total": 2, "pass": 2, "fail": 0, "error": 0})
        self.assertEqual(js["tool"], "migite-smoke")
        self.assertIn("schema_version", js)

    def test_a_failing_call_is_exit_2_with_the_body_and_the_server_log_written_during_it(self):
        def append_log(*_):
            with self.log_file.open("a") as fh:
                fh.write("RuntimeError: boom in ItemsController#show\n")
        orig = _Handler.do_GET

        def do_get(handler):
            if handler.path.startswith("/api/items/0"):
                append_log()
            return orig(handler)
        with mock.patch.object(_Handler, "do_GET", do_get):
            plan = self.write_plan([self.call("boom", "/api/items/0", 404), self.call("up", "/up")],
                                   dropped=[{"id": "del", "reason": "write method DELETE needs smoke.allow_writes: true"}])
            rc, md, js = self.run_cmd(plan, self.write_detect())
        self.assertEqual(rc, 2)
        self.assertIn("Result: FAIL - 1 of 2 calls failed", md)
        self.assertIn("status 500 not in [404]", md)
        self.assertIn("ItemsController#show", md)
        self.assertNotIn("earlier line", md)                         # only what the call appended
        self.assertIn("dropped `del`", md)
        boom = next(c for c in js["calls"] if c["id"] == "boom")
        self.assertEqual(boom["result"], "FAIL")
        self.assertIn("boom", boom["body_excerpt"])
        self.assertIn("ItemsController#show", boom["log_excerpt"])
        self.assertNotIn(SECRET, md)                                  # the body echoed the token: redacted
        self.assertNotIn(SECRET, json.dumps(js))
        self.assertIn("[redacted ${SMOKE_TEST_TOKEN}]", md)

    def test_redirects_are_not_followed_and_the_location_is_recorded(self):
        plan = self.write_plan([self.call("r", "/redir", 200)])
        rc, md, js = self.run_cmd(plan, self.write_detect())
        self.assertEqual(rc, 2)
        self.assertIn("status 302 not in [200] (Location: http://example.com/)", md)
        self.assertEqual(js["calls"][0]["status"], 302)
        self.assertEqual(_Handler.log, ["GET /up", "GET /redir"])     # the probe, then the call; nothing else

    def test_a_timeout_is_an_error_not_a_hang(self):
        plan = self.write_plan([self.call("slow", "/slow")])
        rc, md, js = self.run_cmd(plan, self.write_detect())
        self.assertEqual(rc, 2)
        self.assertEqual(js["calls"][0]["result"], "ERROR")
        self.assertIn("timed out", js["calls"][0]["reason"])
        self.assertLess(js["calls"][0]["elapsed_ms"], 1900)

    def test_auth_calls_carry_the_expanded_header_and_the_reports_redact_it(self):
        plan = self.write_plan([self.call("a", "/echo", auth=True, expect={"status": [200], "body_contains": SECRET}),
                                self.call("b", "/echo", auth=False, expect={"status": [200], "body_contains": "Bearer"})])
        rc, md, js = self.run_cmd(plan, self.write_detect())
        self.assertEqual(rc, 2)                                         # b: no header without auth
        a, b = js["calls"]
        self.assertEqual(a["result"], "PASS")
        self.assertEqual(b["result"], "FAIL")
        self.assertNotIn(SECRET, md)
        self.assertNotIn(SECRET, json.dumps(js))
        self.assertIn("[redacted ${SMOKE_TEST_TOKEN}]", json.dumps(js))
        self.assertIn("auth headers attached on `auth` calls: Authorization", md)

    def test_an_unset_auth_variable_means_the_call_is_not_sent(self):
        os.environ.pop("SMOKE_TEST_TOKEN")
        plan = self.write_plan([self.call("a", "/echo", auth=True)])
        rc, md, js = self.run_cmd(plan, self.write_detect())
        self.assertEqual(rc, 2)
        self.assertEqual(js["calls"][0]["result"], "ERROR")
        self.assertIn("unset $SMOKE_TEST_TOKEN", js["calls"][0]["reason"])
        self.assertEqual(_Handler.log, ["GET /up"])                    # the probe only

    def test_a_non_loopback_url_sends_nothing_and_is_exit_1(self):
        plan = self.write_plan([self.call("up", "/up")])
        rc, md, js = self.run_cmd(plan, base_url="http://10.0.0.1:3000")
        self.assertEqual(rc, 1)
        self.assertIsNone(md)
        self.assertEqual(_Handler.log, [])
        rc, _, _ = self.run_cmd(plan, self.write_detect(base_url="http://example.com"))
        self.assertEqual(rc, 1)

    def test_a_stale_or_down_service_is_skipped_with_a_report(self):
        plan = self.write_plan([self.call("up", "/up")])
        rc, md, js = self.run_cmd(plan, self.write_detect(state="stale", reason="Gemfile.lock changed after start"))
        self.assertEqual(rc, 3)
        self.assertIn("Result: SKIPPED - Gemfile.lock changed after start", md)
        self.assertEqual(js["result"], "skipped")
        self.assertEqual(_Handler.log, [])
        rc, md, _ = self.run_cmd(plan, self.write_detect(base_url=f"http://127.0.0.1:{_free_port()}"))
        self.assertEqual(rc, 3)
        self.assertIn("Result: SKIPPED - no HTTP answer", md)
        rc, md, _ = self.run_cmd(self.write_plan([]), self.write_detect())
        self.assertEqual(rc, 3)
        self.assertIn("the plan has no calls", md)

    def test_a_missing_or_broken_plan_is_exit_1(self):
        rc, md, _ = self.run_cmd(self.tmp / "nope.json", self.write_detect())
        self.assertEqual(rc, 1)
        self.assertIsNone(md)

    def test_a_write_call_is_sent_with_its_json_body(self):
        plan = self.write_plan([self.call("c", "/items", 201, method="POST", body={"name": "x"},
                                          expect={"status": [201], "json_has": ["name"]})])
        rc, _, js = self.run_cmd(plan, self.write_detect())
        self.assertEqual(rc, 0)
        self.assertEqual(js["calls"][0]["status"], 201)


class DetectTest(_ServerTest):
    """detect with docker, lsof and ps replaced by shims first on PATH."""

    def setUp(self):
        super().setUp()
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self._env = dict(os.environ)
        os.environ["PATH"] = str(self.bin) + os.pathsep + os.environ.get("PATH", "")
        os.environ["XDG_CONFIG_HOME"] = str(self.tmp / "xdg")
        os.environ["HOME"] = str(self.tmp / "home")
        config._CACHE.clear()
        self.addCleanup(self._restore_env)
        self.shim("lsof", 'if [[ -n "${FAKE_LSOF_PID:-}" ]]; then echo "p$FAKE_LSOF_PID"; fi')
        self.shim("ps", 'echo "${FAKE_PS_LSTART:-Mon Jan  1 00:00:00 2024}"')
        self.shim("docker", r'''
case "$*" in
  "compose config --format json") cat "$FAKE_DOCKER_CONFIG" ;;
  "compose ps --format json --all "*) cat "$FAKE_DOCKER_PS" ;;
  *) exit 1 ;;
esac''')

    def _restore_env(self):
        os.environ.clear()
        os.environ.update(self._env)
        config._CACHE.clear()

    def shim(self, name, body):
        p = self.bin / name
        p.write_text("#!/usr/bin/env bash\n" + body + "\n")
        p.chmod(p.stat().st_mode | stat.S_IEXEC)

    def compose(self, *, bind=True, state="running", created=None, port=None, service="web"):
        port = port or self.port
        volumes = [{"type": "bind", "source": str(self.repo), "target": "/app"}] if bind else []
        (self.repo / "compose.yaml").write_text("services: {}\n")
        cfg = self.tmp / "compose.json"
        cfg.write_text(json.dumps({"services": {"db": {"image": "postgres"},
                                               service: {"ports": [{"published": str(port), "target": 3000}], "volumes": volumes}}}))
        ps = self.tmp / "ps.json"
        ps.write_text(json.dumps({"Service": service, "State": state, "Created": created or int(time.time()) - 60}))
        os.environ["FAKE_DOCKER_CONFIG"] = str(cfg)
        os.environ["FAKE_DOCKER_PS"] = str(ps)

    def detect(self, changed=(), **kw):
        cfg = config.load(self.repo, use_cache=False)
        return smoke.detect(self.repo, cfg, changed_files=list(changed), **kw)

    def test_base_url_from_config_is_probed_as_is(self):
        info = self.detect(base_url=self.base_url + "/")
        self.assertEqual((info["source"], info["state"], info["base_url"]), ("config", "up", self.base_url))
        self.assertEqual(info["probe"], {"path": "/up", "status": 200})

    def test_compose_service_with_a_bind_mount_and_running_is_up(self):
        self.compose()
        info = self.detect()
        self.assertEqual((info["source"], info["state"], info["compose_service"], info["port"]), ("compose", "up", "web", self.port))
        self.assertIsNotNone(info["started_at"])

    def test_compose_service_without_a_bind_mount_is_stale(self):
        self.compose(bind=False)
        info = self.detect()
        self.assertEqual(info["state"], "stale")
        self.assertIn("no bind mount", info["reason"])

    def test_compose_service_exited_and_nothing_listening_is_down(self):
        self.compose(state="exited", port=_free_port())
        os.environ.pop("FAKE_LSOF_PID", None)
        info = self.detect()
        self.assertEqual(info["state"], "down")
        self.assertIn("compose service web is exited", info["reason"])

    def test_compose_exited_but_a_local_process_serving_the_port_counts(self):
        self.compose(state="exited")
        os.environ["FAKE_LSOF_PID"] = "4242"
        info = self.detect()
        self.assertEqual((info["state"], info["source"], info["compose_service"]), ("up", "local", None))

    def test_local_process_older_than_a_changed_initializer_is_stale(self):
        (self.repo / "config" / "initializers").mkdir(parents=True)
        (self.repo / "config" / "initializers" / "cors.rb").write_text("x")
        (self.repo / ".env").write_text(f"PORT={self.port}\n")
        os.environ["FAKE_LSOF_PID"] = "4242"
        os.environ["FAKE_PS_LSTART"] = "Mon Jan  1 00:00:00 2024"
        info = self.detect(changed=["config/initializers/cors.rb", "app/models/x.rb"])
        self.assertEqual((info["source"], info["port"], info["state"]), ("dotenv", self.port, "stale"))
        self.assertIn("config/initializers/cors.rb changed", info["reason"])
        self.assertEqual(info["restart_sensitive_changed"], ["config/initializers/cors.rb"])
        os.environ["FAKE_PS_LSTART"] = time.strftime("%a %b %d %H:%M:%S %Y", time.localtime(time.time() + 60))
        self.assertEqual(self.detect(changed=["config/initializers/cors.rb"])["state"], "up")

    def test_nothing_listening_is_down_with_the_port_named(self):
        port = _free_port()
        (self.repo / ".env").write_text(f"PORT={port}\n")
        os.environ.pop("FAKE_LSOF_PID", None)
        info = self.detect()
        self.assertEqual(info["state"], "down")
        self.assertIn(f"no HTTP answer at http://127.0.0.1:{port}", info["reason"])
        self.assertIn(f"nothing listens on 127.0.0.1:{port} (lsof)", info["reason"])

    def test_without_docker_a_compose_file_degrades_to_the_local_path(self):
        self.compose()
        (self.bin / "docker").unlink()
        os.environ["PATH"] = str(self.bin)                     # only the shims: no real docker either
        (self.repo / ".env").write_text(f"PORT={self.port}\n")
        info = self.detect()
        self.assertEqual((info["source"], info["state"]), ("dotenv", "up"))
        self.assertIn("docker is not on PATH", info["reason"])

    def test_detect_cli_writes_the_json(self):
        out = self.tmp / "detect.json"
        with mock.patch("sys.stdout"):
            rc = smoke.main(["--repo-root", str(self.repo), "detect", "--base-url", self.base_url, "--out", str(out)])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out.read_text())["state"], "up")


if __name__ == "__main__":
    unittest.main()
