#!/usr/bin/env python3
"""migite.smoke - Phase 3.2, the API smoke check: real HTTP calls against the service under
test, running on this machine. Three subcommands, each run by lib/phases/smoke.sh:

  detect  finds the service (no model call): smoke.base_url, else the docker compose service's
          published port, else PORT from .env* / Procfile.dev / config/puma.rb, else 3000. Then a
          GET on /up, /health, /healthz, / says whether it is up, and the start time of the
          process (or the compose service's bind mount) whether it runs the working tree.
  plan    one headless call (role smoke_plan, read tools, JSON schema) turns the diff and the
          testing plan into smoke-plan.json: a list of calls, validated here before anything is
          sent (paths only, safe methods unless smoke.allow_writes, smoke.max_calls, no secrets).
  run     sends the calls with urllib, no proxy, no redirects, loopback only, and writes
          smoke-check.md (Result: PASS | FAIL | SKIPPED, a table per call, the server log around
          each failure) and smoke-check.json. Configured header values come from ${ENV_VARS} and
          are redacted everywhere they could appear.

Exit codes: detect 0 (the state is in the JSON; "down" is a finding). plan 0 written, 1 config or
agent error, 2 no call survived validation; --out is untouched on 1 and 2. run 0 every call
passed, 1 config / plan / URL error, 2 a call failed or errored, 3 skipped (service down or
stale, nothing to send); the report is still written on 2 and 3. Imports no langgraph and no
third-party package, so bash can run it wherever migite's own version check passes.

  python -m migite.smoke --repo-root DIR detect [--base-url URL] [--changed-files FILE] [--out detect.json]
  python -m migite.smoke --repo-root DIR plan --diff FILE [--testing-plan FILE] [--detect detect.json]
                         [--changed-files FILE] --out smoke-plan.json
  python -m migite.smoke --repo-root DIR run --plan smoke-plan.json [--detect detect.json] [--base-url URL]
                         --out-md smoke-check.md --out-json smoke-check.json
"""

import argparse
import base64
import http.client
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from migite import config
from migite import gateway

ROLE = "smoke_plan"                   # standard tier, read tools (migite/config.py ROLE_TIERS, ROLE_TOOLS)
LABEL = "smoke_plan"                  # the ledger label
TOOL = "migite-smoke"                 # the envelope's tool field
MIGITE_HOME = Path(__file__).resolve().parents[1]

SAFE_METHODS = ("GET", "HEAD", "OPTIONS")
WRITE_METHODS = ("POST", "PUT", "PATCH", "DELETE")
LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")
PROBE_PATHS = ("/up", "/health", "/healthz", "/")
PROBE_TIMEOUT = 3
COMPOSE_FILES = ("compose.yaml", "compose.yml", "docker-compose.yml", "docker-compose.yaml")
PREFERRED_SERVICES = ("web", "app", "api", "rails")
DOTENV_FILES = (".env.development.local", ".env.local", ".env.development", ".env")
DEFAULT_PORT = 3000
SUBPROCESS_TIMEOUT = 15
# Files the dev server reads once at boot: a change to one of them after the server started
# means the running code is not the working tree. config/routes.rb and app/** reload.
RESTART_SENSITIVE = (re.compile(r"(^|/)config/initializers/"), re.compile(r"(^|/)config/environments/"),
                     re.compile(r"(^|/)config/application\.rb$"), re.compile(r"(^|/)Gemfile\.lock$"),
                     re.compile(r"(^|/)\.env[^/]*$"))
ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
HEADER_NAME_RE = config.HEADER_NAME_RE
JSON_PATH_RE = re.compile(r"^[A-Za-z0-9_-]+(\.[A-Za-z0-9_-]+)*$")
# Request headers the plan may not set: the connection's, and the ones only config may carry.
RESERVED_HEADERS = ("host", "content-length", "transfer-encoding", "connection", "cookie", "authorization")
ENV_REF_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
BODY_EXCERPT_MAX = 1500
BODY_READ_MAX = 262144
BODY_JSON_MAX = 16384
LOG_EXCERPT_MAX = 4000
LOG_TOTAL_MAX = 20000
TESTING_PLAN_MAX = 6000
MIN_SECRET_LEN = 4

SMOKE_PLAN_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "description": "one line: what these calls prove about the change"},
        "calls": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "pattern": "^[a-z0-9][a-z0-9_-]{0,39}$"},
                    "why": {"type": "string", "description": "what a pass proves, one line"},
                    "method": {"type": "string", "enum": list(SAFE_METHODS + WRITE_METHODS)},
                    "path": {"type": "string", "description": "path and query only, starting with /; never a full URL"},
                    "auth": {"type": "boolean", "description": "true = migite attaches the configured auth headers"},
                    "headers": {"type": "object", "additionalProperties": {"type": "string"}},
                    "body": {"type": ["object", "array", "string", "null"]},
                    "expect": {
                        "type": "object",
                        "properties": {
                            "status": {"type": ["integer", "array"], "items": {"type": "integer"}},
                            "json_has": {"type": "array", "items": {"type": "string"},
                                         "description": "dotted paths that must exist in a JSON body, e.g. data.0.id"},
                            "body_contains": {"type": ["string", "null"]},
                        },
                        "required": ["status"],
                    },
                },
                "required": ["id", "why", "method", "path", "auth", "expect"],
            },
        },
    },
    "required": ["summary", "calls"],
}


