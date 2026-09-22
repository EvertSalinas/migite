#!/usr/bin/env python3
"""migite_config — layered configuration for migite and its standalone tools.

Precedence (highest first):
  1. command-line flags                (handled by each tool, not here)
  2. environment variables             (ENV_OVERRIDES below; keeps every pre-config env var working)
  3. $MIGITE_CONFIG, if set            (an explicit file, any location)
  4. <repo root>/.migite.yml           (or .migite.yaml / .migite.json)
  5. $XDG_CONFIG_HOME/migite/config.yml  (default ~/.config/migite/config.yml; .yaml/.json also accepted)
  6. built-in DEFAULTS                 (equal to migite's behaviour before config existed)

YAML needs PyYAML (`pip install pyyaml`). It is only required when a YAML config
file actually exists — with no config files, or JSON ones, migite runs without it.

Python API:
    cfg = migite_config.load(repo_root)
    cfg.get("gates.commit.policy")     -> "lenient"
    cfg.model("critic")                -> "claude-opus-5"   (role -> tier -> model id)
    cfg.prompt_path("plan", migite_home) -> Path
    cfg.source("models.strong")        -> "defaults" | "<file>" | "env:VAR"

CLI (used by bash):
    migite_config.py env  --repo-root DIR       shell assignments: MIGITE_CFG_<KEY>=..., MIGITE_CFG_MODEL_<ROLE>=...
    migite_config.py get  --repo-root DIR KEY   one value
    migite_config.py show --repo-root DIR       effective config with the source of every key
    migite_config.py validate --repo-root DIR   exit 1 on errors; prints warnings for unknown keys
    migite_config.py init --repo-root DIR [--force]   write a commented starter .migite.yml
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import shlex
import sys
from pathlib import Path
from typing import Any, Mapping

REPO_FILE_NAMES = (".migite.yml", ".migite.yaml", ".migite.json")
USER_FILE_NAMES = ("config.yml", "config.yaml", "config.json")

DEFAULTS: dict[str, Any] = {
    "vault": {
        "base": "~/dev-log",       # DEV_LOG_BASE
        "org": None,               # MIGITE_ORG; None = auto-detect from the repo's parent dir
    },
    "logs": {
        "dir": "~/.dev-workflow/logs",   # LOG_DIR
    },
    "models": {
        "fast": "claude-haiku-4-5-20251001",
        "standard": "claude-sonnet-5",
        "strong": "claude-opus-5",
        "roles": {},                     # optional per-role override: {"critic": "claude-opus-5", ...}
        "timeout_seconds": 600,
        "thinking_timeout_seconds": 900,
    },
    "stack": "auto",                     # auto | rails | generic  (MIGITE_STACK, or --stack)
    "gates": {
        "commit": {
            "policy": "lenient",         # lenient = today's behaviour; strict = 'y' refused over blockers, 'Y' overrides
            "require_clean_lint": True,  # in strict mode, remaining rubocop offenses are a blocker
            "require_green_specs": True, # in strict mode, spec failures / tooling errors are a blocker
        },
        "plan": {
            "warn_after_rejections": 3,  # warn that the task may be too large after N full redos
        },
    },
    "permissions": {
        "interactive": "bypassPermissions",  # run_phase sessions (implement, fix, PR description)
        "heal": "bypassPermissions",         # the auto-heal loop's headless fixes
        "headless": "none",                  # plan/review/knowledge/... (MIGITE_PERMISSION_MODE); none = no flag
    },
    "heal": {
        "max_attempts": 3,                   # MAX_HEAL_ATTEMPTS
        "full_suite_fallback": True,         # Phase 3: run the full rspec suite when no spec files changed
    },
    "prompts": {
        "dir": None,                         # per-project overrides for prompts/<name>.md (relative to repo root)
    },
    "templates": {
        "dir": None,                         # per-project overrides for templates/<name>.md (intakes, commit.md)
    },
    "budget": {
        "max_usd_per_run": None,             # soft cap: warn at every gate once the ledger passes it
        "print_summary": True,               # print the usage table at exit
    },
    "ui": {
        "tmux": "auto",                      # auto (use if $TMUX set) | on | off
        "notify": "auto",                    # auto | off
        "editor": None,                      # EDITOR; None = vim
        "prompt_inline_max": 100000,         # MIGITE_PROMPT_INLINE_MAX
    },
}

# Which model tier each call site uses by default. `models.roles.<role>` overrides the tier.
ROLE_TIERS: dict[str, str] = {
    # migite-plan
    "explore": "fast", "think": "standard", "critic": "strong",
    # migite-review
    "review": "standard", "verdict": "strong",
    # migite bash phases
    "knowledge": "standard", "improve": "standard", "amend": "standard",
    "plan_refine": "standard", "testing_plan": "standard", "jira": "standard",
    # migite-explore
    "lens": "standard", "explore_synth": "strong", "challenge": "strong", "explore_refine": "standard",
    # migite-blueprint
    "analyst": "standard", "blueprint_synth": "strong", "extract": "standard",
    # migite-audit
    "audit_area": "fast", "audit_synth": "standard",
    # migite-pr-review
    "pr_review": "standard", "pr_verdict": "strong",
}

# Environment variables that override file values (env > files). Keeps every
# variable migite documented before the config file existed working unchanged.
ENV_OVERRIDES: dict[str, str] = {
    "DEV_LOG_BASE": "vault.base",
    "MIGITE_ORG": "vault.org",
    "LOG_DIR": "logs.dir",
    "MIGITE_STACK": "stack",
    "MAX_HEAL_ATTEMPTS": "heal.max_attempts",
    "MIGITE_PERMISSION_MODE": "permissions.headless",
    "EDITOR": "ui.editor",
    "MIGITE_PROMPT_INLINE_MAX": "ui.prompt_inline_max",
}

ENUMS: dict[str, tuple[str, ...]] = {
    "gates.commit.policy": ("lenient", "strict"),
    "permissions.interactive": ("bypassPermissions", "acceptEdits", "default", "plan", "none"),
    "permissions.heal": ("bypassPermissions", "acceptEdits", "default", "plan", "none"),
    "permissions.headless": ("bypassPermissions", "acceptEdits", "default", "plan", "none"),
    "ui.tmux": ("auto", "on", "off"),
    "ui.notify": ("auto", "off"),
}

INT_KEYS = ("models.timeout_seconds", "models.thinking_timeout_seconds", "gates.plan.warn_after_rejections",
            "heal.max_attempts", "ui.prompt_inline_max")
BOOL_KEYS = ("gates.commit.require_clean_lint", "gates.commit.require_green_specs",
             "heal.full_suite_fallback", "budget.print_summary")
FLOAT_KEYS = ("budget.max_usd_per_run",)

STARTER_TEMPLATE = """\
# .migite.yml — per-repo migite configuration.
# Precedence: flags > env vars > $MIGITE_CONFIG > this file > ~/.config/migite/config.yml > defaults.
# Every value below is the default; delete what you don't change. `migite config` shows the
# effective result and where each value came from. Needs PyYAML: pip install pyyaml

