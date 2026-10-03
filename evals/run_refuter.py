#!/usr/bin/env python3
"""Run migite's refuter on one finding, the way a live review does, and print the result as JSON.

Reads {"repo", "ref", "entry", "model"?, "variant"?} on stdin. The repository is extracted at `ref` and the
call runs with that tree as its working directory, so the refuter's Read/Grep/Glob see the code
as it was when the finding was raised. This is its own process because the working directory is
per process: the promptfoo provider can run several of these at once.
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import common  # noqa: E402
import variants  # noqa: E402
from migite import config, gateway, verify  # noqa: E402

CONTEXT = "\nThe code under review is in the repository working tree.\n"


def main() -> None:
    req = json.load(sys.stdin)
    variants.apply(req.get("variant"))
    tree = common.checkout(req["repo"], req["ref"])
    os.chdir(tree)
    gateway.configure_from(config.load(str(tree)))
    gateway.require_cli()

    finding = verify.parse_findings(common.finding_block(req["entry"]))[0]
    prompt = verify.refute_prompt(finding, "ok", "", CONTEXT)
    res = gateway.call_agent(prompt, verify.REFUTE_ROLE, tool="migite-eval", label=f"eval:{req['entry']['id']}",
                             model=req.get("model") or None)
    parsed = verify.parse_verdict(res.text)
    json.dump({
        "raw": res.text,
        "verdict": parsed[0] if parsed else None,
        "model": res.usage.model,
        "cost_usd": res.usage.cost_usd,
        "turns": res.usage.turns,
        "variant": req.get("variant") or "default",
    }, sys.stdout)


if __name__ == "__main__":
    main()