# ── Small helpers ─────────────────────────────────────────────────────────────

def _iso(epoch: float | None) -> str | None:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat(timespec="seconds") if epoch else None


def _run(cmd: list[str], cwd: str | Path, timeout: int = SUBPROCESS_TIMEOUT) -> str | None:
    """stdout of a command, or None when it is missing, fails or times out."""
    try:
        res = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return res.stdout if res.returncode == 0 else None


def is_loopback(url: str) -> bool:
    """http(s) on 127.0.0.1, localhost or ::1; a user info part (`http://127.0.0.1@evil`) fails."""
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return False
    if parts.scheme not in ("http", "https") or not parts.netloc or "@" in parts.netloc:
        return False
    host = (parts.hostname or "").strip("[]").lower()
    return host in LOOPBACK_HOSTS


def truncate(text: str, max_bytes: int) -> str:
    """Head 60% and tail 40% around an elision marker, as lib/stack.sh truncate_log does."""
    data = text.encode("utf-8", errors="replace")
    if len(data) <= max_bytes:
        return text
    head, tail = int(max_bytes * 0.6), int(max_bytes * 0.4)
    elided = len(data) - head - tail
    return (data[:head].decode("utf-8", errors="replace") + f"\n... [{elided} bytes elided] ...\n"
            + data[-tail:].decode("utf-8", errors="replace"))


def json_has(obj: Any, dotted: str) -> bool:
    """Whether a dotted path (integers index lists) resolves inside parsed JSON."""
    cur = obj
    for part in dotted.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return False
    return True


def _json_object(text: str, structured: Any) -> dict | None:
    """The reply as a dict: structured output when honoured, else the first {...} in the text
    (code fences allowed; the same extraction as migite/doc_edits.py parse_reply)."""
    if isinstance(structured, dict):
        return structured
    if not text:
        return None
    stripped = re.sub(r"^```(?:json)?\s*|\s*```\s*$", "", text.strip())
    start, end = stripped.find("{"), stripped.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        obj = json.loads(stripped[start:end + 1])
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


# ── Headers and secrets ───────────────────────────────────────────────────────

def expand_headers(configured: dict[str, str], env: Mapping[str, str] | None = None
                   ) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    """(headers, secrets, missing): every ${VAR} replaced from env; secrets maps the var name to
    the value it contributed (for redaction); missing maps a header to the first unset var."""
    env = os.environ if env is None else env
    headers: dict[str, str] = {}
    secrets: dict[str, str] = {}
    missing: dict[str, str] = {}
    for name, template in (configured or {}).items():
        unset = None
        def sub(m: re.Match) -> str:
            nonlocal unset
            var = m.group(1)
            val = env.get(var)
            if val is None or val == "":
                unset = unset or var
                return ""
            if len(val) >= MIN_SECRET_LEN:
                secrets[var] = val
            return val
        value = ENV_REF_RE.sub(sub, template)
        if unset:
            missing[name] = unset
        else:
            headers[name] = value
    return headers, secrets, missing


class Redactor:
    """Replaces every configured secret (and its url-encoded and base64 forms, which is how a
    token comes back in a log line or an echoed header) with [redacted ${VAR}], longest first."""

    def __init__(self, secrets: dict[str, str]):
        pairs: list[tuple[str, str]] = []
        for var, val in secrets.items():
            forms = {val, urllib.parse.quote(val, safe=""), base64.b64encode(val.encode()).decode()}
            pairs.extend((form, f"[redacted ${{{var}}}]") for form in forms if len(form) >= MIN_SECRET_LEN)
        self.pairs = sorted(pairs, key=lambda p: -len(p[0]))

    def __call__(self, text: str) -> str:
        for form, marker in self.pairs:
            text = text.replace(form, marker)
        return text


# ── detect ────────────────────────────────────────────────────────────────────

def parse_port_from_puma(text: str) -> int | None:
    m = re.search(r"""port\s+ENV\.fetch\(\s*["']PORT["']\s*(?:,\s*(\d+)\s*\)|\)\s*\{\s*(\d+)\s*\})""", text)
    if m:
        return int(m.group(1) or m.group(2))
    m = re.search(r"^\s*port\s+(\d+)", text, re.MULTILINE)
    return int(m.group(1)) if m else None


def parse_port_from_procfile(text: str) -> int | None:
    for line in text.splitlines():
        if not re.match(r"^\s*web\s*:", line):
            continue
        m = re.search(r"(?:-p|--port)[ =](\d+)", line) or re.search(r"PORT=(\d+)", line)
        if m:
            return int(m.group(1))
    return None


def parse_port_from_dotenv(text: str) -> int | None:
    m = re.search(r"^\s*(?:export\s+)?PORT=[\"']?(\d+)", text, re.MULTILINE)
    return int(m.group(1)) if m else None


