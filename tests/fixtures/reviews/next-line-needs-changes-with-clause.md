# Review: RSVP config schema constants

Date: 2026-07-01

## Brakeman: PASS
## Rubocop: PASS
## Tests: PASS (18 examples)

## Verdict

**NEEDS CHANGES** — two blocking items:

1. Constant is redefined in two initializers
2. Serializer exposes `internal_token`
