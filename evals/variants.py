"""Alternative "how to work" blocks for the refuter prompt (migite/verify.py HOW_TO_WORK), so a
change can be measured against the same labeled findings before it goes into migite.

`default` is what migite ships. A provider selects one with `config: {variant: <name>}`."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from migite import verify  # noqa: E402

FOCUSED = """How to work:
1. Open the cited file and read the code around the cited line. Do not trust the claim's description of the code.
2. Trace the claim to the code that would make it true. Resolve names the way Ruby and Rails do: constant
   lookup through the enclosing modules (a class inside `module A::B` finds `A::B::Foo` before a top-level
   `Foo`), inheritance and ancestors, concerns and includes, default scopes, callbacks, Pundit and Rails conventions.
3. Ask what would make THIS claim false, given what it asserts (a definition the reviewer missed, a base class
   or concern that already provides it, an existing guard, a spec that already covers it), and check only that.

Stop as soon as one line you have traced decides the verdict. Do not corroborate it with further searches."""

HOW = {"default": verify.HOW_TO_WORK, "focused": FOCUSED}


def apply(name: str | None) -> None:
    """Make `name` the refuter prompt's how-to-work block for this process."""
    name = name or "default"
    if name not in HOW:
        raise SystemExit(f"unknown prompt variant {name!r}; known: {', '.join(HOW)}")
    verify.HOW_TO_WORK = HOW[name]