def local_port(repo_root: Path, pinned: int | None = None) -> tuple[int, str]:
    """(port, source) for a dev server outside compose."""
    if pinned:
        return int(pinned), "config"
    for name in DOTENV_FILES:
        p = repo_root / name
        if p.is_file():
            port = parse_port_from_dotenv(p.read_text(errors="replace"))
            if port:
                return port, "dotenv"
    p = repo_root / "Procfile.dev"
    if p.is_file():
        port = parse_port_from_procfile(p.read_text(errors="replace"))
        if port:
            return port, "procfile"
    p = repo_root / "config" / "puma.rb"
    if p.is_file():
        port = parse_port_from_puma(p.read_text(errors="replace"))
        if port:
            return port, "puma"
    return DEFAULT_PORT, "default"


def restart_sensitive(changed: list[str]) -> list[str]:
    return [f for f in changed if any(rx.search(f) for rx in RESTART_SENSITIVE)]


def stale_by_mtime(repo_root: Path, files: list[str], started_at: float) -> str | None:
    """The first restart-sensitive file written after the server started, with both times."""
    for f in files:
        p = repo_root / f
        try:
            mtime = p.stat().st_mtime
        except OSError:
            continue
        if mtime > started_at:
            return (f"process started {_iso(started_at)}, but {f} changed at {_iso(mtime)}; "
                    "restart the server so it runs the working tree")
    return None


def _compose_services(repo_root: Path) -> dict | None:
    out = _run(["docker", "compose", "config", "--format", "json"], repo_root)
    if not out:
        return None
    try:
        data = json.loads(out)
    except ValueError:
        return None
    services = data.get("services") if isinstance(data, dict) else None
    return services if isinstance(services, dict) else None


def _published_port(service: dict) -> int | None:
    for entry in service.get("ports") or []:
        published = entry.get("published") if isinstance(entry, dict) else None
        if published is None and isinstance(entry, str):          # "3000:3000" when not normalized
            published = entry.split(":")[0] if ":" in entry else None
        m = re.match(r"^\s*(\d+)", str(published or ""))
        if m:
            return int(m.group(1))
    return None


def _pick_service(services: dict, pinned: str | None) -> tuple[str, dict] | None:
    if pinned:
        return (pinned, services[pinned]) if pinned in services else None
    for name in PREFERRED_SERVICES:
        if name in services and _published_port(services[name]):
            return name, services[name]
    for name, svc in services.items():
        if isinstance(svc, dict) and _published_port(svc):
            return name, svc
    return None


def _bind_mounts_repo(service: dict, repo_root: Path) -> bool:
    root = repo_root.resolve()
    for vol in service.get("volumes") or []:
        if not isinstance(vol, dict) or vol.get("type") != "bind":
            continue
        try:
            src = Path(str(vol.get("source") or "")).resolve()
        except OSError:
            continue
        if src == root or src in root.parents:
            return True
    return False


def _compose_ps(repo_root: Path, service: str) -> dict | None:
    out = _run(["docker", "compose", "ps", "--format", "json", "--all", service], repo_root)
    if not out or not out.strip():
        return None
    rows: list = []
    try:
        data = json.loads(out)
        rows = data if isinstance(data, list) else [data]
    except ValueError:
        for line in out.splitlines():                              # NDJSON, one object per line
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    for row in rows:
        if isinstance(row, dict) and row.get("Service", service) == service:
            return row
    return None


def probe(base_url: str, timeout: int = PROBE_TIMEOUT) -> dict | None:
    """The first of PROBE_PATHS that answers with any HTTP status, or None when nothing does."""
    opener = _opener()
    for path in PROBE_PATHS:
        req = urllib.request.Request(base_url.rstrip("/") + path, method="GET",
                                     headers={"User-Agent": "migite-smoke", "Accept": "*/*"})
        try:
            with opener.open(req, timeout=timeout) as resp:
                return {"path": path, "status": resp.status}
        except urllib.error.HTTPError as e:
            return {"path": path, "status": e.code}
        except (urllib.error.URLError, OSError, http.client.HTTPException):
            continue
    return None


