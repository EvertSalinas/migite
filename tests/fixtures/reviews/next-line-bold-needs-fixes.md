# Review: RSVP answer serializers

Date: 2026-07-14

## Brakeman: PASS
## Rubocop: PASS
## Tests: PASS (44 examples)

## Checklist
- [x] Implementation matches the approved plan
- [ ] No N+1 queries
- [x] No raw SQL without parameterization

## Issues found
- 🔴 Critical — `answers.each { |a| a.question.title }` loads questions lazily

## Verdict

**NEEDS FIXES**

Fix required before commit: add `includes(:question)` to the scope.