vault:
  base: ~/dev-log            # DEV_LOG_BASE
  # org: Acme                # MIGITE_ORG — default: name of the directory containing the repo

logs:
  dir: ~/.dev-workflow/logs  # LOG_DIR

models:
  fast: claude-haiku-4-5-20251001   # explorers, audit areas
  standard: claude-sonnet-5         # synthesis, review dimensions, knowledge, amendments
  strong: claude-opus-5             # architecture critic, verdicts, adversarial challenge
  # roles:                          # pin one call site without changing its tier
  #   critic: claude-opus-5
  #   knowledge: claude-haiku-4-5-20251001
  timeout_seconds: 600
  thinking_timeout_seconds: 900

stack: auto                  # auto | rails | generic  (or --stack on the command line)

gates:
  commit:
    policy: lenient          # strict: 'y' is refused while blockers remain; 'Y' overrides and is logged
    require_clean_lint: true
    require_green_specs: true
  plan:
    warn_after_rejections: 3

permissions:
  interactive: bypassPermissions   # implement / fix / PR-description sessions
  heal: bypassPermissions          # auto-heal loop
  headless: none                   # plan, review, knowledge, ... (none = don't pass --permission-mode)

heal:
  max_attempts: 3
  full_suite_fallback: true  # Phase 3 runs the whole rspec suite when no spec files changed; false = skip