def detect(repo_root: str | Path, cfg: config.Config, *, base_url: str | None = None,
           changed_files: list[str] | None = None) -> dict:
    root = Path(repo_root)
    info: dict[str, Any] = {"base_url": None, "source": None, "state": "unknown", "reason": "",
                            "compose_service": None, "started_at": None, "port": None, "probe": None,
                            "restart_sensitive_changed": restart_sensitive(changed_files or [])}
    reasons: list[str] = []
    started_at: float | None = None
    compose_stale: str | None = None
    compose_down: str | None = None

    url = base_url or cfg.get("smoke.base_url")
    if url:
        info["base_url"], info["source"] = str(url).rstrip("/"), "config"
    else:
        services = None
        if any((root / f).is_file() for f in COMPOSE_FILES):
            if shutil.which("docker"):
                services = _compose_services(root)
                if services is None:
                    reasons.append("compose: `docker compose config` failed")
            else:
                reasons.append("compose file present but docker is not on PATH")
        picked = _pick_service(services, cfg.get("smoke.compose_service")) if services else None
        if services and not picked:
            reasons.append("compose: no service publishes a port" if not cfg.get("smoke.compose_service")
                           else f"compose: no service named {cfg.get('smoke.compose_service')}")
        if picked:
            name, svc = picked
            port = _published_port(svc)
            if port:
                info.update(base_url=f"http://127.0.0.1:{port}", source="compose", compose_service=name, port=port)
                row = _compose_ps(root, name)
                state = str((row or {}).get("State") or "").lower()
                if row is None or state != "running":
                    compose_down = f"compose service {name} is {state or 'not created'}"
                else:
                    created = row.get("Created")
                    if isinstance(created, (int, float)) and created > 0:
                        started_at = float(created)
                if not _bind_mounts_repo(svc, root):
                    compose_stale = (f"compose service {name} has no bind mount of this repo; it runs the "
                                     "image's code, not the working tree")
            else:
                reasons.append(f"compose service {name} publishes no port")
        if not info["base_url"]:
            port, source = local_port(root, cfg.get("smoke.port"))
            info.update(base_url=f"http://127.0.0.1:{port}", source=source, port=port)

    # A process listening on the port, and when it started (the restart rule needs it).
    if info["source"] != "compose" or compose_down:
        port = info["port"] or urllib.parse.urlsplit(info["base_url"]).port or (443 if info["base_url"].startswith("https") else 80)
        info["port"] = port
        if shutil.which("lsof"):
            out = _run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-Fp"], root)
            pid = next((line[1:] for line in (out or "").splitlines() if line.startswith("p")), None)
            if pid:
                lstart = _run(["ps", "-o", "lstart=", "-p", pid], root)
                try:
                    started_at = time.mktime(time.strptime((lstart or "").strip(), "%a %b %d %H:%M:%S %Y"))
                except ValueError:
                    reasons.append(f"start time of pid {pid} unknown; the restart rule is skipped")
                if compose_down:
                    compose_down = None                            # something else serves the port
                    compose_stale = None
                    info["compose_service"] = None
                    info["source"] = "local"
            elif not compose_down:
                reasons.append(f"nothing listens on 127.0.0.1:{port} (lsof)")
        else:
            reasons.append("lsof not on PATH; the restart rule is skipped")

    hit = probe(info["base_url"])
    info["probe"] = hit
    info["started_at"] = _iso(started_at)
    if hit is None:
        info["state"] = "down"
        reasons.insert(0, compose_down or f"no HTTP answer at {info['base_url']}")
    elif compose_stale:
        info["state"] = "stale"
        reasons.insert(0, compose_stale)
    else:
        stale = stale_by_mtime(root, info["restart_sensitive_changed"], started_at) if started_at else None
        if stale:
            info["state"] = "stale"
            reasons.insert(0, stale)
        else:
            info["state"] = "up"
            if info["restart_sensitive_changed"] and not started_at:
                reasons.append("restart-sensitive files changed but the start time is unknown; "
                               "the result may reflect the old code")
    info["reason"] = "; ".join(reasons)
    return info


# ── plan ──────────────────────────────────────────────────────────────────────

def build_prompt(template: str, *, diff: str, testing_plan: str, base_url: str, header_names: list[str],
                 max_calls: int, allow_writes: bool, changed_files: list[str]) -> str:
    writes = ("Write methods (POST, PUT, PATCH, DELETE) are allowed: they hit the local development database, "
              "so every write must be idempotent or create a record you also delete later in the list."
              if allow_writes else
              "Only GET, HEAD and OPTIONS are sent; a write call is dropped before it is sent, so do not plan one.")
    auth = (f"When `auth` is true migite attaches these configured headers: {', '.join(header_names)}. "
            "Plan one call with `auth: false` on an authenticated route when a rejection (401 / 403 / 302) proves the guard."
            if header_names else
            "No auth headers are configured: mark every call `auth: false`; a route that needs a session "
            "is checked for its rejection status, not its happy path.")
    plan_block = f"\n## Testing plan (its verification steps name routes and expected results)\n{testing_plan}\n" if testing_plan else ""
    files = "\n".join(f"- {f}" for f in changed_files) or "- (none listed)"
    return f"""{template.rstrip()}

## Service
Base URL: {base_url} (the development server on this machine; migite prepends it to every path).
Limits: at most {max_calls} calls, in order of value. {writes}
{auth}
{plan_block}
## Files changed
{files}

## Diff (capped)
{diff}
"""


