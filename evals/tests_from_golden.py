"""promptfoo test generator: one test per labeled finding in golden/findings.yaml."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import common  # noqa: E402


def generate_tests(config=None):
    tests = []
    for e in common.load_golden():
        if e.get("label") is None or not e.get("ref"):
            continue
        tests.append({
            "description": e["id"],
            "vars": {
                "id": e["id"], "repo": str(common.expand(e["repo"])), "ref": e["ref"],
                "severity": e["severity"], "location": e["location"], "title": e["title"],
                "problem": e["problem"], "label": "true" if e["label"] else "false",
            },
        })
    return tests
