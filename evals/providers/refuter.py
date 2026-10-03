"""promptfoo provider: migite's refuter on one golden finding (see ../run_refuter.py).

Options (promptfooconfig.yaml, `config:`): `model` pins the refuter's model for this provider,
and `variant` picks a prompt from ../variants.py, so the same findings can be run against several
models or prompts side by side. Without them the model comes from your migite config for the
`refute` role and the prompt is the one migite ships."""

import json
import subprocess
import sys
from pathlib import Path

RUNNER = Path(__file__).resolve().parent.parent / "run_refuter.py"
TIMEOUT = 1200


def call_api(prompt, options, context):
    v = context["vars"]
    entry = {k: v[k] for k in ("id", "severity", "location", "title", "problem")}
    payload = {"repo": v["repo"], "ref": v["ref"], "entry": entry, "model": (options.get("config") or {}).get("model"),
               "variant": (options.get("config") or {}).get("variant")}
    try:
        proc = subprocess.run([sys.executable, str(RUNNER)], input=json.dumps(payload), capture_output=True,
                              text=True, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        return {"error": f"the refuter timed out after {TIMEOUT}s"}
    if proc.returncode != 0:
        return {"error": (proc.stderr or proc.stdout)[-1500:]}
    try:
        data = json.loads(proc.stdout)
    except ValueError:
        return {"error": f"the runner printed something that is not JSON: {proc.stdout[-500:]}"}
    return {"output": data["raw"], "cost": data.get("cost_usd") or 0,
            "metadata": {"model": data.get("model"), "turns": data.get("turns"), "variant": data.get("variant")}}