# prompts:
#   dir: .migite/prompts     # override any of prompts/{plan,implement,review,architecture_critic}.md
# templates:
#   dir: .migite/templates   # override intake templates or commit.md (the PR-description prompt)

budget:
  # max_usd_per_run: 5.00    # soft cap: every gate banner warns once the run's headless spend passes it
  print_summary: true

ui:
  tmux: auto                 # auto | on | off
  notify: auto               # auto | off
  # editor: nvim             # EDITOR
  prompt_inline_max: 100000  # MIGITE_PROMPT_INLINE_MAX
"""


class ConfigError(Exception):
    pass


# ── helpers ───────────────────────────────────────────────────────────────────

def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _flatten(d: dict, prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in d.items():
        key = f"{prefix}.{k}" if prefix else k
        # An EMPTY mapping is a leaf too — otherwise `stacks: {node: {}}` would
        # flatten to nothing and an unknown section could never be flagged.
        if isinstance(v, dict) and v and key != "models.roles":
            out.update(_flatten(v, key))
        else:
            out[key] = v
    return out


def _set_path(d: dict, dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    cur = d
    for p in parts[:-1]:
        cur = cur.setdefault(p, {})
    cur[parts[-1]] = value


def _get_path(d: dict, dotted: str) -> Any:
    cur: Any = d
    for p in dotted.split("."):
        if not isinstance(cur, dict) or p not in cur:
            return None
        cur = cur[p]
    return cur


def _read_file(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".json":
        try:
            data = json.loads(text) if text.strip() else {}
        except ValueError as e:
            raise ConfigError(f"{path}: invalid JSON: {e}") from e
    else:
        try:
            import yaml  # type: ignore
        except ImportError as e:  # pragma: no cover - depends on the environment
            raise ConfigError(
                f"{path} exists but PyYAML is not installed. Run: pip install pyyaml "
                f"(or rename the file to .json)"
            ) from e
        try:
            data = yaml.safe_load(text) or {}
        except yaml.YAMLError as e:
            raise ConfigError(f"{path}: invalid YAML: {e}") from e
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: top level must be a mapping")
    return data


def _coerce(key: str, value: Any, source: str) -> Any:
    """Type-check/coerce one leaf; env values arrive as strings."""
    if value is None:
        return None
    if key in INT_KEYS:
        try:
            return int(value)
        except (TypeError, ValueError):
            raise ConfigError(f"{key} must be an integer (got {value!r} from {source})")
    if key in FLOAT_KEYS:
        try:
            return float(value)
        except (TypeError, ValueError):
            raise ConfigError(f"{key} must be a number (got {value!r} from {source})")
    if key in BOOL_KEYS:
        if isinstance(value, bool):
            return value
        s = str(value).strip().lower()
        if s in ("true", "yes", "1", "on"):
            return True
        if s in ("false", "no", "0", "off"):
            return False
        raise ConfigError(f"{key} must be true or false (got {value!r} from {source})")
    if key in ENUMS and str(value) not in ENUMS[key]:
        raise ConfigError(f"{key} must be one of {', '.join(ENUMS[key])} (got {value!r} from {source})")
    if key == "models.roles":
        if not isinstance(value, dict):
            raise ConfigError(f"models.roles must be a mapping of role -> model id (from {source})")
        unknown = sorted(set(value) - set(ROLE_TIERS))
        if unknown:
            raise ConfigError(f"models.roles has unknown role(s): {', '.join(unknown)} (from {source}). "
                              f"Known: {', '.join(sorted(ROLE_TIERS))}")
        return {k: str(v) for k, v in value.items()}
    return value


# ── the Config object ─────────────────────────────────────────────────────────

class Config:
    def __init__(self, data: dict, sources: dict[str, str], files: list[Path], warnings: list[str]):
        self._data = data
        self._sources = sources
        self.files = files
        self.warnings = warnings

    def get(self, key: str, default: Any = None) -> Any:
        v = _get_path(self._data, key)
        return default if v is None else v

    def source(self, key: str) -> str:
        return self._sources.get(key, "defaults")

    def flat(self) -> dict[str, Any]:
        return _flatten(self._data)

    def model(self, role: str) -> str:
        """models.roles.<role> if set, else the model for the role's default tier."""
        if role not in ROLE_TIERS:
            raise KeyError(f"unknown model role {role!r}; known: {', '.join(sorted(ROLE_TIERS))}")
        roles = self.get("models.roles") or {}
        if role in roles:
            return str(roles[role])
        return str(self.get(f"models.{ROLE_TIERS[role]}"))

    def models_by_role(self) -> dict[str, str]:
        return {role: self.model(role) for role in ROLE_TIERS}

    def expanded_path(self, key: str) -> Path | None:
        v = self.get(key)
        return Path(os.path.expanduser(str(v))) if v else None

    def _override_file(self, kind: str, name: str, migite_home: str | Path, repo_root: str | Path | None) -> Path:
        override_dir = self.get(f"{kind}.dir")
        if override_dir:
            base = Path(os.path.expanduser(str(override_dir)))
            if not base.is_absolute() and repo_root:
                base = Path(repo_root) / base
            candidate = base / f"{name}.md"
            if candidate.is_file():
                return candidate
        return Path(migite_home) / kind / f"{name}.md"

    def prompt_path(self, name: str, migite_home: str | Path, repo_root: str | Path | None = None) -> Path:
        """prompts.dir/<name>.md if configured and present, else <migite_home>/prompts/<name>.md."""
        return self._override_file("prompts", name, migite_home, repo_root)

    def template_path(self, name: str, migite_home: str | Path, repo_root: str | Path | None = None) -> Path:
        return self._override_file("templates", name, migite_home, repo_root)


