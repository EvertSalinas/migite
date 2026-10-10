#!/usr/bin/env python3
"""runstate - the run manifest (run.json): where a migite run got to.

migite writes run.json at every phase boundary, in the run's own folder
(scratchpad/<task>/<run>/run.json, mirrored to the vault), so an interrupted run
continues from its recorded position instead of restarting at the plan. It
records the run's mode (interactive, or automata for `migite --automata`), its
arguments, repo and layout, and one entry per phase
(plan, tdd, implement, heal, review, deliver) with its status and the gate
counters a phase needs to resume, plus each invocation's mode and exit status.
See docs/run-manifest-and-resume.md.

  python -m migite.runstate init   --file F [--set K=V]... [--set-json K=JSON]... [--add K=V]...
  python -m migite.runstate update --file F [--set K=V]... [--set-json K=JSON]... [--add K=V]...
  python -m migite.runstate export --file F --shell [--prefix MANIFEST_]
  python -m migite.runstate find   --root DIR [--root DIR]... [--jira K] [--task T] [--intake F]
                                   [--audit F] [--builds-only] [--unfinished]

`init` writes a fresh manifest (every phase pending) and applies the sets;
`update` merges the sets into an existing one. A phase status change stamps
started_at (running) or completed_at (done, skipped), and every write bumps
updated_at and recomputes next_phase and the run status. Writes are atomic:
a crash mid-write never leaves a truncated run.json. `export --shell` prints
KEY='value' lines for bash to eval, one per leaf (lists become bash arrays).
`find` prints the newest manifest under the roots (each a folder of task
folders) whose args match. Exit 2 on a missing, malformed or foreign manifest,
or a value the schema doesn't allow; `find` exits 1 when nothing matches.
Stdlib only.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from migite import gateway

TOOL = "migite-run"
SCHEMA_VERSION = 1
FILENAME = "run.json"
PHASES = ("plan", "tdd", "implement", "heal", "review", "deliver")
PHASE_STATUSES = ("pending", "running", "pending_gate", "done", "skipped", "failed")
RUN_STATUSES = ("in_progress", "complete", "failed")
# interactive: someone answers the gates; automata: `migite --automata`, unattended.
MODES = ("interactive", "automata")
FINISHED = ("done", "skipped")
# A run folder: 00-build, or NN-amend-<slug> (lib/vault.sh's RUN_DIR_RE).
RUN_DIR_RE = re.compile(r"^[0-9][0-9]+-(build|amend-[a-z0-9-]+)$")
BUILD_RUN = "00-build"
# Written by migite itself, never through --set.
_RESERVED = {"schema_version", "tool", "generated_at", "updated_at", "next_phase"}


class ManifestError(Exception):
    """run.json is missing, malformed, from another tool or schema, or a value is not allowed."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_manifest() -> dict:
    """A fresh manifest: every phase pending, no args yet."""
    m = gateway.envelope_base(TOOL)
    m["updated_at"] = m["generated_at"]
    m["status"] = "in_progress"
    m["next_phase"] = PHASES[0]
    m["mode"] = MODES[0]
    # The last invocation's exit status (0-3, see `migite --help`), and one line per
    # invocation: "<start> to <end>, <mode>, exit <status>" (lib/manifest.sh).
    m["exit_status"] = None
    m["invocations"] = []
    m["args"] = {
        "task": "", "task_type": "", "jira_ticket": "", "jira_url": "", "intake": "",
        "audit": "", "blueprint": "", "attach": [], "staged": False, "stack": "",
        "amend": {"feedback": "", "file": "", "num": ""},
    }
    m["repo"] = {"org": "", "name": "", "root": "", "branch": "", "base_branch": ""}
    m["layout"] = {"slug": "", "run_slug": "", "task_dir": "", "scratchpad_dir": "",
                   "log_dir": "", "usage_ledger": "", "timestamp": ""}
    m["phases"] = {
        "plan": {"status": "pending", "gate_attempts": 0},
        "tdd": {"status": "pending", "decided": None},
        "implement": {"status": "pending", "stage_num": 0, "stage_count": 0, "stage_labels": []},
        "heal": {"status": "pending", "heal_attempt": 0},
        "review": {"status": "pending", "gate_attempts": 0, "tree_fingerprint": "", "blockers": []},
        "deliver": {"status": "pending"},
    }
    return m