def validate_plan(obj: Any, *, allow_writes: bool, max_calls: int) -> tuple[list[dict], list[dict]] | None:
    """(calls, dropped) after the safety rules, or None when the reply is not a plan at all."""
    if not isinstance(obj, dict) or not isinstance(obj.get("calls"), list):
        return None
    calls: list[dict] = []
    dropped: list[dict] = []
    seen: set[str] = set()
    for i, raw in enumerate(obj["calls"]):
        ident = str(raw.get("id") or f"call-{i + 1}") if isinstance(raw, dict) else f"call-{i + 1}"

        def drop(reason: str) -> None:
            dropped.append({"id": ident, "reason": reason})

        if not isinstance(raw, dict):
            drop("not an object"); continue
        if not ID_RE.match(ident):
            drop("id must be lowercase letters, digits, _ or -"); continue
        if ident.lower() in seen:
            drop("duplicate id"); continue
        path = raw.get("path")
        if not isinstance(path, str):
            drop("path must start with a single /"); continue
        parts = urllib.parse.urlsplit(path)
        if "://" in path or parts.scheme or parts.netloc or path.startswith("//") or "@" in path.split("?", 1)[0]:
            drop("absolute URL or host in path"); continue
        if not path.startswith("/") or re.search(r"\s|[\x00-\x1f]", path):
            drop("path must start with a single /"); continue
        method = str(raw.get("method") or "GET").upper()
        if method not in SAFE_METHODS + WRITE_METHODS:
            drop(f"unknown method {method}"); continue
        if method in WRITE_METHODS and not allow_writes:
            drop(f"write method {method} needs smoke.allow_writes: true"); continue
        headers: dict[str, str] = {}
        bad_header = None
        for name, val in (raw.get("headers") or {}).items() if isinstance(raw.get("headers"), dict) else []:
            name, val = str(name), str(val)
            if not HEADER_NAME_RE.match(name) or name.lower() in RESERVED_HEADERS or "\n" in val or "\r" in val:
                bad_header = f"header {name} is reserved or malformed"; break
            if "${" in val:
                bad_header = "only smoke.headers may reference env vars"; break
            headers[name] = val
        if bad_header:
            drop(bad_header); continue
        body = raw.get("body")
        if body is not None:
            if method not in WRITE_METHODS:
                drop(f"{method} carries no body"); continue
            if len(json.dumps(body)) > BODY_JSON_MAX:
                drop("body over 16 KB"); continue
        expect_raw = raw.get("expect") if isinstance(raw.get("expect"), dict) else {}
        status_raw = expect_raw.get("status", 200)
        statuses = [s for s in (status_raw if isinstance(status_raw, list) else [status_raw])
                    if isinstance(s, int) and not isinstance(s, bool) and 100 <= s <= 599]
        expect: dict[str, Any] = {"status": statuses or [200]}
        paths = [p for p in (expect_raw.get("json_has") or []) if isinstance(p, str) and JSON_PATH_RE.match(p)]
        if paths:
            expect["json_has"] = paths
        contains = expect_raw.get("body_contains")
        if isinstance(contains, str) and 0 < len(contains) <= 200:
            expect["body_contains"] = contains
        seen.add(ident.lower())
        call = {"id": ident, "why": str(raw.get("why") or ""), "method": method, "path": path,
                "auth": bool(raw.get("auth")), "headers": headers, "expect": expect}
        if body is not None:
            call["body"] = body
        calls.append(call)
    if len(calls) > max_calls:
        for extra in calls[max_calls:]:
            dropped.append({"id": extra["id"], "reason": f"over smoke.max_calls ({max_calls})"})
        calls = calls[:max_calls]
    return calls, dropped


# ── run ───────────────────────────────────────────────────────────────────────

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):   # noqa: N802 - urllib's name
        return None


def _opener() -> urllib.request.OpenerDirector:
    """No proxy (a corporate proxy must never see loopback) and no redirects (a 3xx is the answer)."""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())


def _send(opener: urllib.request.OpenerDirector, base_url: str, call: dict, headers: dict[str, str],
          timeout: int) -> dict:
    """One call: status, elapsed, the capped body, the Location, or an error."""
    data = None
    req_headers = {"User-Agent": "migite-smoke", "Accept": "application/json, text/html;q=0.5", **headers}
    if "body" in call:
        body = call["body"]
        data = (body if isinstance(body, str) else json.dumps(body)).encode()
        req_headers.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(base_url.rstrip("/") + call["path"], method=call["method"], data=data,
                                 headers=req_headers)
    t0 = time.perf_counter()
    try:
        with opener.open(req, timeout=timeout) as resp:
            raw = resp.read(BODY_READ_MAX + 1)
            status, location = resp.status, resp.headers.get("Location")
    except urllib.error.HTTPError as e:
        raw = e.read(BODY_READ_MAX + 1)
        status, location = e.code, e.headers.get("Location")
    except (urllib.error.URLError, OSError, http.client.HTTPException) as e:
        reason = getattr(e, "reason", e)
        kind = "timed out" if isinstance(reason, TimeoutError) or "timed out" in str(reason) else f"{reason}"
        return {"status": None, "elapsed_ms": int((time.perf_counter() - t0) * 1000), "error": kind}
    body_text = raw[:BODY_READ_MAX].decode("utf-8", errors="replace")
    return {"status": status, "elapsed_ms": int((time.perf_counter() - t0) * 1000), "body": body_text,
            "truncated": len(raw) > BODY_READ_MAX, "location": location}


