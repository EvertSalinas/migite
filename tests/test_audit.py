#!/usr/bin/env python3
"""tests/test_audit.py - migite-audit's areas and checks come from the stack's checklist
(prompts/checklists/<stack>.md): rails by default, generic for a stack without a file of
its own, so a non-Rails repo is never audited for Rails layers. The agent is replaced by a
fake; skipped where langgraph isn't installed (the tool imports it at module load).
Run: python3 -m unittest tests/test_audit.py"""

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

HAS_LANGGRAPH = importlib.util.find_spec("langgraph") is not None


def load_tool():
    spec = importlib.util.spec_from_file_location("audit_under_test", ROOT / "migite" / "tools" / "audit.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@unittest.skipUnless(HAS_LANGGRAPH, "langgraph not installed")
class StackChecklistTest(unittest.TestCase):
    def setUp(self):
        self.tool = load_tool()
        from migite import checklists
        self.generic = [(a.name, a.globs, a.checks) for a in checklists.load("generic", ROOT).audit]
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def state(self, **overrides):
        s = {"repo_root": str(self.repo), "repo_name": "r", "focus": "", "output": "", "findings": [], "report": ""}
        s.update(overrides)
        return s

    def test_rails_areas_are_the_default(self):
        names = [s.arg["area"] for s in self.tool.route_to_auditors(self.state())]
        self.assertEqual(names, ["models", "controllers", "services", "serializers", "jobs", "migrations",
                                 "schema_indexes"])

    def test_a_profile_is_audited_with_the_generic_areas_and_expertise(self):
        sends = self.tool.route_to_auditors(self.state(stack="node", areas=self.generic, expertise="software"))
        self.assertEqual([s.arg["area"] for s in sends], [a for a, _, _ in self.generic])
        self.assertEqual({s.arg["stack"] for s in sends}, {"node"})
        prompts = []
        self.tool.call_agent = lambda prompt, label="", role="": prompts.append(prompt) or "✅ No issues."
        self.tool.audit_area(sends[0].arg)
        self.assertIn("You are a senior software architect", prompts[0])
        self.assertNotIn("Pundit", prompts[0])

    def test_focus_still_filters_by_area_name(self):
        sends = self.tool.route_to_auditors(self.state(focus="error", areas=self.generic))
        self.assertEqual([s.arg["area"] for s in sends], ["error_handling"])

    def test_broad_globs_skip_vendored_code_and_fence_by_language(self):
        (self.repo / "src").mkdir()
        (self.repo / "node_modules" / "dep").mkdir(parents=True)
        (self.repo / "src" / "app.ts").write_text("export const a = 1\n")
        (self.repo / "node_modules" / "dep" / "index.ts").write_text("vendored\n")
        out = self.tool.read_area_files(["**/*.ts"], str(self.repo), stack="node")
        self.assertIn("src/app.ts", out)
        self.assertIn("```typescript", out)
        self.assertNotIn("node_modules", out)

    def test_rails_reads_its_files_exactly_as_before(self):
        (self.repo / "app" / "models").mkdir(parents=True)
        (self.repo / "app" / "models" / "item.rb").write_text("class Item; end\n")
        out = self.tool.read_area_files(["app/models/**/*.rb"], str(self.repo))
        self.assertIn("### app/models/item.rb\n```ruby\nclass Item; end", out)


if __name__ == "__main__":
    unittest.main()
