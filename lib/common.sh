#!/usr/bin/env bash
# lib/common.sh - what every migite command needs before anything else.
#
# Sourced first by bin/migite and by every standalone wrapper (migite-explore,
# migite-audit, ...). Nothing here reads a run's globals: colours, output helpers,
# desktop notifications, and the resolution of the Python interpreter that runs
# the LangGraph tools. Expects MIGITE_HOME to be set by the caller (only a script
# can find its own real path - see the preamble of any bin/ script).

# ── Colors ────────────────────────────────────────────────────────────────────
BOLD='\033[1m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
RED='\033[0;31m'
RESET='\033[0m'

# ── Output ────────────────────────────────────────────────────────────────────
log()     { echo -e "${CYAN}▶ $1${RESET}"; }
success() { echo -e "${GREEN}✔ $1${RESET}"; }
warn()    { echo -e "${YELLOW}⚠ $1${RESET}"; }
error()   { echo -e "${RED}✘ $1${RESET}" >&2; exit 1; }
# notify <subtitle> <message> — desktop notification, best effort. macOS via
# osascript, Linux via notify-send, silent no-op anywhere else. Never fails the run.
notify() {
  [[ "${MIGITE_CFG_UI_NOTIFY:-auto}" == "off" ]] && return 0
  if command -v osascript &>/dev/null; then
    osascript -e "display notification \"$2\" with title \"Migite\" subtitle \"$1\" sound name \"Glass\"" 2>/dev/null || true
  elif command -v notify-send &>/dev/null; then
    notify-send "Migite — $1" "$2" 2>/dev/null || true
  fi
}

require_cmd() {
  command -v "$1" &>/dev/null || error "$1 is required but not installed"
}

# resolve_path <file> — echoes <file> as an absolute path.
# Used on user-supplied file args before migite cd's to $REPO_ROOT, so paths
# given relative to the invocation directory keep resolving correctly.
resolve_path() {
  local p="$1"
  echo "$(cd "$(dirname "$p")" && pwd)/$(basename "$p")"
}

# ── Python ────────────────────────────────────────────────────────────────────
# The Python side lives in the migite/ package of this checkout. Every call below
# is `python -m migite.<module>`, found through PYTHONPATH, so nothing in the
# checkout needs a symlink and the tools run the same from any directory.
export PYTHONPATH="$MIGITE_HOME${PYTHONPATH:+:$PYTHONPATH}"

# migite_resolve_python — sets MIGITE_PYTHON: an explicit MIGITE_PYTHON is
# trusted as-is; else a python3 that actually runs; else the asdf fallback.
# `command -v python3` alone isn't enough: an asdf shim always exists on PATH
# even with no version selected for the current directory, so presence-checking
# it would resolve to a shim that then fails with "No version is set for command
# python3" on any repo without one pinned (migite's own repo has none). Resolved
# dynamically rather than pinned to an asdf patch version so a Python upgrade
# doesn't silently break every tool the way a hardcoded path once did.
migite_resolve_python() {
  if [[ -n "${MIGITE_PYTHON:-}" ]]; then
    return 0
  elif command -v python3 &>/dev/null && python3 --version &>/dev/null; then
    MIGITE_PYTHON="$(command -v python3)"
  else
    MIGITE_PYTHON="$HOME/.asdf/installs/python/3.13.5/bin/python3"
  fi
}

# require_python — the interpreter MIGITE_PYTHON names must exist (a path or a
# command on PATH) and be runnable.
require_python() {
  if [[ ! -x "$MIGITE_PYTHON" ]] && ! command -v "$MIGITE_PYTHON" &>/dev/null; then
    error "Python not found at $MIGITE_PYTHON. Set MIGITE_PYTHON to your Python 3.11+ binary."
  fi
}

# require_langgraph — the LangGraph tools need the langgraph package in that
# interpreter. No model SDK is required: every model call goes through the
# configured agent CLI.
require_langgraph() {
  if ! "$MIGITE_PYTHON" -c "import langgraph" 2>/dev/null; then
    error "Python dependencies missing. Run: $MIGITE_PYTHON -m pip install langgraph"
  fi
}
