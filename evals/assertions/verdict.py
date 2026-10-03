"""promptfoo assertion: did the refuter judge the finding the way its label says?

A finding labeled true is right when CONFIRMED. One labeled false is right when REFUTED, and
half right when UNVERIFIABLE (it is demoted to a Note, so it no longer blocks). The named
scores are the rates to watch:

  false_refute   a true finding was REFUTED: it would be dropped from the review. The costly one.
  missed         a true finding was UNVERIFIABLE: it would be demoted to a Note.
  false_confirm  a false finding was CONFIRMED: the wrong Critical survives, the original problem.
"""

import re

VERDICT_RE = re.compile(r"^\s*VERDICT:\s*(CONFIRMED|REFUTED|UNVERIFIABLE)\b", re.I | re.M)


def verdict_of(output: str) -> str:
    m = VERDICT_RE.search(output or "")
    return m.group(1).upper() if m else "UNPARSEABLE"


def grade(label: bool, verdict: str) -> dict:
    scores = {"correct": 0.0, "false_refute": 0.0, "missed": 0.0, "false_confirm": 0.0}
    if label:
        if verdict == "CONFIRMED":
            score, reason = 1.0, "confirmed a real finding"
            scores["correct"] = 1.0
        elif verdict == "REFUTED":
            score, reason = 0.0, "refuted a REAL finding: it would be silently dropped"
            scores["false_refute"] = 1.0
        elif verdict == "UNVERIFIABLE":
            score, reason = 0.0, "could not confirm a real finding: it would be demoted to a Note"
            scores["missed"] = 1.0
        else:
            score, reason = 0.0, "the refuter's reply did not follow the required format"
    else:
        if verdict == "REFUTED":
            score, reason = 1.0, "refuted a wrong finding"
            scores["correct"] = 1.0
        elif verdict == "UNVERIFIABLE":
            score, reason = 0.5, "demoted a wrong finding to a Note: it no longer blocks"
            scores["correct"] = 0.5
        elif verdict == "CONFIRMED":
            score, reason = 0.0, "CONFIRMED a WRONG finding: it would still block the merge"
            scores["false_confirm"] = 1.0
        else:
            score, reason = 0.0, "the refuter's reply did not follow the required format"
    return {"pass": score >= 0.5, "score": score, "reason": f"{reason} ({verdict})", "namedScores": scores}


def get_assert(output, context):
    result = grade(str(context["vars"]["label"]).lower() == "true", verdict_of(output))
    # Turns (one per tool call, plus the answer) average per provider: the cost of a prompt, next to its accuracy.
    meta = (context.get("providerResponse") or {}).get("metadata") or {}
    if isinstance(meta.get("turns"), (int, float)):
        result["namedScores"]["turns"] = float(meta["turns"])
    return result
