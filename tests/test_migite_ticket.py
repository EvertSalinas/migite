#!/usr/bin/env python3
"""tests/test_migite_ticket.py - ticket references, the acli and agent ticket
sources, source selection, the tracker config, and the migite_ticket CLI. acli is
replaced by a fake runner or tests/fake-acli and the agent by a fake, so no test
reaches Jira or a model, even on a machine where a real acli is logged in.
Run: python3 -m unittest tests/test_migite_ticket.py"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import migite_config  # noqa: E402
import migite_paths   # noqa: E402
import migite_ticket  # noqa: E402
from trackers import InvalidTicketRef, Ticket, TicketError, TicketRef, parse_ref, render  # noqa: E402
from trackers.jira_acli import LOGIN_HINT, JiraAcliTracker  # noqa: E402
from trackers.jira_agent import FAILED_MARKER, JiraAgentTracker  # noqa: E402
from trackers.jira_format import FIELDS, acceptance_from, adf_to_text, field_text  # noqa: E402

SITE = "https://acme.atlassian.net"
FAKE_ACLI = str(ROOT / "tests" / "fake-acli")
NO_ACLI = "/nonexistent/acli"


def adf(*content):
    return {"type": "doc", "version": 1, "content": list(content)}


def para(*texts):
    return {"type": "paragraph", "content": [{"type": "text", "text": t} for t in texts]}


def heading(text, level=2):
    return {"type": "heading", "attrs": {"level": level}, "content": [{"type": "text", "text": text}]}


def bullets(*items):
    return {"type": "bulletList", "content": [{"type": "listItem", "content": [para(i)]} for i in items]}


ISSUE = {
    "key": "BB-12",
    "fields": {
        "summary": "Soft-delete announcements",
        "issuetype": {"name": "Story"}, "priority": {"name": "High"}, "status": {"name": "In Progress"},
        "labels": ["backend", "q3"],
        "description": adf(para("Admins need to undo deletes."), heading("Acceptance criteria"),
                           bullets("Deleted items hidden", "Restorable for 30 days"), heading("Notes"), para("n/a")),
    },
}
STATUS_OK = "✓ Authenticated\n  Site: acme.atlassian.net\n  Email: dev@acme.test\n  Authentication Type: oauth\n"


class FakeRunner:
    """Stands in for subprocess.run for acli: answers `jira auth status` and
    `jira workitem view`, records every argv."""

    def __init__(self, *, status=(0, STATUS_OK), view=None, view_rc=0, view_err="", raise_on_view=None):
        self.status = status
        self.view = json.dumps(ISSUE) if view is None else view
        self.view_rc, self.view_err, self.raise_on_view = view_rc, view_err, raise_on_view
        self.calls = []

    def __call__(self, argv, **kw):
        self.calls.append(argv)
        if argv[1:4] == ["jira", "auth", "status"]:
            rc, out = self.status
            return SimpleNamespace(returncode=rc, stdout=out, stderr="")
        if argv[1:4] == ["jira", "workitem", "view"]:
            if self.raise_on_view:
                raise self.raise_on_view
            return SimpleNamespace(returncode=self.view_rc, stdout=self.view, stderr=self.view_err)
        raise AssertionError(f"unexpected acli call {argv}")


def acli(runner=None, installed=True, **kw):
    return JiraAcliTracker(command="acli", runner=runner or FakeRunner(),
                           which=(lambda c: "/usr/bin/acli") if installed else (lambda c: None), **kw)


class ParseRefTest(unittest.TestCase):
    def test_keys_and_urls(self):
        self.assertEqual(parse_ref("bb-12"), TicketRef("BB-12"))
        self.assertEqual(parse_ref(" ABC2-7 ").key, "ABC2-7")                 # digits in the project key
        r = parse_ref(f"{SITE}/browse/bb-12?focusedCommentId=1")
        self.assertEqual((r.key, r.base_url), ("BB-12", SITE))
        self.assertEqual(parse_ref("https://jira.corp.example/browse/OPS-3").base_url, "https://jira.corp.example")

    def test_invalid_input_is_rejected(self):
        for bad in ("", "#", "BB_12", "12-BB", "../../etc", "BB-12/../x", "javascript:x/browse/BB-1",
                    "ftp://host/browse/BB-1", "https:///browse/BB-1", "--help"):
            with self.subTest(bad=bad), self.assertRaises(InvalidTicketRef):
                parse_ref(bad)

    def test_path_resolver_uses_the_same_parser(self):
        self.assertEqual(migite_paths.extract_ticket_key("abc2-7"), "ABC2-7")
        with self.assertRaises(migite_paths.InvalidRunKeyError):
            migite_paths.extract_ticket_key("BB_1")


class ConversionTest(unittest.TestCase):
    def test_adf_to_text(self):
        doc = adf(heading("Goal", 1), para("Line one", " continues"),
                  {"type": "paragraph", "content": [{"type": "text", "text": "a"}, {"type": "hardBreak"},
                                                    {"type": "mention", "attrs": {"text": "@sam"}}]},
                  bullets("first", "second"),
                  {"type": "orderedList", "content": [{"type": "listItem", "content": [para("one")]}]},
                  {"type": "codeBlock", "attrs": {"language": "ruby"}, "content": [{"type": "text", "text": "x = 1"}]},
                  {"type": "paragraph", "content": [{"type": "inlineCard", "attrs": {"url": "https://doc"}}]},
                  {"type": "table", "content": [{"type": "tableRow", "content": [
                      {"type": "tableCell", "content": [para("k")]}, {"type": "tableCell", "content": [para("v")]}]}]})
        text = adf_to_text(doc)
        for expected in ("# Goal", "Line one continues", "a\n@sam", "- first\n- second", "1. one",
                         "```ruby\nx = 1\n```", "https://doc", "| k | v |"):
            self.assertIn(expected, text)

    def test_field_text_handles_every_shape(self):
        self.assertEqual(field_text(None), "")
        self.assertEqual(field_text("h2. Title\nbody"), "## Title\nbody")      # wiki markup
        self.assertEqual(field_text({"value": "Yes"}), "Yes")                  # select-list custom field
        self.assertIn("hello", field_text(adf(para("hello"))))

    def test_acceptance_from_description(self):
        self.assertEqual(acceptance_from("intro\n## Acceptance criteria\n- a\n- b\n## Notes\nx"), "- a\n- b")
        self.assertEqual(acceptance_from("h3. Acceptance Criteria\n* a\nh3. Other\n* b"), "* a")
        self.assertEqual(acceptance_from("**Definition of done:**\n- shipped"), "- shipped")
        self.assertEqual(acceptance_from("no such section"), "")

    def test_render_is_the_shape_the_planner_reads(self):
        text = render(Ticket(key="BB-1", title="T", description="D", url="u"))
        self.assertTrue(text.startswith("## Jira ticket: BB-1\n**Title:** T"))
        self.assertIn("**Acceptance criteria:**\nNone specified in the ticket", text)


class JiraAcliTest(unittest.TestCase):
    def test_fetch_success(self):
        runner = FakeRunner()
        text = acli(runner).fetch(parse_ref("BB-12"))
        self.assertEqual(runner.calls[-1], ["acli", "jira", "workitem", "view", "BB-12", "--fields", ",".join(FIELDS), "--json"])
        for expected in ("## Jira ticket: BB-12", "**Title:** Soft-delete announcements", "**Type:** Story",
                         "**Priority:** High", "**Status:** In Progress", "**Labels:** backend, q3",
                         f"**Link:** {SITE}/browse/BB-12", "Admins need to undo deletes."):
            self.assertIn(expected, text)
        self.assertIn("**Acceptance criteria:**\n- Deleted items hidden\n- Restorable for 30 days", text)
        self.assertNotIn("dev@acme.test", text)                   # the account email never leaks into output

    def test_the_url_you_passed_is_the_link(self):
        text = acli().fetch(parse_ref("https://other.atlassian.net/browse/BB-12"))
        self.assertIn("**Link:** https://other.atlassian.net/browse/BB-12", text)

    def test_configured_acceptance_field_is_requested_and_wins(self):
        issue = json.loads(json.dumps(ISSUE))
        issue["fields"]["customfield_10035"] = adf(bullets("from the field"))
        runner = FakeRunner(view=json.dumps(issue))
        text = acli(runner, acceptance_field="customfield_10035").fetch(parse_ref("BB-12"))
        self.assertTrue(runner.calls[-1][6].endswith(",customfield_10035"))
        self.assertIn("**Acceptance criteria:**\n- from the field", text)

    def test_not_installed(self):
        runner = FakeRunner()
        t = acli(runner, installed=False)
        ok, why = t.available()
        self.assertFalse(ok); self.assertIn("not installed", why)
        with self.assertRaises(TicketError):
            t.fetch(parse_ref("BB-12"))
        self.assertEqual(runner.calls, [])                        # nothing was started

    def test_logged_out_is_unauthorized_with_the_login_hint(self):
        runner = FakeRunner(status=(1, "✗ Error: not authenticated"))
        t = acli(runner)
        ok, why = t.available()
        self.assertFalse(ok); self.assertIn(LOGIN_HINT, why)
        with self.assertRaises(TicketError) as cm:
            t.fetch(parse_ref("BB-12"))
        self.assertIn("acli jira auth login --web", str(cm.exception))
        self.assertEqual([c[1:4] for c in runner.calls], [["jira", "auth", "status"]])   # no view attempted

    def test_login_status_is_asked_once(self):
        runner = FakeRunner()
        t = acli(runner)
        t.available(); t.fetch(parse_ref("BB-12")); t.fetch(parse_ref("BB-13"))
        self.assertEqual(sum(c[1:4] == ["jira", "auth", "status"] for c in runner.calls), 1)

    def test_failures(self):
        cases = (
            (FakeRunner(view="", view_rc=1, view_err="✗ Error: Issue does not exist or you do not have permission to see it."),
             "Issue does not exist or you do not have permission to see it."),
            (FakeRunner(view="", view_rc=1, view_err=""), "acli exited 1"),
            (FakeRunner(view="not json"), "did not return JSON"),
            (FakeRunner(view=json.dumps({"errorMessages": []})), "no fields"),
            (FakeRunner(raise_on_view=subprocess.TimeoutExpired("acli", 30)), "timed out"),
        )
        for runner, words in cases:
            with self.subTest(words=words), self.assertRaises(TicketError) as cm:
                acli(runner).fetch(parse_ref("BB-12"))
            self.assertIn(words, str(cm.exception))

    def test_an_unvalidated_key_never_reaches_a_process(self):
        runner = FakeRunner()
        for bad in ("BB-1; rm -rf ~", "--help", "BB-1/../x"):
            with self.subTest(bad=bad), self.assertRaises(TicketError):
                acli(runner).fetch(TicketRef(key=bad))
        self.assertEqual(runner.calls, [])


class FakeAgent:
    class info:
        display_name = "Fake Agent"


class FakeGateway:
    """Stands in for migite_call: records calls, returns text or raises."""

    class AgentError(RuntimeError):
        pass

    def __init__(self, text="## Jira ticket: BB-12\n**Title:** via agent", scoped=True, error=None):
        self.AGENT = FakeAgent()
        self.text, self.scoped, self.error, self.calls = text, scoped, error, []

    def supports(self, cap):
        return self.scoped and cap == "scope:jira.read"

    def call_agent(self, prompt, role, **kw):
        self.calls.append((prompt, role, kw))
        if self.error:
            raise self.AgentError(self.error)
        return SimpleNamespace(text=self.text)


class JiraAgentTest(unittest.TestCase):
    def test_success_uses_the_jira_role_and_scope(self):
        gw = FakeGateway()
        text = JiraAgentTracker(gw).fetch(parse_ref(f"{SITE}/browse/BB-12"))
        self.assertIn("via agent", text)
        prompt, role, kw = gw.calls[0]
        self.assertEqual(role, "jira")
        self.assertEqual((kw["scopes"], kw["permission"], kw["label"]), (("jira.read",), "auto", "jira-fetch"))
        self.assertIn(f"{SITE}/browse/BB-12", prompt)

    def test_unavailable_without_the_scope(self):
        ok, why = JiraAgentTracker(FakeGateway(scoped=False)).available()
        self.assertFalse(ok); self.assertIn("jira.read", why)
        with self.assertRaises(TicketError):
            JiraAgentTracker(FakeGateway(scoped=False)).fetch(parse_ref("BB-1"))
        self.assertFalse(JiraAgentTracker(None).available()[0])

    def test_failures(self):
        with self.assertRaises(TicketError) as cm:
            JiraAgentTracker(FakeGateway(text=f"{FAILED_MARKER}: not authenticated")).fetch(parse_ref("BB-1"))
        self.assertEqual(str(cm.exception), "not authenticated")
        for gw in (FakeGateway(text="  "), FakeGateway(error="timed out")):
            with self.assertRaises(TicketError):
                JiraAgentTracker(gw).fetch(parse_ref("BB-1"))


class _Isolated(unittest.TestCase):
    def setUp(self):
        self._env = dict(os.environ)
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "repo"; self.repo.mkdir()
        home = Path(self.tmp.name) / "home"; (home / ".config" / "migite").mkdir(parents=True)
        os.environ["HOME"] = str(home); os.environ["XDG_CONFIG_HOME"] = str(home / ".config")
        for var in list(migite_config.ENV_OVERRIDES) + ["MIGITE_CONFIG"]:
            os.environ.pop(var, None)
        os.environ["MIGITE_ACLI"] = NO_ACLI          # never the real, logged-in acli
        migite_config._CACHE.clear()

    def tearDown(self):
        os.environ.clear(); os.environ.update(self._env)
        migite_config._CACHE.clear(); self.tmp.cleanup()

    def cfg(self, data=None, **env):
        if data is not None:
            (self.repo / ".migite.json").write_text(json.dumps(data))
        return migite_config.load(self.repo, env={**os.environ, **env}, use_cache=False)


def installed(_):
    return "/usr/bin/acli"


class SelectionTest(_Isolated):
    def test_auto_prefers_acli_when_logged_in(self):
        gw = FakeGateway()
        text, used = migite_ticket.fetch(parse_ref("BB-12"), self.cfg(), runner=FakeRunner(), which=installed, gateway=gw)
        self.assertEqual(used, "jira-acli"); self.assertIn("Soft-delete", text)
        self.assertEqual(gw.calls, [])                               # no model call

    def test_auto_falls_back_to_the_agent_when_acli_fails(self):
        gw = FakeGateway()
        runner = FakeRunner(view="", view_rc=1, view_err="✗ Error: boom")
        with unittest.mock.patch("sys.stderr") as err:
            text, used = migite_ticket.fetch(parse_ref("BB-12"), self.cfg(), runner=runner, which=installed, gateway=gw)
        self.assertEqual(used, "jira-agent"); self.assertIn("via agent", text)
        self.assertIn("jira-acli", "".join(str(c) for c in err.write.call_args_list))   # announced, not silent

    def test_auto_without_acli_uses_the_agent(self):
        _, used = migite_ticket.fetch(parse_ref("BB-12"), self.cfg(), which=lambda c: None, gateway=FakeGateway())
        self.assertEqual(used, "jira-agent")

    def test_no_source_lists_every_reason(self):
        with self.assertRaises(migite_ticket.NoSource) as cm:
            migite_ticket.fetch(parse_ref("BB-12"), self.cfg(), which=lambda c: None, gateway=FakeGateway(scoped=False))
        self.assertEqual([name for name, _ in cm.exception.reasons], ["jira-acli", "jira-agent"])
        with self.assertRaises(migite_ticket.NoSource):
            migite_ticket.fetch(parse_ref("BB-12"), self.cfg({"tracker": {"provider": "none"}}),
                                runner=FakeRunner(), which=installed)

    def test_explicit_provider_and_source_override(self):
        cfg = self.cfg({"tracker": {"provider": "jira-agent"}})
        _, used = migite_ticket.fetch(parse_ref("BB-12"), cfg, runner=FakeRunner(), which=installed, gateway=FakeGateway())
        self.assertEqual(used, "jira-agent")
        _, used = migite_ticket.fetch(parse_ref("BB-12"), cfg, source="jira-acli", runner=FakeRunner(), which=installed)
        self.assertEqual(used, "jira-acli")

    def test_the_configured_acli_command_is_used(self):
        os.environ.pop("MIGITE_ACLI", None)          # env beats files; let the file value apply
        runner = FakeRunner()
        migite_ticket.fetch(parse_ref("BB-12"), self.cfg({"tracker": {"jira": {"acli": "/opt/acli/bin/acli"}}}),
                            runner=runner, which=installed)
        self.assertTrue(all(c[0] == "/opt/acli/bin/acli" for c in runner.calls))

    def test_every_available_source_failing_raises_the_last_error(self):
        with unittest.mock.patch("sys.stderr"), self.assertRaises(TicketError):
            migite_ticket.fetch(parse_ref("BB-12"), self.cfg({"tracker": {"provider": "jira-agent"}}),
                                gateway=FakeGateway(error="boom"))


class TrackerConfigTest(_Isolated):
    def test_defaults_and_env_overrides(self):
        os.environ.pop("MIGITE_ACLI", None)
        cfg = self.cfg()
        self.assertEqual((cfg.get("tracker.provider"), cfg.get("tracker.jira.acli")), ("auto", "acli"))
        cfg = self.cfg(MIGITE_TRACKER="jira-acli", MIGITE_ACLI="/opt/acli")
        self.assertEqual((cfg.get("tracker.provider"), cfg.get("tracker.jira.acli")), ("jira-acli", "/opt/acli"))

    def test_invalid_provider_is_an_error(self):
        for bad in ("linear", "jira-rest"):
            with self.subTest(bad=bad), self.assertRaises(migite_config.ConfigError):
                self.cfg({"tracker": {"provider": bad}})

    def test_a_token_in_a_config_file_is_refused(self):
        for key in ("api_token", "token"):
            with self.subTest(key=key), self.assertRaises(migite_config.ConfigError) as cm:
                self.cfg({"tracker": {"jira": {key: "s3cret"}}})
            self.assertIn("acli jira auth login --web", str(cm.exception))
            self.assertNotIn("s3cret", str(cm.exception))

    def test_provider_enum_matches_the_ticket_module(self):
        self.assertEqual(migite_config.TRACKER_PROVIDERS, migite_ticket.PROVIDERS)


class CliTest(_Isolated):
    """The entry point bash and `migite-ticket` use: success, unauthorized, unavailable, invalid input."""

    def run_cli(self, *args, env=None):
        full_env = {**os.environ, **(env or {})}
        return subprocess.run([sys.executable, str(ROOT / "migite_ticket.py"), "--repo-root", str(self.repo), *args],
                              capture_output=True, text=True, env=full_env, timeout=60)

    def fake_claude_on_path(self):
        bin_dir = Path(self.tmp.name) / "bin"; bin_dir.mkdir(exist_ok=True)
        shim = bin_dir / "claude"
        shim.write_text(f'#!/usr/bin/env bash\nexec "{ROOT / "tests" / "fake-claude"}" "$@"\n'); shim.chmod(0o755)
        os.chmod(ROOT / "tests" / "fake-claude", 0o755)
        return {"PATH": str(bin_dir) + os.pathsep + os.environ["PATH"], "FAKE_CLAUDE_MODE": "envelope"}

    def setUp(self):
        super().setUp()
        os.chmod(FAKE_ACLI, 0o755)

    def test_parse(self):
        r = self.run_cli("parse", f"{SITE}/browse/bb-3")
        self.assertEqual(r.returncode, 0)
        self.assertEqual(json.loads(r.stdout), {"key": "BB-3", "url": f"{SITE}/browse/bb-3", "base_url": SITE})
        self.assertIn("TICKET_KEY=BB-3", self.run_cli("parse", "BB-3", "--shell").stdout)
        self.assertEqual(self.run_cli("parse", "not a ticket").returncode, 1)

    def test_fetch_success_via_acli_writes_the_file(self):
        out = Path(self.tmp.name) / "ticket.md"
        r = self.run_cli("fetch", "BB-3", "--out", str(out), env={"MIGITE_ACLI": FAKE_ACLI})
        self.assertEqual(r.returncode, 0, r.stderr)
        text = out.read_text()
        self.assertTrue(text.startswith("## Jira ticket: BB-3"))
        self.assertIn(f"**Link:** {SITE}/browse/BB-3", text)
        self.assertIn("via jira-acli", r.stderr)

    def test_fetch_falls_back_to_the_agent(self):
        out = Path(self.tmp.name) / "ticket.md"
        r = self.run_cli("fetch", "BB-3", "--out", str(out), env=self.fake_claude_on_path())
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("via jira-agent", r.stderr)

    def test_logged_out_acli_is_unauthorized(self):
        env = {"MIGITE_ACLI": FAKE_ACLI, "FAKE_ACLI_MODE": "logged_out", "MIGITE_TRACKER": "jira-acli"}
        r = self.run_cli("fetch", "BB-3", env=env)
        self.assertEqual(r.returncode, 2)
        self.assertIn("acli jira auth login --web", r.stderr)

    def test_fetch_with_no_source_exits_2_and_writes_nothing(self):
        out = Path(self.tmp.name) / "ticket.md"
        r = self.run_cli("fetch", "BB-3", "--out", str(out), env={"MIGITE_AGENT": "cursor"})
        self.assertEqual(r.returncode, 2)
        self.assertIn("acli is not installed", r.stderr)
        self.assertFalse(out.exists())

    def test_fetch_failure_exits_1_and_writes_nothing(self):
        out = Path(self.tmp.name) / "ticket.md"
        env = {"MIGITE_ACLI": FAKE_ACLI, "FAKE_ACLI_MODE": "missing", "MIGITE_TRACKER": "jira-acli"}
        r = self.run_cli("fetch", "BB-3", "--out", str(out), env=env)
        self.assertEqual(r.returncode, 1)
        self.assertIn("Issue does not exist", r.stderr)
        self.assertFalse(out.exists())

    def test_fetch_invalid_ref(self):
        self.assertEqual(self.run_cli("fetch", "../../etc").returncode, 1)

    def test_sources(self):
        r = self.run_cli("sources", env={"MIGITE_ACLI": FAKE_ACLI})
        self.assertEqual(r.returncode, 0)
        self.assertIn("✔ jira-acli", r.stdout)
        self.assertIn("acli logged in to acme.atlassian.net", r.stdout)
        self.assertIn("→ jira-acli would be used", r.stdout)
        self.assertNotIn("dev@acme.test", r.stdout + r.stderr)      # never prints the account email

    def test_config_error_is_reported(self):
        (self.repo / ".migite.json").write_text(json.dumps({"tracker": {"provider": "linear"}}))
        r = self.run_cli("sources")
        self.assertEqual(r.returncode, 1)
        self.assertIn("config error", r.stderr)


import unittest.mock  # noqa: E402  (used by SelectionTest)

if __name__ == "__main__":
    unittest.main()
