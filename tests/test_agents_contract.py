#!/usr/bin/env python3
"""tests/test_agents_contract.py - what every agent adapter must do, checked for
every agent in agents.AGENTS. A new adapter gets these checks by adding one row
to FAKES below (its fake CLI under tests/). An agent registered without a row
fails the suite. Run: python3 -m unittest tests/test_agents_contract.py"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from migite import agents          # noqa: E402
from migite import gateway     # noqa: E402

# agent name -> the fake CLI that stands in for it in tests
FAKES = {
    "claude": "fake-claude",
    "cursor": "fake-cursor-agent",
    "opencode": "fake-opencode",
}

PROMPT = "contract prompt 7f3a"


class ContractTest(unittest.TestCase):
    def each_agent(self):
        for name in agents.names():
            with self.subTest(agent=name):
                yield name, agents.get(name)

    def test_every_agent_has_a_fake_cli(self):
        for name in agents.names():
            self.assertIn(name, FAKES, f"agent {name!r} has no fake CLI row in FAKES")
            self.assertTrue((ROOT / "tests" / FAKES[name]).is_file(), FAKES[name])

    def test_info_is_complete(self):
        for name, agent in self.each_agent():
            i = agent.info
            self.assertEqual(i.name, name)
            self.assertTrue(i.display_name and i.default_binary and i.exit_hint and i.instruction_files)
            self.assertEqual(set(i.models), set(agents.TIERS))
            self.assertLessEqual(set(i.scopes), set(agents.SCOPES))
            self.assertIn(i.prompt_via, ("stdin", "arg"))
            self.assertGreater(i.max_arg_bytes, 0)
            json.dumps(agent.describe())                       # bash reads it as JSON

    def test_headless_prompt_travels_exactly_once(self):
        for _, agent in self.each_agent():
            launch = agent.ask_launch(agents.AskRequest(prompt=PROMPT))
            self.assertEqual(launch.argv[0], agent.binary)
            in_argv = sum(PROMPT in a for a in launch.argv)
            if agent.info.prompt_via == "stdin":
                self.assertEqual((launch.stdin, in_argv), (PROMPT, 0))
            else:
                self.assertEqual((launch.stdin, in_argv), (None, 1))

    def test_every_permission_word_builds_and_auto_differs_from_none(self):
        for _, agent in self.each_agent():
            built = {w: agent.ask_launch(agents.AskRequest(prompt=PROMPT, permission=w)).argv for w in agents.PERMISSIONS}
            self.assertNotEqual(built["auto"], built["none"])
            self.assertNotEqual(agent.session_launch(agents.SessionRequest(prompt=PROMPT, permission="auto")).argv,
                                agent.session_launch(agents.SessionRequest(prompt=PROMPT, permission="none")).argv)

    def test_model_is_passed_only_when_given(self):
        for _, agent in self.each_agent():
            self.assertNotIn("model-xyz", agent.ask_launch(agents.AskRequest(prompt=PROMPT)).argv)
            self.assertIn("model-xyz", agent.ask_launch(agents.AskRequest(prompt=PROMPT, model="model-xyz")).argv)

    def test_capabilities_match_what_reaches_the_command_line(self):
        schema = {"type": "object", "title": "contract-schema"}
        for _, agent in self.each_agent():
            argv = " ".join(agent.ask_launch(agents.AskRequest(prompt=PROMPT, schema=schema, effort="high",
                                                                model="model-xyz")).argv)
            self.assertEqual("contract-schema" in argv, agent.info.structured_output)
            self.assertEqual("high" in argv.split(), agent.info.effort)

    def test_scopes_map_to_tools(self):
        for _, agent in self.each_agent():
            for scope in agent.info.scopes:
                argv = " ".join(agent.ask_launch(agents.AskRequest(prompt=PROMPT, scopes=(scope,))).argv)
                for tool in agent.info.scopes[scope]:
                    self.assertIn(tool, argv)

    def test_session_carries_the_prompt_once(self):
        for _, agent in self.each_agent():
            launch = agent.session_launch(agents.SessionRequest(prompt=PROMPT))
            self.assertEqual(launch.argv[0], agent.binary)
            self.assertEqual(sum(PROMPT in a for a in launch.argv), 1)

    def test_plain_text_output_falls_back(self):
        for _, agent in self.each_agent():
            r = agent.parse("not the machine format\n", 0, agents.AskRequest(prompt=PROMPT, model="m"))
            self.assertFalse(r.is_envelope); self.assertTrue(r.ok); self.assertEqual(r.text, "not the machine format")
            self.assertFalse(agent.parse("boom", 1, agents.AskRequest(prompt=PROMPT)).ok)


class ContractEndToEndTest(unittest.TestCase):
    """One real round trip per agent, through the gateway, against its fake CLI."""

    def setUp(self):
        self._env = dict(os.environ)
        self.tmp = tempfile.TemporaryDirectory()
        self.bin = Path(self.tmp.name) / "bin"; self.bin.mkdir()
        os.environ["PATH"] = str(self.bin) + os.pathsep + os.environ["PATH"]
        os.environ.pop("MIGITE_USAGE_LEDGER", None)
        self.ledger = str(Path(self.tmp.name) / "usage.jsonl")

    def tearDown(self):
        os.environ.clear(); os.environ.update(self._env)
        gateway.reset(); self.tmp.cleanup()

    def test_round_trip(self):
        for name in agents.names():
            with self.subTest(agent=name):
                agent = agents.get(name)
                shim = self.bin / agent.binary
                shim.write_text(f'#!/usr/bin/env bash\nexec "{ROOT / "tests" / FAKES[name]}" "$@"\n'); shim.chmod(0o755)
                os.chmod(ROOT / "tests" / FAKES[name], 0o755)
                gateway.AGENT = agent
                gateway.require_cli()
                r = gateway.call_agent(PROMPT, "knowledge", label=f"contract:{name}", ledger=self.ledger)
                self.assertTrue(r.text)
                rec = gateway.read_ledger(self.ledger)[-1]
                self.assertEqual((rec["label"], rec["ok"]), (f"contract:{name}", True))


if __name__ == "__main__":
    unittest.main()