def _user_config_path() -> Path | None:
    base = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")) / "migite"
    for name in USER_FILE_NAMES:
        p = base / name
        if p.is_file():
            return p
    return None


def _repo_config_path(repo_root: str | Path | None) -> Path | None:
    if not repo_root:
        return None
    for name in REPO_FILE_NAMES:
        p = Path(repo_root) / name
        if p.is_file():
            return p
    return None


def config_files(repo_root: str | Path | None) -> list[Path]:
    """The files that would be loaded, lowest precedence first."""
    files: list[Path] = []
    user = _user_config_path()
    if user:
        files.append(user)
    repo = _repo_config_path(repo_root)
    if repo:
        files.append(repo)
    explicit = os.environ.get("MIGITE_CONFIG")
    if explicit:
        p = Path(os.path.expanduser(explicit))
        if not p.is_file():
            raise ConfigError(f"MIGITE_CONFIG points to a missing file: {p}")
        files.append(p)
    return files


_CACHE: dict[str, Config] = {}


def load(repo_root: str | Path | None = None, *, env: Mapping[str, str] | None = None, use_cache: bool = True) -> Config:
    """Build the effective config for a repo. Raises ConfigError on an invalid file/value."""
    env_map: Mapping[str, str] = os.environ if env is None else env
    from_os_env = env is None
    cache_key = str(Path(repo_root).resolve()) if repo_root else ""
    if use_cache and from_os_env and cache_key in _CACHE:
        return _CACHE[cache_key]

    data = copy.deepcopy(DEFAULTS)
    sources = {k: "defaults" for k in _flatten(DEFAULTS)}
    warnings: list[str] = []
    known = set(_flatten(DEFAULTS))

    files = config_files(repo_root)
    for path in files:
        loaded = _read_file(path)
        flat = _flatten(loaded)
        for key, value in flat.items():
            if key not in known and not key.startswith("models.roles"):
                warnings.append(f"{path}: unknown key '{key}' (ignored)")
                continue
            coerced = _coerce(key, value, str(path))
            _set_path(data, key, coerced)
            sources[key] = str(path)

    for var, key in ENV_OVERRIDES.items():
        if var in env_map and env_map[var] != "":
            _set_path(data, key, _coerce(key, env_map[var], f"env:{var}"))
            sources[key] = f"env:{var}"

    cfg = Config(data, sources, files, warnings)
    if use_cache and from_os_env:
        _CACHE[cache_key] = cfg
    return cfg


# ── shell export ──────────────────────────────────────────────────────────────

def _shell_key(dotted: str) -> str:
    return "MIGITE_CFG_" + dotted.upper().replace(".", "_").replace("-", "_")


def _shell_value(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (dict, list)):
        return json.dumps(v)
    return str(v)


