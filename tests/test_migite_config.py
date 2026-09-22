#!/usr/bin/env python3
"""tests/test_migite_config.py — layered configuration: precedence, env overrides,
validation, model-role resolution, prompt overrides, shell export, CLI.
Run: python3 -m unittest tests/test_migite_config.py"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import migite_config  # noqa: E402

try:
    import yaml  # noqa: F401
    HAVE_YAML = True
except ImportError:
    HAVE_YAML = False


class _Isolated(unittest.TestCase):
    """Each test gets an empty HOME/XDG so the developer's real config never leaks in."""

    def setUp(self):
        self._env = dict(os.environ)
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "home"
        self.repo = Path(self.tmp.name) / "repo"
        (self.home / ".config" / "migite").mkdir(parents=True)
        self.repo.mkdir()
        for var in list(migite_config.ENV_OVERRIDES) + ["MIGITE_CONFIG", "XDG_CONFIG_HOME"]:
            os.environ.pop(var, None)
        os.environ["HOME"] = str(self.home)
        os.environ["XDG_CONFIG_HOME"] = str(self.home / ".config")
        migite_config._CACHE.clear()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        migite_config._CACHE.clear()
        self.tmp.cleanup()

    def write_repo(self, data: dict, name=".migite.json"):
        (self.repo / name).write_text(json.dumps(data))

    def write_user(self, data: dict, name="config.json"):
        (self.home / ".config" / "migite" / name).write_text(json.dumps(data))

    def load(self):
        return migite_config.load(self.repo, use_cache=False)


class DefaultsTest(_Isolated):
    def test_no_files_gives_defaults_equal_to_pre_config_behaviour(self):
        cfg = self.load()
        self.assertEqual(cfg.files, [])
        self.assertEqual(cfg.get("vault.base"), "~/dev-log")
        self.assertEqual(cfg.get("heal.max_attempts"), 3)
        self.assertEqual(cfg.get("gates.commit.policy"), "lenient")
        self.assertEqual(cfg.get("permissions.interactive"), "bypassPermissions")
        self.assertEqual(cfg.get("permissions.headless"), "none")
        self.assertTrue(cfg.get("heal.full_suite_fallback"))
        self.assertEqual(cfg.source("models.strong"), "defaults")

    def test_model_roles_resolve_through_tiers(self):
        cfg = self.load()
        self.assertEqual(cfg.model("explore"), "claude-haiku-4-5-20251001")
        self.assertEqual(cfg.model("think"), "claude-sonnet-5")
        self.assertEqual(cfg.model("critic"), "claude-opus-5")
        self.assertEqual(cfg.model("verdict"), "claude-opus-5")
        self.assertEqual(cfg.model("knowledge"), "claude-sonnet-5")
        with self.assertRaises(KeyError):
            cfg.model("nonexistent_role")

    def test_every_role_has_a_tier(self):
        self.assertTrue(set(migite_config.ROLE_TIERS.values()) <= {"fast", "standard", "strong"})


class PrecedenceTest(_Isolated):
    def test_repo_file_beats_user_file_beats_defaults(self):
        self.write_user({"models": {"strong": "user-strong", "fast": "user-fast"}, "heal": {"max_attempts": 5}})
        self.write_repo({"models": {"strong": "repo-strong"}})
        cfg = self.load()
        self.assertEqual(cfg.get("models.strong"), "repo-strong")
        self.assertEqual(cfg.get("models.fast"), "user-fast")      # user layer survives where repo is silent
        self.assertEqual(cfg.get("heal.max_attempts"), 5)
        self.assertEqual(cfg.get("models.standard"), "claude-sonnet-5")  # default survives
        self.assertTrue(cfg.source("models.strong").endswith(".migite.json"))
        self.assertTrue(cfg.source("models.fast").endswith("config.json"))
        self.assertEqual(len(cfg.files), 2)

    def test_env_beats_files(self):
        self.write_repo({"vault": {"base": "/from/file"}, "heal": {"max_attempts": 9}})
        os.environ["DEV_LOG_BASE"] = "/from/env"
        os.environ["MAX_HEAL_ATTEMPTS"] = "1"
        cfg = self.load()
        self.assertEqual(cfg.get("vault.base"), "/from/env")
        self.assertEqual(cfg.get("heal.max_attempts"), 1)          # coerced to int
        self.assertEqual(cfg.source("vault.base"), "env:DEV_LOG_BASE")

    def test_empty_env_var_does_not_override(self):
        self.write_repo({"vault": {"base": "/from/file"}})
        os.environ["DEV_LOG_BASE"] = ""
        self.assertEqual(self.load().get("vault.base"), "/from/file")

    def test_migite_config_env_points_at_an_explicit_file_with_top_precedence(self):
        self.write_repo({"models": {"strong": "repo"}})
        explicit = Path(self.tmp.name) / "explicit.json"
        explicit.write_text(json.dumps({"models": {"strong": "explicit"}}))
        os.environ["MIGITE_CONFIG"] = str(explicit)
        self.assertEqual(self.load().get("models.strong"), "explicit")

    def test_migite_config_pointing_nowhere_is_an_error(self):
        os.environ["MIGITE_CONFIG"] = str(Path(self.tmp.name) / "missing.yml")
        with self.assertRaises(migite_config.ConfigError):
            self.load()

    def test_role_pin_beats_tier(self):
        self.write_repo({"models": {"strong": "tier-strong", "roles": {"critic": "pinned-critic"}}})
        cfg = self.load()
        self.assertEqual(cfg.model("critic"), "pinned-critic")
        self.assertEqual(cfg.model("verdict"), "tier-strong")


