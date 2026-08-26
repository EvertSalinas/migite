#!/usr/bin/env python3
# migite_paths — shared run-directory resolver
#
# Answers "given an optional Jira id and/or a free-text name, which vault
# folder does this run belong in" for migite-audit, migite-pr-review, and
# migite-explore. Centralises detect_org(), detect_base_branch(), and
# slugify(), which used to be duplicated across those scripts.

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

TICKET_RE = re.compile(r"^[A-Za-z]+-\d+$")
ATLASSIAN_BROWSE_RE = re.compile(r"/browse/([^/?#]+)", re.IGNORECASE)


class InvalidRunKeyError(Exception):
    """Raised when a --id or --name value can't be turned into a safe slug."""

    def __init__(self, raw: str):
        self.raw = raw
        super().__init__(raw)


class AmbiguousRunDirError(Exception):
    """Raised when a prefix match against --id finds more than one sibling."""

    def __init__(self, slug: str, candidates: list[str]):
        self.slug = slug
        self.candidates = candidates
        super().__init__(f"Ambiguous run folder for '{slug}': {', '.join(candidates)}")


def slugify(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s-]", "", text)
    text = re.sub(r"[\s-]+", "-", text).strip("-")
    return text[:50].rstrip("-")


def detect_org(repo_root: str) -> str:
    """The vault "org" bucket a repo files under.

    MIGITE_ORG wins if set. Otherwise it's the name of the directory that
    directly contains the repo (~/Code/Acme/foo -> "Acme"), so it works for
    any org/client name without hardcoding one. Falls back to "Personal"
    only when the repo has no meaningful parent (e.g. sitting at "/").
    """
    env_org = os.environ.get("MIGITE_ORG")
    if env_org:
        return env_org
    return Path(repo_root).resolve().parent.name or "Personal"


def detect_base_branch(repo_root: str) -> str:
    """origin/HEAD if set; else whichever of main/master/develop exists locally."""
    remote_head = subprocess.run(
        "git symbolic-ref --short -q refs/remotes/origin/HEAD", shell=True,
        capture_output=True, text=True, cwd=repo_root,
    ).stdout.strip()
    if remote_head:
        return remote_head.removeprefix("origin/")
    for branch in ("main", "master", "develop"):
        result = subprocess.run(
            f"git show-ref --verify --quiet refs/heads/{branch}", shell=True, cwd=repo_root,
        )
        if result.returncode == 0:
            return branch
    return "main"


def extract_ticket_key(raw: str) -> str:
    s = raw.strip()
    m = ATLASSIAN_BROWSE_RE.search(s)
    if m:
        s = m.group(1)
    if not TICKET_RE.match(s):
        raise InvalidRunKeyError(raw)
    return s.upper()


def resolve_run_dir(vault_base: str, org: str, repo: str, id: str = None, name: str = None) -> Path | None:
    if not id and not name:
        return None

    if id:
        slug = slugify(extract_ticket_key(id))
        prefix_ok = True
    else:
        slug = slugify(name)
        prefix_ok = False

    if not slug:
        raise InvalidRunKeyError(id if id else name)

    base_dir = Path(vault_base) / org / repo
    if not base_dir.is_dir():
        return base_dir / slug

    siblings = [p for p in base_dir.iterdir() if p.is_dir()]

    exact = [p for p in siblings if p.name.lower() == slug.lower()]
    if exact:
        return exact[0]

    if prefix_ok:
        prefix_matches = [p for p in siblings if p.name.lower().startswith(slug.lower() + "-")]
        if len(prefix_matches) == 1:
            return prefix_matches[0]
        if len(prefix_matches) > 1:
            raise AmbiguousRunDirError(slug, sorted(p.name for p in prefix_matches))

    return base_dir / slug


# ── Self-check ──────────────────────────────────────────────────────────────

