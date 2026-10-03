#!/usr/bin/env python3
"""tests/test_plan.py - how the planner decides whether a task touches the
frontend: the intake's `**Frontend:**` answer first, then the repo (an
app/javascript dir, an importmap, a Hotwire or ViewComponent gem). That
decision adds the views_frontend explorer, the frontend planning guidance and
the browser-ready testing-plan steps. The agent is replaced by a fake; skipped
where langgraph isn't installed (the tool imports it at module load).
Run: python3 -m unittest tests/test_plan.py"""

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

HAS_LANGGRAPH = importlib.util.find_spec("langgraph") is not None


def load_tool():
    # A fresh copy per test, so replacing its call_agent never leaks between tests.
    spec = importlib.util.spec_from_file_location("plan_under_test", ROOT / "migite" / "tools" / "plan.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


TEMPLATE_LINE = "**Frontend:** <!-- yes | no | unknown -->\n"


@unittest.skipUnless(HAS_LANGGRAPH, "langgraph not installed")
class IntakeFrontendTest(unittest.TestCase):
    def setUp(self):
        self.plan = load_tool()

    def test_untouched_template_line_is_no_answer(self):
        self.assertEqual(self.plan.intake_frontend(TEMPLATE_LINE), "")

    def test_yes_and_no_are_read_with_trailing_words_and_comments(self):
        self.assertEqual(self.plan.intake_frontend("**Frontend:** yes <!-- yes | no | unknown -->\n"), "yes")
        self.assertEqual(self.plan.intake_frontend("**Frontend:** No - API only\n"), "no")

    def test_unknown_or_missing_line_is_no_answer(self):
        self.assertEqual(self.plan.intake_frontend("**Frontend:** unknown\n"), "")
        self.assertEqual(self.plan.intake_frontend("# Task intake\n**Type:** feature\n"), "")


@unittest.skipUnless(HAS_LANGGRAPH, "langgraph not installed")
class FrontendDecisionTest(unittest.TestCase):
    def setUp(self):
        self.plan = load_tool()
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_app_javascript_dir_means_the_repo_has_a_frontend(self):
        (self.repo / "app" / "javascript").mkdir(parents=True)
        self.assertTrue(self.plan.repo_has_frontend(str(self.repo)))

    def test_hotwire_gem_in_the_gemfile_means_the_repo_has_a_frontend(self):
        (self.repo / "Gemfile").write_text('source "https://rubygems.org"\ngem "rails"\ngem "turbo-rails"\n')
        self.assertTrue(self.plan.repo_has_frontend(str(self.repo)))

    def test_mailer_views_alone_do_not_make_an_api_app_a_frontend(self):
        (self.repo / "app" / "views" / "user_mailer").mkdir(parents=True)
        (self.repo / "Gemfile").write_text('gem "rails"\n')
        self.assertFalse(self.plan.repo_has_frontend(str(self.repo)))

    def test_intake_no_skips_the_frontend_even_in_a_hotwire_repo(self):
        (self.repo / "app" / "javascript").mkdir(parents=True)
        self.assertEqual(self.plan.frontend_decision("**Frontend:** no\n", str(self.repo), "rails"),
                         (False, "intake says no"))

    def test_intake_yes_explores_the_frontend_even_when_nothing_is_detected(self):
        self.assertEqual(self.plan.frontend_decision("**Frontend:** yes\n", str(self.repo), "rails"),
                         (True, "intake says yes"))

    def test_no_answer_falls_back_to_the_repo(self):
        self.assertEqual(self.plan.frontend_decision(TEMPLATE_LINE, str(self.repo), "rails"),
                         (False, "no frontend detected"))
        (self.repo / "config").mkdir()
        (self.repo / "config" / "importmap.rb").write_text('pin "application"\n')
        self.assertEqual(self.plan.frontend_decision(TEMPLATE_LINE, str(self.repo), "rails"),
                         (True, "detected in repo"))

    def test_generic_stack_never_adds_the_frontend_explorer(self):
        self.assertFalse(self.plan.frontend_decision("**Frontend:** yes\n", str(self.repo), "generic")[0])


def base_state(**overrides):
    state = {"intake": "", "knowledge": "", "jira_context": "", "repo_root": "/nowhere",
             "base_branch": "main", "stack": "rails", "frontend": False, "frontend_source": "",
             "plan_final": "# Plan\n## Scope\n"}
    state.update(overrides)
    return state


@unittest.skipUnless(HAS_LANGGRAPH, "langgraph not installed")
class FrontendPlanningTest(unittest.TestCase):
    def setUp(self):
        self.plan = load_tool()
        self.prompts = []

        def fake_call_agent(prompt, role="think", thinking=False, label=""):
            self.prompts.append(prompt)
            return "# Testing Plan"
        self.plan.call_agent = fake_call_agent

    def areas(self, **state):
        return [send.arg["area"] for send in self.plan.route_to_explorers(base_state(**state))]

    def test_frontend_task_gets_an_eighth_views_frontend_explorer(self):
        self.assertEqual(len(self.areas()), 7)
        self.assertEqual(self.areas(frontend=True)[-1], "views_frontend")
        self.assertEqual(len(self.areas(frontend=True)), 8)

    def test_frontend_testing_plan_asks_for_browser_ready_steps(self):
        self.plan.generate_testing_plan(base_state(frontend=True))
        self.plan.generate_testing_plan(base_state(frontend=False))
        self.assertIn("without a full page reload", self.prompts[0])
        self.assertNotIn("without a full page reload", self.prompts[1])

    def test_backend_only_intake_is_told_to_raise_ui_needs_as_open_questions(self):
        block = self.plan.frontend_block(base_state(frontend_source="intake says no"))
        self.assertIn("Open questions", block)
        self.assertEqual(self.plan.frontend_block(base_state(frontend_source="no frontend detected")), "")
        self.assertIn("turbo_stream", self.plan.frontend_block(base_state(frontend=True, frontend_source="detected in repo")))

    def test_view_and_javascript_excerpts_are_fenced_in_their_own_language(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "app" / "views").mkdir(parents=True)
            (Path(tmp) / "app" / "views" / "index.html.erb").write_text("<%= render @items %>\n")
            (Path(tmp) / "app" / "javascript").mkdir(parents=True)
            (Path(tmp) / "app" / "javascript" / "app.js").write_text("import '@hotwired/turbo-rails'\n")
            area, patterns = self.plan.FRONTEND_EXPLORE_AREA
            context = self.plan.read_area_context(area, patterns, tmp, set(), "main")
        self.assertIn("```erb\n<%= render @items %>", context)
        self.assertIn("```javascript\nimport", context)


@unittest.skipUnless(HAS_LANGGRAPH, "langgraph not installed")
class CriticOutputGuardTest(unittest.TestCase):
    def setUp(self):
        self.plan = load_tool()
        self.responses = []
        self.prompts = []

        def fake_call_agent(prompt, role="think", thinking=False, label=""):
            self.prompts.append((label, prompt))
            return self.responses.pop(0)
        self.plan.call_agent = fake_call_agent

    def critic(self, **state):
        return self.plan.run_architecture_critic(
            base_state(critic_cmd="critique", plan_draft="# Plan\n## Scope\n", **state))

    def test_usable_clean_signal_passes_through_untouched(self):
        self.responses = ["✅ No architectural concerns found."]
        self.assertEqual(self.critic(), {"critic_findings": "✅ No architectural concerns found.",
                                         "critic_usable": True})

    def test_findings_checklist_is_usable(self):
        self.assertTrue(self.plan.critic_is_usable("🔴 **Critical** — n+1 in #index"))
        self.assertTrue(self.plan.critic_is_usable("🟡 **Warning** — missing index"))
        self.assertFalse(self.plan.critic_is_usable(""))
        self.assertFalse(self.plan.critic_is_usable("I checked the code and it looks fine."))

    def test_leaked_tool_call_markup_is_not_usable_and_is_stripped(self):
        leaked = '<invoke name="Bash">\n<parameter name="command">cat app/models/event.rb</parameter>\n</invoke>\n🟢 **Note** — fine'
        self.assertFalse(self.plan.critic_is_usable(leaked))
        self.assertEqual(self.plan.strip_tool_call_markup(
            '<invoke name="Bash"><parameter name="command">ls</parameter></invoke>'), "")
        self.assertEqual(self.plan.strip_tool_call_markup(leaked), "🟢 **Note** — fine")

    def test_unusable_reply_is_retried_once_and_the_retry_is_used(self):
        self.responses = [
            '<invoke name="Bash"><parameter name="command">grep -n foo</parameter></invoke>',
            "🟡 **Warning** — no index on events.client_id",
        ]
        out = self.critic()
        self.assertEqual([label for label, _ in self.prompts],
                         ["architecture_critic", "architecture_critic:retry"])
        self.assertTrue(out["critic_usable"])
        self.assertIn("no index on events.client_id", out["critic_findings"])
        self.assertNotIn("<invoke", out["critic_findings"])

    def test_two_unusable_replies_degrade_to_a_warning_placeholder(self):
        self.responses = ["", '<invoke name="Bash"><parameter name="command">ls</parameter></invoke>']
        out = self.critic()
        self.assertFalse(out["critic_usable"])
        self.assertIn("un-critiqued", out["critic_findings"])
        self.assertNotIn("<invoke", out["critic_findings"])

    def test_refine_skips_an_unusable_critique(self):
        out = self.plan.refine_plan(base_state(
            plan_draft="# Plan\n## Scope\n", critic_findings="🟡 **Warning** — un-critiqued",
            critic_usable=False))
        self.assertEqual(out["refine_status"], "critic_unusable")
        self.assertEqual(out["plan_final"], "# Plan\n## Scope\n")


if __name__ == "__main__":
    unittest.main()