class ValidationTest(_Isolated):
    def test_unknown_key_is_a_warning_not_an_error(self):
        self.write_repo({"stacks": {"node": {}}, "models": {"strong": "x"}})
        cfg = self.load()
        self.assertEqual(len(cfg.warnings), 1)
        self.assertIn("stacks.node", cfg.warnings[0])
        self.assertEqual(cfg.get("models.strong"), "x")

    def test_invalid_enum_is_an_error(self):
        self.write_repo({"gates": {"commit": {"policy": "yolo"}}})
        with self.assertRaises(migite_config.ConfigError) as cm:
            self.load()
        self.assertIn("gates.commit.policy", str(cm.exception))

    def test_bad_int_is_an_error(self):
        self.write_repo({"heal": {"max_attempts": "lots"}})
        with self.assertRaises(migite_config.ConfigError):
            self.load()

    def test_bool_coercion_from_env_strings(self):
        self.write_repo({"heal": {"full_suite_fallback": "no"}})
        self.assertFalse(self.load().get("heal.full_suite_fallback"))

    def test_unknown_model_role_is_an_error(self):
        self.write_repo({"models": {"roles": {"planner": "x"}}})
        with self.assertRaises(migite_config.ConfigError) as cm:
            self.load()
        self.assertIn("planner", str(cm.exception))

    def test_invalid_json_is_an_error(self):
        (self.repo / ".migite.json").write_text("{not json")
        with self.assertRaises(migite_config.ConfigError):
            self.load()

    def test_top_level_must_be_a_mapping(self):
        (self.repo / ".migite.json").write_text("[1, 2]")
        with self.assertRaises(migite_config.ConfigError):
            self.load()

    @unittest.skipUnless(HAVE_YAML, "PyYAML not installed")
    def test_yaml_file_and_starter_template_parse(self):
        (self.repo / ".migite.yml").write_text("models:\n  strong: from-yaml\ngates:\n  commit:\n    policy: strict\n")
        cfg = self.load()
        self.assertEqual(cfg.get("models.strong"), "from-yaml")
        self.assertEqual(cfg.get("gates.commit.policy"), "strict")
        # The starter template must itself be valid and equal to the defaults
        (self.repo / ".migite.yml").write_text(migite_config.STARTER_TEMPLATE)
        cfg = self.load()
        self.assertEqual(cfg.warnings, [])
        for key, value in migite_config._flatten(migite_config.DEFAULTS).items():
            if key == "models.roles":
                continue
            self.assertEqual(cfg.get(key), value, f"starter template differs from default for {key}")


class PathsTest(_Isolated):
    def test_prompt_override_dir_relative_to_repo(self):
        (self.repo / "overrides").mkdir()
        (self.repo / "overrides" / "plan.md").write_text("custom")
        self.write_repo({"prompts": {"dir": "overrides"}})
        cfg = self.load()
        self.assertEqual(cfg.prompt_path("plan", ROOT, self.repo), self.repo / "overrides" / "plan.md")
        # Not overridden → repo copy
        self.assertEqual(cfg.prompt_path("review", ROOT, self.repo), ROOT / "prompts" / "review.md")
        self.assertEqual(cfg.template_path("commit", ROOT, self.repo), ROOT / "templates" / "commit.md")

    def test_expanded_path(self):
        cfg = self.load()
        self.assertEqual(cfg.expanded_path("vault.base"), self.home / "dev-log")
        self.assertIsNone(cfg.expanded_path("prompts.dir"))