def _load_blueprint_slugify():
    """migite-blueprint.py isn't importable by name (hyphenated filename) and
    is intentionally left with its own unreconciled slugify copy — see plan
    step 1's note. Loaded here only to diff behaviour, not to enforce parity."""
    import importlib.util
    bp_path = Path(__file__).parent / "migite-blueprint.py"
    if not bp_path.exists():
        return None
    spec = importlib.util.spec_from_file_location("migite_blueprint_ref", bp_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.slugify


def run_self_test() -> None:
    import tempfile

    failures = []

    def check(label, cond):
        if cond:
            print(f"  ✔ {label}")
        else:
            print(f"  ✘ {label}")
            failures.append(label)

    # extract_ticket_key
    check("extract_ticket_key: raw key", extract_ticket_key("BB-3370") == "BB-3370")
    check("extract_ticket_key: raw key lowercase", extract_ticket_key("bb-3370") == "BB-3370")
    check(
        "extract_ticket_key: Atlassian URL, uppercase key",
        extract_ticket_key("https://yourcompany.atlassian.net/browse/BB-3370") == "BB-3370",
    )
    check(
        "extract_ticket_key: Atlassian URL, lowercase key",
        extract_ticket_key("https://yourcompany.atlassian.net/browse/bb-3370") == "BB-3370",
    )
    for bad in ("#", "BB_3370", ""):
        try:
            extract_ticket_key(bad)
            check(f"extract_ticket_key: invalid input {bad!r} raises", False)
        except InvalidRunKeyError:
            check(f"extract_ticket_key: invalid input {bad!r} raises", True)

    # detect_org
    check(
        "detect_org: parent directory name, any org",
        detect_org("/Users/x/Code/Acme-client/foo") == "Acme-client",
    )
    check(
        "detect_org: parent directory name works for any label, not just known orgs",
        detect_org("/Users/x/Code/Evert/foo") == "Evert",
    )
    prev_env_org = os.environ.pop("MIGITE_ORG", None)
    try:
        os.environ["MIGITE_ORG"] = "Freelance"
        check(
            "detect_org: MIGITE_ORG env override wins over parent dir",
            detect_org("/Users/x/Code/Acme-client/foo") == "Freelance",
        )
    finally:
        if prev_env_org is None:
            os.environ.pop("MIGITE_ORG", None)
        else:
            os.environ["MIGITE_ORG"] = prev_env_org

    # detect_base_branch
    with tempfile.TemporaryDirectory() as base_tmp:
        subprocess.run("git init -q", shell=True, cwd=base_tmp)
        subprocess.run("git config commit.gpgsign false", shell=True, cwd=base_tmp)
        subprocess.run("git commit --allow-empty -q -m init", shell=True, cwd=base_tmp)
        subprocess.run("git branch -m master", shell=True, cwd=base_tmp)
        check(
            "detect_base_branch: no origin/HEAD, local master -> master",
            detect_base_branch(base_tmp) == "master",
        )
        subprocess.run("git checkout -q -b main", shell=True, cwd=base_tmp)
        check(
            "detect_base_branch: no origin/HEAD, main takes priority over master",
            detect_base_branch(base_tmp) == "main",
        )

    # resolve_run_dir
    check("resolve_run_dir: no id/no name -> None", resolve_run_dir("/vault", "Org", "repo") is None)

    with tempfile.TemporaryDirectory() as tmp:
        vault_base, org, repo = tmp, "Org", "repo"
        repo_dir = Path(vault_base) / org / repo

        # New ticket, vault dir doesn't exist yet
        result = resolve_run_dir(vault_base, org, repo, id="BB-1111")
        check("resolve_run_dir: new ticket, empty vault dir -> new path", result == repo_dir / "bb-1111")

        repo_dir.mkdir(parents=True)
        (repo_dir / "audit-2026-08-01.md").write_text("")  # flat file sibling
        (repo_dir / "bb-2222").mkdir()
        (repo_dir / "BB-2222-ward-metadata-outbox").mkdir()

        # Exact match preferred over prefix match
        result = resolve_run_dir(vault_base, org, repo, id="bb-2222")
        check(
            "resolve_run_dir: exact match preferred over prefix sibling",
            result == repo_dir / "bb-2222",
        )

        # Flat file sibling never satisfies a match
        result = resolve_run_dir(vault_base, org, repo, name="audit-2026-08-01")
        check(
            "resolve_run_dir: flat file sibling never matches",
            result is not None and not result.exists(),
        )

        # Genuinely ambiguous siblings
        (repo_dir / "bb-3333-foo").mkdir()
        (repo_dir / "bb-3333-bar").mkdir()
        try:
            resolve_run_dir(vault_base, org, repo, id="bb-3333")
            check("resolve_run_dir: ambiguous siblings raise", False)
        except AmbiguousRunDirError as e:
            check(
                "resolve_run_dir: ambiguous siblings raise with both names",
                "bb-3333-foo" in e.candidates and "bb-3333-bar" in e.candidates,
            )

        # No match among siblings -> new path
        result = resolve_run_dir(vault_base, org, repo, id="BB-4444")
        check("resolve_run_dir: no match among siblings -> new path", result == repo_dir / "bb-4444")

        # --name is exact-match-only: a hyphen-prefix of an existing folder does not match
        (repo_dir / "add-users-auth-scoping").mkdir()
        result = resolve_run_dir(vault_base, org, repo, name="add-users")
        check(
            "resolve_run_dir: --name is exact-match-only (no prefix match)",
            result == repo_dir / "add-users",
        )

    # slugify
    long_text = "jira ticket bb-3370 please fetch context from the discovery doc and figure it out"
    check(
        "slugify: 50-char truncation never leaves a trailing hyphen",
        not slugify(long_text).endswith("-"),
    )

    bp_slugify = _load_blueprint_slugify()
    if bp_slugify is not None:
        bp_result = bp_slugify(long_text)
        my_result = slugify(long_text)
        note = "same" if bp_result == my_result else f"differs (blueprint={bp_result!r}, ours={my_result!r})"
        print(f"  · slugify parity vs migite-blueprint.py: {note} (informational only)")
    else:
        print("  · migite-blueprint.py not found — skipping slugify parity check")

    print()
    if failures:
        print(f"{len(failures)} check(s) FAILED:")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print("All checks passed.")


# ── CLI ───────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="migite_paths: shared run-directory resolver")
    parser.add_argument("--self-test", action="store_true", help="Run the built-in self-check")
    sub = parser.add_subparsers(dest="command")

    p_detect = sub.add_parser("detect-org", help="Print the detected org for a repo root")
    p_detect.add_argument("--repo-root", required=True)

    p_base = sub.add_parser("detect-base-branch", help="Print the detected base branch for a repo root")
    p_base.add_argument("--repo-root", required=True)

    args = parser.parse_args()

    if args.self_test:
        run_self_test()
        return

    if args.command == "detect-org":
        print(detect_org(args.repo_root))
        return

    if args.command == "detect-base-branch":
        print(detect_base_branch(args.repo_root))
        return

    parser.print_help()
    sys.exit(1)


if __name__ == "__main__":
    main()