def load(path: str | Path) -> dict:
    """Read and check a manifest. Raises ManifestError on anything resume can't trust."""
    p = Path(path)
    if not p.is_file():
        raise ManifestError(f"no run manifest at {p}")
    try:
        m = json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError) as e:
        raise ManifestError(f"{p} is not valid JSON: {e}") from e
    if not isinstance(m, dict) or m.get("tool") != TOOL:
        raise ManifestError(f"{p} is not a migite run manifest")
    version = m.get("schema_version")
    if version != SCHEMA_VERSION:
        raise ManifestError(f"{p} has schema_version {version!r}; this migite reads {SCHEMA_VERSION}")
    if not isinstance(m.get("phases"), dict) or not isinstance(m.get("args"), dict):
        raise ManifestError(f"{p} is missing its args or phases")
    return m


def save(path: str | Path, m: dict) -> None:
    """Write atomically: a temp file in the same folder, then os.replace over run.json."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{p.name}.", suffix=".tmp", dir=p.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(m, indent=2, ensure_ascii=False) + "\n")
        os.replace(tmp, p)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def next_phase(m: dict) -> str:
    """The first phase not done or skipped, or "done"."""
    for name in PHASES:
        if m["phases"].get(name, {}).get("status") not in FINISHED:
            return name
    return "done"


def _set_path(obj: dict, dotted: str, value: Any, *, append: bool = False) -> None:
    parts = dotted.split(".")
    if not all(parts) or parts[0] in _RESERVED:
        raise ManifestError(f"{dotted!r} can't be set")
    cur = obj
    for part in parts[:-1]:
        nxt = cur.get(part)
        if nxt is None:
            nxt = cur[part] = {}
        if not isinstance(nxt, dict):
            raise ManifestError(f"{dotted!r}: {part!r} is not an object")
        cur = nxt
    leaf = parts[-1]
    if append:
        existing = cur.get(leaf)
        if existing is None:
            existing = cur[leaf] = []
        if not isinstance(existing, list):
            raise ManifestError(f"{dotted!r} is not a list")
        existing.append(value)
    else:
        cur[leaf] = value


def _check_value(dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    if parts[0] == "status" and len(parts) == 1 and value not in RUN_STATUSES:
        raise ManifestError(f"status must be one of {', '.join(RUN_STATUSES)}, not {value!r}")
    if parts[0] == "mode" and len(parts) == 1 and value not in MODES:
        raise ManifestError(f"mode must be one of {', '.join(MODES)}, not {value!r}")
    if parts[0] == "phases":
        if len(parts) < 2 or parts[1] not in PHASES:
            raise ManifestError(f"{dotted!r}: phases are {', '.join(PHASES)}")
        if len(parts) == 3 and parts[2] == "status" and value not in PHASE_STATUSES:
            raise ManifestError(f"{dotted!r} must be one of {', '.join(PHASE_STATUSES)}, not {value!r}")


def apply(m: dict, sets: list[tuple[str, Any, bool]], *, now: str | None = None) -> dict:
    """Merge (dotted key, value, append) triples into m, stamp phase timestamps, and
    recompute updated_at, next_phase and status. Returns m."""
    now = now or _now()
    before = {name: m["phases"].get(name, {}).get("status") for name in PHASES}
    for key, value, append in sets:
        _check_value(key, value)
        _set_path(m, key, value, append=append)
    for name in PHASES:
        phase = m["phases"].setdefault(name, {})
        status = phase.get("status")
        if status == before[name]:
            continue
        if status == "running":
            phase["started_at"] = now
            phase.pop("completed_at", None)
        elif status in FINISHED:
            phase["completed_at"] = now
    m["updated_at"] = now
    m["next_phase"] = next_phase(m)
    if m["next_phase"] == "done":
        m["status"] = "complete"
    elif m.get("status") == "complete":
        m["status"] = "in_progress"
    return m


# ── Shell export ──────────────────────────────────────────────────────────────

def _shell_value(value: Any) -> str:
    if value is None:
        return "''"
    if isinstance(value, bool):
        return "true" if value else "false"
    return shlex.quote(str(value))


def shell_lines(m: dict, prefix: str = "MANIFEST_") -> list[str]:
    """One KEY='value' line per leaf: args.task -> MANIFEST_ARGS_TASK. Lists become
    bash arrays, null an empty string, booleans true/false."""
    lines: list[str] = []

    def walk(obj: Any, path: list[str]) -> None:
        if isinstance(obj, dict):
            for k, v in obj.items():
                walk(v, path + [k])
            return
        name = prefix + "_".join(re.sub(r"[^A-Za-z0-9]", "_", p).upper() for p in path)
        if isinstance(obj, list):
            lines.append(f"{name}=(" + " ".join(_shell_value(v) for v in obj) + ")")
        else:
            lines.append(f"{name}={_shell_value(obj)}")

    walk(m, [])
    return lines


# ── Discovery ─────────────────────────────────────────────────────────────────

def matches(m: dict, *, jira: str = "", task: str = "", intake: str = "", audit: str = "") -> bool:
    """Whether a manifest is the run this invocation names: the Jira key when given,
    else the intake file, else the task text. An audit report must match as well,
    since every audit run without a task shares the task text "audit-remediation".
    No query matches every manifest."""
    args = m.get("args", {})
    if audit and args.get("audit") != audit:
        return False
    if jira:
        return args.get("jira_ticket") == jira
    if intake:
        return args.get("intake") == intake
    if task:
        return args.get("task") == task
    return True


def find(roots: list[str], *, jira: str = "", task: str = "", intake: str = "", audit: str = "",
         builds_only: bool = False, unfinished: bool = False) -> Path | None:
    """The newest (by updated_at) matching manifest under roots, each a folder of task
    folders holding run folders. The same run in two roots (scratchpad and vault
    mirror) counts once, from the first root. Unreadable manifests are skipped."""
    seen: set[tuple[str, str]] = set()
    best: tuple[str, Path] | None = None
    for root in roots:
        base = Path(root)
        if not base.is_dir():
            continue
        for path in sorted(base.glob(f"*/*/{FILENAME}")):
            run_dir, task_dir = path.parent.name, path.parent.parent.name
            if not RUN_DIR_RE.match(run_dir) or (builds_only and run_dir != BUILD_RUN):
                continue
            if (task_dir, run_dir) in seen:
                continue
            try:
                m = load(path)
            except ManifestError:
                continue
            seen.add((task_dir, run_dir))
            if unfinished and m.get("status") == "complete":
                continue
            if not matches(m, jira=jira, task=task, intake=intake, audit=audit):
                continue
            stamp = str(m.get("updated_at") or "")
            if best is None or stamp > best[0]:
                best = (stamp, path)
    return best[1] if best else None


# ── CLI ───────────────────────────────────────────────────────────────────────

def _parse_sets(args: argparse.Namespace) -> list[tuple[str, Any, bool]]:
    out: list[tuple[str, Any, bool]] = []
    for kind, items in (("set", args.set), ("json", args.set_json), ("add", args.add)):
        for item in items or []:
            key, sep, raw = item.partition("=")
            if not sep or not key:
                raise ManifestError(f"expected KEY=VALUE, got {item!r}")
            if kind == "json":
                try:
                    value = json.loads(raw)
                except ValueError as e:
                    raise ManifestError(f"--set-json {key}: not JSON: {raw!r}") from e
            else:
                value = raw
            out.append((key, value, kind == "add"))
    return out


def _cmd_init(args: argparse.Namespace) -> int:
    m = apply(new_manifest(), _parse_sets(args))
    save(args.file, m)
    return 0


def _cmd_update(args: argparse.Namespace) -> int:
    m = apply(load(args.file), _parse_sets(args))
    save(args.file, m)
    return 0


def _cmd_export(args: argparse.Namespace) -> int:
    print("\n".join(shell_lines(load(args.file), args.prefix)))
    return 0


def _cmd_find(args: argparse.Namespace) -> int:
    found = find(args.root, jira=args.jira, task=args.task, intake=args.intake, audit=args.audit,
                 builds_only=args.builds_only, unfinished=args.unfinished)
    if found is None:
        return 1
    print(found)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="migite.runstate", description="the run manifest (run.json)")
    sub = ap.add_subparsers(dest="command", required=True)
    for name in ("init", "update"):
        p = sub.add_parser(name)
        p.add_argument("--file", required=True)
        p.add_argument("--set", action="append", help="KEY=VALUE, the value a string")
        p.add_argument("--set-json", dest="set_json", action="append", help="KEY=JSON")
        p.add_argument("--add", action="append", help="KEY=VALUE, appended to a list")
    p_exp = sub.add_parser("export")
    p_exp.add_argument("--file", required=True)
    p_exp.add_argument("--shell", action="store_true", required=True)
    p_exp.add_argument("--prefix", default="MANIFEST_")
    p_find = sub.add_parser("find")
    p_find.add_argument("--root", action="append", required=True)
    p_find.add_argument("--jira", default="")
    p_find.add_argument("--task", default="")
    p_find.add_argument("--intake", default="")
    p_find.add_argument("--audit", default="")
    p_find.add_argument("--builds-only", dest="builds_only", action="store_true")
    p_find.add_argument("--unfinished", action="store_true")

    args = ap.parse_args(argv)
    handlers = {"init": _cmd_init, "update": _cmd_update, "export": _cmd_export, "find": _cmd_find}
    try:
        return handlers[args.command](args)
    except ManifestError as e:
        print(f"runstate: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
