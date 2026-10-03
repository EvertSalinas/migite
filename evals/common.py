"""Helpers shared by the eval scripts: the golden file, locations, and repo checkouts."""

import hashlib
import re
import subprocess
import tarfile
import io
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
MIGITE_ROOT = HERE.parent
GOLDEN = HERE / "golden" / "findings.yaml"
CACHE = HERE / ".cache" / "trees"


def load_golden(path: Path | None = None) -> list[dict]:
    path = path or GOLDEN
    if not path.is_file():
        return []
    return yaml.safe_load(path.read_text()) or []


def save_golden(entries: list[dict], path: Path | None = None) -> None:
    path = path or GOLDEN
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(entries, sort_keys=False, allow_unicode=True, width=100))


def expand(repo: str) -> Path:
    return Path(repo).expanduser()


def split_location(location: str) -> tuple[str, int | None]:
    """`path:12` or `path:12-20` -> (path, 12); a bare path -> (path, None)."""
    m = re.fullmatch(r"(.+?)(?::(\d+)(?:-\d+)?)?", location.strip())
    return (m.group(1), int(m.group(2)) if m.group(2) else None) if m else (location, None)


def git(repo: Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, errors="replace")
    return r.stdout.strip() if r.returncode == 0 else ""


def show_file(repo: Path, ref: str, path: str) -> str | None:
    r = subprocess.run(["git", "-C", str(repo), "show", f"{ref}:{path}"], capture_output=True, text=True, errors="replace")
    return r.stdout if r.returncode == 0 else None


def checkout(repo: str, ref: str) -> Path:
    """The repository's files at `ref`, extracted once under evals/.cache (git archive: the
    repo's own working tree and branches are never touched). Returns the directory."""
    root = expand(repo)
    sha = git(root, "rev-parse", "--verify", f"{ref}^{{commit}}")
    if not sha:
        raise RuntimeError(f"{ref} is not a commit in {root}")
    dest = CACHE / f"{root.name}-{hashlib.sha1(str(root).encode()).hexdigest()[:6]}-{sha[:12]}"
    if dest.is_dir():
        return dest
    archive = subprocess.run(["git", "-C", str(root), "archive", sha], capture_output=True)
    if archive.returncode != 0:
        raise RuntimeError(f"git archive failed for {ref} in {root}: {archive.stderr.decode(errors='replace')[:200]}")
    tmp = dest.with_name(dest.name + ".part")
    tmp.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as tar:
        tar.extractall(tmp, filter="data")
    tmp.rename(dest)
    return dest


def finding_block(entry: dict) -> str:
    """A golden entry as the finding block migite's reviewers produce, so it goes through
    the same parser and refuter prompt as a live one."""
    mark = {"critical": "🔴 **Critical**", "warning": "🟡 **Warning**"}.get(entry.get("severity", "critical"), "🔴 **Critical**")
    return f"- {mark} · `{entry['location']}` · {entry['title']}\n  - **Problem:** {entry['problem']}"
