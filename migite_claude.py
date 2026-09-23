#!/usr/bin/env python3
"""migite_claude - compatibility shim. The module is now `migite_call`.

`import migite_claude` returns the migite_call module itself (not a copy), so
setting a global such as `migite_claude.BACKEND` still affects every call.
The old names `call_claude` and `ClaudeError` remain as aliases. Running this
file as a script (`migite_claude.py run|summary|field|extract`) runs
migite_call's CLI unchanged, so an old `~/.local/bin` symlink keeps working.

New code should import migite_call and use call_agent / AgentError.
"""

import sys

import migite_call

migite_call.call_claude = migite_call.call_agent
migite_call.ClaudeError = migite_call.AgentError

if __name__ == "__main__":
    migite_call.main()
else:
    sys.modules[__name__] = migite_call