class ShellExportTest(_Isolated):
    def test_to_shell_quotes_and_flattens(self):
        self.write_repo({"vault": {"base": "/tmp/my vault", "org": "Acme"}, "models": {"roles": {"critic": "pinned"}},
                         "budget": {"max_usd_per_run": 2.5}})
        out = migite_config.to_shell(self.load())
        self.assertIn("MIGITE_CFG_VAULT_BASE='/tmp/my vault'", out)
        self.assertIn("MIGITE_CFG_VAULT_ORG=Acme", out)
        self.assertIn("MIGITE_CFG_GATES_COMMIT_POLICY=lenient", out)
        self.assertIn("MIGITE_CFG_HEAL_FULL_SUITE_FALLBACK=true", out)
        self.assertIn("MIGITE_CFG_BUDGET_MAX_USD_PER_RUN=2.5", out)
        self.assertIn("MIGITE_CFG_MODEL_CRITIC=pinned", out)
        self.assertIn("MIGITE_CFG_MODEL_VERDICT=claude-opus-5", out)
        self.assertIn("MIGITE_CFG_PROMPTS_DIR=''", out)      # None → empty string
        self.assertNotIn("MIGITE_CFG_MODELS_ROLES=", out)     # roles dict is exported per role, not raw
        # Every line must be a valid shell assignment
        rc = subprocess.run(["bash", "-euc", out + "\n:"], capture_output=True, text=True)
        self.assertEqual(rc.returncode, 0, rc.stderr)


class CliTest(_Isolated):
    def run_cli(self, *args):
        return subprocess.run([sys.executable, str(ROOT / "migite_config.py"), "--repo-root", str(self.repo), *args],
                              capture_output=True, text=True, env=os.environ)

    def test_env_get_show_validate(self):
        self.write_repo({"models": {"strong": "cli-strong"}, "bogus": 1})
        env = self.run_cli("env")
        self.assertEqual(env.returncode, 0)
        self.assertIn("MIGITE_CFG_MODELS_STRONG=cli-strong", env.stdout)
        self.assertIn("unknown key", env.stdout)   # carried in MIGITE_CFG_WARNINGS (shell-quoted)
        self.assertIn("bogus", env.stdout)
        # --repo-root is accepted after the subcommand too (how config.sh calls it)
        alt = subprocess.run([sys.executable, str(ROOT / "migite_config.py"), "get", "--repo-root", str(self.repo), "models.strong"],
                             capture_output=True, text=True, env=os.environ)
        self.assertEqual(alt.stdout.strip(), "cli-strong")
        self.assertEqual(self.run_cli("get", "models.strong").stdout.strip(), "cli-strong")
        self.assertEqual(self.run_cli("get", "model:critic").stdout.strip(), "cli-strong")
        self.assertEqual(self.run_cli("get", "prompts.dir").returncode, 1)   # unset → exit 1
        show = self.run_cli("show")
        self.assertIn("cli-strong", show.stdout)
        self.assertIn(".migite.json", show.stdout)
        self.assertIn("⚠", show.stdout)
        val = self.run_cli("validate")
        self.assertEqual(val.returncode, 0)
        self.assertIn("unknown key", val.stdout)

    def test_env_on_error_emits_a_failing_shell_line(self):
        self.write_repo({"ui": {"tmux": "sometimes"}})
        env = self.run_cli("env")
        self.assertEqual(env.returncode, 1)
        self.assertIn("false", env.stdout)
        rc = subprocess.run(["bash", "-c", env.stdout], capture_output=True, text=True)
        self.assertNotEqual(rc.returncode, 0)
        self.assertIn("ui.tmux", rc.stderr)

    def test_init_writes_starter_and_refuses_to_overwrite(self):
        r = self.run_cli("init")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((self.repo / ".migite.yml").is_file())
        self.assertIn("gates:", (self.repo / ".migite.yml").read_text())
        self.assertEqual(self.run_cli("init").returncode, 1)
        self.assertEqual(self.run_cli("init", "--force").returncode, 0)


if __name__ == "__main__":
    unittest.main()
