#!/usr/bin/env python3
"""migite_agent - how bash talks to the configured agent.

Bash never builds an agent CLI's flags or reads its output. It calls one of
these subcommands, which load the config for the repo, pick the agent from
`agent.backend`, and go through the gateway (migite_call.py). The CLI-specific
parts live in agents/<name>.py.

  migite_agent.py info [--shell | --field NAME]
        the agent's description as JSON, as shell assignments (MIGITE_AGENT_*),
        or one field
  migite_agent.py ask --role R --label L [--tool T] [--permission P] [--scope S]... [--thinking]
        one headless call: prompt on stdin, result text on stdout, one usage line
        in $MIGITE_USAGE_LEDGER. Exit 3 when a scope can't be honoured, 1 on failure.
  migite_agent.py session --prompt-file F [--permission P]
        one shell command line that opens an interactive session with the file's
        contents as the first message (a pointer to the file when it is too long)
  migite_agent.py check
        the agent's binary, path, and version; exit 1 when the CLI is missing

--repo-root DIR (before or after the subcommand) selects whose .migite.yml applies.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
from pathlib import Path

import migite_call
import migite_config

SHELL_FIELDS = {
    "MIGITE_AGENT_NAME": "name",
    "MIGITE_AGENT_DISPLAY": "display_name",
    "MIGITE_AGENT_BINARY": "binary",
    "MIGITE_AGENT_EXIT_HINT": "exit_hint",
    "MIGITE_AGENT_INSTRUCTIONS": "instruction_files",
}


def _capabilities(desc: dict) -> str:
    caps = [c for c in ("structured_output", "effort", "usage") if desc.get(c)]
    caps += [f"scope:{s}" for s in desc.get("scopes", [])]
    return " ".join(caps)


def _cmd_info(args: argparse.Namespace) -> int:
    desc = migite_call.AGENT.describe()
    if args.field:
        value = desc.get(args.field)
        if value is None:
            print(f"✘ unknown field {args.field!r}; known: {', '.join(desc)}", file=sys.stderr)
            return 1
        print(json.dumps(value) if isinstance(value, (list, dict)) else value)
        return 0
    if args.shell:
        for var, key in SHELL_FIELDS.items():
            print(f"{var}={shlex.quote(str(desc[key]))}")
        print(f"MIGITE_AGENT_CAPS={shlex.quote(_capabilities(desc))}")
        return 0
    print(json.dumps(desc, indent=2))
    return 0


def _cmd_ask(args: argparse.Namespace) -> int:
    prompt = sys.stdin.read()
    try:
        res = migite_call.call_agent(prompt, args.role, label=args.label, tool=args.tool,
                                     scopes=tuple(args.scope or ()), permission=args.permission,
                                     thinking=args.thinking, timeout=args.timeout or None)
    except migite_call.ScopeUnsupported as e:
        print(f"✘ {e}", file=sys.stderr)
        return 3
    except migite_call.AgentError as e:
        print(f"✘ {e}", file=sys.stderr)
        return 1
    sys.stdout.write(res.text)
    return 0


def _cmd_session(args: argparse.Namespace) -> int:
    prompt = Path(args.prompt_file).read_text()
    launch = migite_call.session_launch(prompt, permission=args.permission, prompt_file=args.prompt_file)
    print(launch.shell())
    return 0


def _cmd_check(args: argparse.Namespace) -> int:
    status = migite_call.check_agent()
    if args.json:
        print(json.dumps(status, indent=2))
    elif status["path"]:
        print(f"✔ Agent CLI: {status['display_name']} at {status['path']}"
              + (f" ({status['version']})" if status["version"] else ""))
    else:
        print(f"✘ Agent CLI not found: {status['binary']} (agent.backend={status['name']}). "
              f"Install it, or point agent.command at it.")
    return 0 if status["path"] else 1


def main() -> None:
    ap = argparse.ArgumentParser(description="migite_agent: bash's interface to the configured agent")
    ap.add_argument("--repo-root", default=None)
    sub = ap.add_subparsers(dest="command", required=True)
    parsers = {
        "info": sub.add_parser("info", help="describe the configured agent"),
        "ask": sub.add_parser("ask", help="one headless call: stdin prompt → stdout text"),
        "session": sub.add_parser("session", help="print the command that opens an interactive session"),
        "check": sub.add_parser("check", help="is the agent CLI installed, and which version"),
    }
    for p in parsers.values():
        p.add_argument("--repo-root", dest="repo_root", default=argparse.SUPPRESS)
    info = parsers["info"]
    mode = info.add_mutually_exclusive_group()
    mode.add_argument("--shell", action="store_true", help="MIGITE_AGENT_* assignments for bash to eval")
    mode.add_argument("--field", default=None, help="print one field")
    ask = parsers["ask"]
    ask.add_argument("--role", required=True, help="call-site role; picks the model and effort")
    ask.add_argument("--label", default="", help="name of this call in the usage ledger")
    ask.add_argument("--tool", default="migite")
    ask.add_argument("--permission", default=None, help="auto | edits | plan | ask | none (default: permissions.headless)")
    ask.add_argument("--scope", action="append", help="named tool scope, e.g. jira.read (repeatable)")
    ask.add_argument("--thinking", action="store_true", help="use the longer thinking timeout")
    ask.add_argument("--timeout", type=int, default=0)
    session = parsers["session"]
    session.add_argument("--prompt-file", required=True)
    session.add_argument("--permission", default=None, help="default: permissions.interactive")
    parsers["check"].add_argument("--json", action="store_true")
    args = ap.parse_args()

    try:
        cfg = migite_config.load(args.repo_root or os.getcwd())
        migite_call.configure_from(cfg)
    except (migite_config.ConfigError, ValueError) as e:
        print(f"✘ {e}", file=sys.stderr)
        sys.exit(1)

    handler = {"info": _cmd_info, "ask": _cmd_ask, "session": _cmd_session, "check": _cmd_check}[args.command]
    sys.exit(handler(args))


if __name__ == "__main__":
    main()