def to_shell(cfg: Config) -> str:
    lines = []
    for key, value in sorted(cfg.flat().items()):
        if key == "models.roles":
            continue
        lines.append(f"{_shell_key(key)}={shlex.quote(_shell_value(value))}")
    for role, model in sorted(cfg.models_by_role().items()):
        lines.append(f"MIGITE_CFG_MODEL_{role.upper()}={shlex.quote(model)}")
    lines.append("MIGITE_CFG_FILES=" + shlex.quote(" ".join(str(p) for p in cfg.files)))
    lines.append("MIGITE_CFG_WARNINGS=" + shlex.quote("\n".join(cfg.warnings)))
    return "\n".join(lines) + "\n"


def show(cfg: Config) -> str:
    rows = []
    for key, value in sorted(cfg.flat().items()):
        if key == "models.roles":
            value = json.dumps(value) if value else "{}"
        rows.append((key, _shell_value(value) or "(unset)", cfg.source(key)))
    width = max(len(r[0]) for r in rows)
    vwidth = min(40, max(len(r[1]) for r in rows))
    out = [f"  {'key':<{width}}  {'value':<{vwidth}}  source"]
    for k, v, s in rows:
        out.append(f"  {k:<{width}}  {v[:vwidth]:<{vwidth}}  {s}")
    out.append("")
    out.append("  resolved models by role:")
    for role, model in sorted(cfg.models_by_role().items()):
        tier = ROLE_TIERS[role]
        pinned = " (pinned)" if role in (cfg.get("models.roles") or {}) else f" ({tier})"
        out.append(f"    {role:<16} {model}{pinned}")
    out.append("")
    out.append("  files: " + (", ".join(str(p) for p in cfg.files) if cfg.files else "(none — defaults + env only)"))
    for w in cfg.warnings:
        out.append(f"  ⚠ {w}")
    return "\n".join(out)


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description="migite_config: layered configuration")
    ap.add_argument("--repo-root", default=None, help="repo root to look for .migite.yml in (default: none)")
    sub = ap.add_subparsers(dest="command", required=True)
    # --repo-root is accepted before OR after the subcommand (SUPPRESS keeps a
    # subparser from clobbering the top-level value with None).
    subparsers = {
        "env": sub.add_parser("env", help="shell assignments for bash to eval"),
        "get": sub.add_parser("get", help="print one value"),
        "show": sub.add_parser("show", help="effective config with sources"),
        "validate": sub.add_parser("validate", help="exit 1 on errors, print warnings"),
        "init": sub.add_parser("init", help="write a starter .migite.yml into the repo root"),
    }
    for p in subparsers.values():
        p.add_argument("--repo-root", dest="repo_root", default=argparse.SUPPRESS)
    subparsers["get"].add_argument("key")
    subparsers["init"].add_argument("--force", action="store_true")
    args = ap.parse_args()

    if args.command == "init":
        root = Path(args.repo_root or ".")
        target = root / ".migite.yml"
        if target.exists() and not args.force:
            print(f"✘ {target} already exists (use --force to overwrite)", file=sys.stderr)
            sys.exit(1)
        target.write_text(STARTER_TEMPLATE)
        print(f"✔ wrote {target}")
        return

    try:
        cfg = load(args.repo_root, use_cache=False)
    except ConfigError as e:
        if args.command == "env":
            # Make the eval'ing shell fail loudly instead of silently running on defaults.
            print(f"echo {shlex.quote('✘ migite config error: ' + str(e))} >&2; false")
        else:
            print(f"✘ config error: {e}", file=sys.stderr)
        sys.exit(1)

    if args.command == "env":
        sys.stdout.write(to_shell(cfg))
    elif args.command == "get":
        if args.key.startswith("model:"):
            print(cfg.model(args.key.split(":", 1)[1]))
        else:
            v = cfg.get(args.key)
            if v is None:
                sys.exit(1)
            print(_shell_value(v))
    elif args.command == "show":
        print(show(cfg))
    elif args.command == "validate":
        for w in cfg.warnings:
            print(f"⚠ {w}")
        print(f"✔ config valid ({len(cfg.files)} file(s): " + (", ".join(str(p) for p in cfg.files) or "none") + ")")


if __name__ == "__main__":
    main()