def evaluate(call: dict, sent: dict) -> tuple[str, str]:
    """(result, reason): PASS, FAIL with what missed, or ERROR when no response came back."""
    if sent.get("error"):
        return "ERROR", sent["error"]
    expect = call["expect"]
    misses: list[str] = []
    if sent["status"] not in expect["status"]:
        loc = f" (Location: {sent['location']})" if sent.get("location") else ""
        misses.append(f"status {sent['status']} not in {expect['status']}{loc}")
    if expect.get("json_has"):
        try:
            parsed = json.loads(sent.get("body") or "")
        except ValueError:
            parsed = None
        if parsed is None:
            misses.append("body is not JSON")
        else:
            misses.extend(f"json_has {p} missing" for p in expect["json_has"] if not json_has(parsed, p))
    if expect.get("body_contains") and expect["body_contains"] not in (sent.get("body") or ""):
        misses.append(f"body does not contain {expect['body_contains']!r}")
    return ("FAIL", "; ".join(misses)) if misses else ("PASS", "")


class _LogTail:
    """The server log written during one call: the bytes appended to smoke.log_file, or
    `docker compose logs --since` for a compose service."""

    def __init__(self, repo_root: Path, detected: dict, log_file: str):
        self.root = repo_root
        self.service = detected.get("compose_service") if detected.get("source") == "compose" else None
        self.path = (repo_root / log_file) if log_file and not self.service else None
        self.budget = LOG_TOTAL_MAX

    def start(self) -> Any:
        if self.service:
            return datetime.now(timezone.utc).isoformat(timespec="seconds")
        try:
            return self.path.stat().st_size if self.path and self.path.is_file() else None
        except OSError:
            return None

    def since(self, marker: Any) -> str:
        if self.budget <= 0:
            return "(log excerpt omitted: cap reached)"
        text = ""
        if self.service and marker:
            text = _run(["docker", "compose", "logs", "--no-color", "--no-log-prefix", "--since", marker, self.service],
                        self.root) or ""
        elif self.path and marker is not None:
            try:
                with self.path.open("rb") as fh:
                    fh.seek(marker)
                    text = fh.read().decode("utf-8", errors="replace")
            except OSError:
                text = ""
        text = truncate(text.strip(), LOG_EXCERPT_MAX)
        self.budget -= len(text.encode("utf-8", errors="replace"))
        return text


def run_calls(base_url: str, calls: list[dict], *, auth_headers: dict[str, str], missing: dict[str, str],
              timeout: int, log_tail: _LogTail, redact: Redactor) -> list[dict]:
    opener = _opener()
    results: list[dict] = []
    for call in calls:
        rec = {"id": call["id"], "why": call["why"], "method": call["method"], "path": call["path"],
               "auth": call["auth"], "expect": call["expect"]}
        if call["auth"] and missing:
            name, var = next(iter(missing.items()))
            rec.update(status=None, elapsed_ms=0, result="ERROR",
                       reason=f"header {name} references unset ${var}; call not sent")
            results.append(rec)
            continue
        marker = log_tail.start()
        sent = _send(opener, base_url, call, {**(auth_headers if call["auth"] else {}), **call["headers"]}, timeout)
        result, reason = evaluate(call, sent)
        rec.update(status=sent.get("status"), elapsed_ms=sent["elapsed_ms"], result=result, reason=redact(reason))
        if sent.get("location"):
            rec["location"] = redact(str(sent["location"]))
        if result != "PASS":
            if sent.get("body"):
                rec["body_excerpt"] = redact(truncate(sent["body"], BODY_EXCERPT_MAX))
            excerpt = redact(log_tail.since(marker))
            if excerpt:
                rec["log_excerpt"] = excerpt
        results.append(rec)
    return results


def render_report(*, result: str, reason: str, base_url: str, detected: dict, calls: list[dict],
                  dropped: list[dict], auth_names: list[str], missing: dict[str, str]) -> str:
    where = ""
    if detected.get("compose_service"):
        where = f" (compose service {detected['compose_service']}"
        where += f", up since {detected['started_at']})" if detected.get("started_at") else ")"
    elif detected.get("started_at"):
        where = f" (local process, up since {detected['started_at']})"
    lines = ["# Smoke check", f"Result: {result}{' - ' + reason if reason else ''}", f"URL: {base_url}{where}",
             f"Plan: {len(calls)} calls run, {len(dropped)} dropped by validation", "", "## Calls",
             "| # | id | method | path | expect | status | ms | result | reason |",
             "|---|----|--------|------|--------|--------|----|--------|--------|"]
    for i, c in enumerate(calls, 1):
        expect = "/".join(str(s) for s in c["expect"]["status"])
        cell = lambda v: str(v if v is not None else "-").replace("|", "\\|")   # noqa: E731
        lines.append(f"| {i} | {cell(c['id'])} | {c['method']} | {cell(c['path'])} | {expect} | {cell(c.get('status'))} "
                     f"| {c.get('elapsed_ms', '-')} | {c['result']} | {cell(c.get('reason'))} |")
    if not calls:
        lines.append("| - | - | - | - | - | - | - | - | no calls were sent |")
    excerpts = [c for c in calls if c.get("log_excerpt") or c.get("body_excerpt")]
    if excerpts:
        lines += ["", "## Server log excerpts"]
        for c in excerpts:
            lines += ["", f"### {c['id']} ({c['result']})"]
            if c.get("body_excerpt"):
                lines += ["Response body:", "```", c["body_excerpt"], "```"]
            if c.get("log_excerpt"):
                lines += ["Server log:", "```", c["log_excerpt"], "```"]
    lines += ["", "## Notes"]
    notes = []
    if auth_names:
        notes.append(f"- auth headers attached on `auth` calls: {', '.join(auth_names)} (values redacted)")
    for name, var in missing.items():
        notes.append(f"- header {name} references ${var}, which is unset: calls marked auth were not sent")
    notes.extend(f"- dropped `{d['id']}`: {d['reason']}" for d in dropped)
    if detected.get("reason"):
        notes.append(f"- detection: {detected['reason']}")
    lines += notes or ["- none"]
    return "\n".join(lines) + "\n"


# ── CLI ───────────────────────────────────────────────────────────────────────

def _read(path: str | None, limit: int | None = None) -> str:
    if not path:
        return ""
    p = Path(path)
    text = p.read_text(errors="replace") if p.is_file() else ""
    return truncate(text, limit) if limit else text


def _read_lines(path: str | None) -> list[str]:
    return [line.strip() for line in _read(path).splitlines() if line.strip()]


def _read_json(path: str | None) -> dict | None:
    if not path or not Path(path).is_file():
        return None
    try:
        obj = json.loads(Path(path).read_text())
    except ValueError:
        return None
    return obj if isinstance(obj, dict) else None


def _cmd_detect(args: argparse.Namespace, cfg: config.Config) -> int:
    info = detect(args.repo_root, cfg, base_url=args.base_url, changed_files=_read_lines(args.changed_files))
    text = json.dumps(info, indent=2) + "\n"
    if args.out:
        Path(args.out).write_text(text)
    sys.stdout.write(text)
    return 0


def _cmd_plan(args: argparse.Namespace, cfg: config.Config) -> int:
    detected = _read_json(args.detect) or {}
    base_url = args.base_url or detected.get("base_url") or cfg.get("smoke.base_url") or f"http://127.0.0.1:{DEFAULT_PORT}"
    template_path = cfg.prompt_path("smoke_plan", MIGITE_HOME, args.repo_root)
    if not template_path.is_file():
        print(f"  ✘ smoke: prompt file missing: {template_path} - is the migite checkout intact?", file=sys.stderr, flush=True)
        return 1
    allow_writes = bool(cfg.get("smoke.allow_writes"))
    max_calls = int(cfg.get("smoke.max_calls") or 12)
    header_names = sorted(cfg.get("smoke.headers") or {})
    changed = _read_lines(args.changed_files)
    existing = _read_json(args.existing) if args.existing else None
    if existing and isinstance(existing.get("calls"), list) and existing.get("changed_files") == changed:
        print(f"  ✔ Reusing {args.existing}: the changed files are the same (delete it to plan again)", flush=True)
        return 0
    prompt = build_prompt(template_path.read_text(), diff=_read(args.diff), testing_plan=_read(args.testing_plan, TESTING_PLAN_MAX),
                          base_url=base_url, header_names=header_names, max_calls=max_calls,
                          allow_writes=allow_writes, changed_files=changed)
    schema = SMOKE_PLAN_SCHEMA if gateway.supports("structured_output") else None
    if schema is None:
        print(f"  ▶ {gateway.AGENT.name} backend has no structured output - the call list is parsed from the text", flush=True)
    try:
        res = gateway.call_agent(prompt, ROLE, label=LABEL, tool="migite", schema=schema)
    except gateway.AgentError as e:
        print(f"  ✘ smoke: {e}", file=sys.stderr, flush=True)
        return 1
    obj = _json_object(res.text, res.structured) or {}
    validated = validate_plan(obj, allow_writes=allow_writes, max_calls=max_calls)
    if validated is None:
        print("  ✘ smoke: the reply had no usable call list", file=sys.stderr, flush=True)
        return 1
    calls, dropped = validated
    if not calls:
        print(f"  ⚠ smoke: no call survived validation ({len(dropped)} dropped)", flush=True)
        for d in dropped:
            print(f"    - {d['id']}: {d['reason']}", flush=True)
        return 2
    gateway.write_json(args.out, {**gateway.envelope_base(TOOL), "base_url_hint": base_url,
                                  "summary": str(obj.get("summary") or ""), "auth_header_names": header_names,
                                  "changed_files": changed, "calls": calls, "dropped": dropped})
    print(f"  ✔ {len(calls)} smoke call(s) planned, {len(dropped)} dropped", flush=True)
    for d in dropped:
        print(f"    ⚠ dropped {d['id']}: {d['reason']}", flush=True)
    return 0


def _write_report(args: argparse.Namespace, **kw: Any) -> None:
    detected = kw["detected"]
    md = render_report(result=kw["result"], reason=kw["reason"], base_url=kw["base_url"], detected=detected,
                       calls=kw["calls"], dropped=kw["dropped"], auth_names=kw["auth_names"], missing=kw["missing"])
    redact = kw["redact"]
    Path(args.out_md).write_text(redact(md))
    counts = {"total": len(kw["calls"]), "pass": sum(c["result"] == "PASS" for c in kw["calls"]),
              "fail": sum(c["result"] == "FAIL" for c in kw["calls"]), "error": sum(c["result"] == "ERROR" for c in kw["calls"])}
    payload = {**gateway.envelope_base(TOOL), "result": kw["result"].lower(), "reason": kw["reason"],
               "base_url": kw["base_url"], "source": detected.get("source"), "state": detected.get("state"),
               "compose_service": detected.get("compose_service"), "started_at": detected.get("started_at"),
               "counts": counts, "calls": kw["calls"], "dropped": kw["dropped"], "auth_header_names": kw["auth_names"],
               "plan_path": str(args.plan)}
    gateway.write_json(args.out_json, json.loads(redact(json.dumps(payload))))


def _cmd_run(args: argparse.Namespace, cfg: config.Config) -> int:
    plan = _read_json(args.plan)
    if plan is None or not isinstance(plan.get("calls"), list):
        print(f"  ✘ smoke: no usable plan at {args.plan}", file=sys.stderr, flush=True)
        return 1
    detected = _read_json(args.detect) or {}
    base_url = str(args.base_url or detected.get("base_url") or plan.get("base_url_hint") or "").rstrip("/")
    if not is_loopback(base_url):
        print(f"  ✘ smoke: {base_url or '(no base URL)'} is not a loopback URL; nothing sent", file=sys.stderr, flush=True)
        return 1
    auth_headers, secrets, missing = expand_headers(cfg.get("smoke.headers") or {})
    redact = Redactor(secrets)
    auth_names = sorted((cfg.get("smoke.headers") or {}).keys())
    calls: list[dict] = [c for c in plan["calls"] if isinstance(c, dict)]
    dropped: list[dict] = [d for d in (plan.get("dropped") or []) if isinstance(d, dict)]
    common = dict(base_url=base_url, detected=detected, dropped=dropped, auth_names=auth_names, missing=missing, redact=redact)

    skip = None
    if detected.get("state") == "stale":
        skip = detected.get("reason") or "the running service is not the working tree"
    elif not calls:
        skip = "the plan has no calls"
    elif probe(base_url) is None:
        skip = f"no HTTP answer at {base_url}"
    if skip:
        _write_report(args, result="SKIPPED", reason=skip, calls=[], **common)
        print(f"  ⚠ smoke check skipped: {skip}", flush=True)
        return 3

    log_tail = _LogTail(Path(args.repo_root), detected, str(cfg.get("smoke.log_file") or ""))
    results = run_calls(base_url, calls, auth_headers=auth_headers, missing=missing,
                        timeout=int(cfg.get("smoke.timeout_seconds") or 5), log_tail=log_tail, redact=redact)
    bad = [r for r in results if r["result"] != "PASS"]
    if bad:
        result, reason = "FAIL", f"{len(bad)} of {len(results)} calls failed"
    else:
        result, reason = "PASS", ""
    _write_report(args, result=result, reason=reason, calls=results, **common)
    for r in results:
        mark = "✔" if r["result"] == "PASS" else "✘"
        print(f"  {mark} {r['method']} {r['path']} -> {r.get('status') or r['result']}"
              + (f"  {r['reason']}" if r.get("reason") else ""), flush=True)
    return 2 if bad else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="migite.smoke: Phase 3.2 API smoke check")
    ap.add_argument("--repo-root", default=None)
    sub = ap.add_subparsers(dest="command", required=True)
    d = sub.add_parser("detect", help="find the service under test and whether it runs the working tree")
    d.add_argument("--base-url", default=None)
    d.add_argument("--changed-files", default=None, help="file listing the changed paths, one per line")
    d.add_argument("--out", default=None, help="also write the JSON here")
    p = sub.add_parser("plan", help="one headless call: the calls to send, validated, as smoke-plan.json")
    p.add_argument("--diff", required=True, help="file holding the capped diff")
    p.add_argument("--testing-plan", default=None)
    p.add_argument("--detect", default=None, help="detect's JSON, for the base URL")
    p.add_argument("--base-url", default=None)
    p.add_argument("--changed-files", default=None)
    p.add_argument("--existing", default=None, help="a plan from an earlier round: reused, with no call, when the changed files match")
    p.add_argument("--out", required=True)
    r = sub.add_parser("run", help="send the planned calls and write the report")
    r.add_argument("--plan", required=True)
    r.add_argument("--detect", default=None)
    r.add_argument("--base-url", default=None)
    r.add_argument("--out-md", required=True)
    r.add_argument("--out-json", required=True)
    for s in (d, p, r):
        s.add_argument("--repo-root", dest="repo_root", default=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    args.repo_root = args.repo_root or os.getcwd()
    try:
        cfg = config.load(args.repo_root)
    except config.ConfigError as e:
        print(f"  ✘ smoke: config error: {e}", file=sys.stderr, flush=True)
        return 1
    if args.command == "detect":
        return _cmd_detect(args, cfg)
    if args.command == "plan":
        gateway.configure_from(cfg)
        return _cmd_plan(args, cfg)
    return _cmd_run(args, cfg)


if __name__ == "__main__":
    sys.exit(main())
