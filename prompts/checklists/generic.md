# Generic checklists

<!-- What migite-review (Phase 3), migite-pr-review and migite-audit check on the generic stack and
     on any stacks.<name> profile without a checklist of its own. Language- and framework-neutral on
     purpose. Give a profile its own checks with checklists/<name>.md in prompts.dir: its sections
     replace these, the rest are kept. Format: docs/configuration.md#checklists. -->

Expertise: software

## review: correctness
Implementation matches the approved plan and its amendments (an amendment supersedes the plan where they conflict, and plan.md text an amendment superseded is not a finding). No scope creep. All acceptance criteria covered. Logic is correct. No dead code or commented-out blocks.

## review: security
Every entry point that touches user data is authorised, the way the codebase already does it. Data is scoped to the user or account allowed to see it. No queries or remote calls inside a loop over a collection (N+1). No raw SQL or string-built queries: parameterised queries or the ORM / query builder. No hardcoded secrets, tokens or credentials. User input is validated before use, and never reaches a shell, eval, a template rendered unescaped, or a deserializer of untrusted data.

## review: test_coverage
New public functions and methods have unit tests. New endpoints or entry points have tests covering success, unauthorized, and invalid input. The codebase's factories or builders are used where it has them. No real network calls in tests: external services are stubbed. Tests describe behaviour, not implementation.

## review: testing_plan
The testing plan document exists and is complete: a setup script that creates the data the steps need (generic emails only), step-by-step verification actions (commands, requests or UI steps, each with what should happen), log lines to grep, and a teardown script. A missing, placeholder, or empty testing plan is a critical failure. If this task had any amendments, the testing plan must reflect the CURRENT amended behaviour, not just the original plan - flag any step that still describes pre-amendment behaviour.

## pr_review: correctness
- Logic errors or bugs in the implementation
- Edge cases not handled (null or missing values, empty collections, boundary conditions)
- Incorrect conditionals, off-by-one errors, or flipped logic
- Functions that can return unexpected types or null
- Unintended behaviour changes - side effects beyond the PR's stated scope

## pr_review: security
- Entry points that touch user data without the codebase's authorization check
- Records or collections not scoped to the current user or account
- Queries or remote calls inside a loop over a collection (N+1)
- Raw SQL or string-built queries instead of parameterised queries or the ORM / query builder
- User input used without validation, or passed to a shell, eval, or an unsafe deserializer
- Sensitive data exposed in responses or log output
- Hardcoded secrets, tokens, or credentials

## pr_review: test_coverage
- New public functions or methods without unit tests
- New endpoints without tests covering success, unauthorized, and invalid input
- New models or records without the factory or builder the codebase uses
- Tests that call real external services instead of stubbing them
- Tests that assert implementation details rather than behaviour

## pr_review: conventions_and_migrations
- Code that doesn't follow the patterns in adjacent files (structure, naming, error handling)
- Lint violations in the diff (see the lint log)
- Schema or data migrations that aren't reversible, or that add a required column to an existing table with no default or backfill
- Foreign keys without an index
- Background jobs or workers that aren't safe to retry
- New dependencies added without a stated reason

## audit: security
Files: **/*.py **/*.js **/*.jsx **/*.ts **/*.tsx **/*.go **/*.java **/*.kt **/*.rs **/*.php **/*.cs **/*.swift **/*.rb
- Entry points that touch user data with no authorization check
- Data not scoped to the user or account allowed to see it
- Raw SQL or string-built queries
- User input reaching a shell, eval, a template rendered unescaped, or a deserializer
- Hardcoded secrets, tokens, or credentials

## audit: data_access
Files: **/*.py **/*.js **/*.jsx **/*.ts **/*.tsx **/*.go **/*.java **/*.kt **/*.rs **/*.php **/*.cs **/*.swift **/*.rb
- Queries or remote calls inside loops (N+1)
- Queries that can return unbounded result sets
- Multiple writes that must succeed together, outside a transaction
- Missing indexes for the lookups the code makes

## audit: error_handling
Files: **/*.py **/*.js **/*.jsx **/*.ts **/*.tsx **/*.go **/*.java **/*.kt **/*.rs **/*.php **/*.cs **/*.swift **/*.rb
- Errors caught and swallowed silently
- Retries or background work that isn't idempotent
- Remote calls with no timeout
- Failures logged without the context needed to act on them

## audit: design
Files: **/*.py **/*.js **/*.jsx **/*.ts **/*.tsx **/*.go **/*.java **/*.kt **/*.rs **/*.php **/*.cs **/*.swift **/*.rb
- Modules or classes doing more than one thing
- Business logic inside request handlers or UI code
- Functions with no clear return contract
- Duplicated logic that has already drifted apart

## audit: tests
Files: **/test_*.py **/*_test.py **/*_test.go **/*.test.* **/*.spec.* **/*_spec.rb **/*Test.java **/*Test.kt
- Tests that call real external services
- Tests that assert implementation details rather than behaviour
- Important paths (authorization, invalid input) with no test

## refute
How to work:
1. Open the cited file and read the code around the cited line. Do not trust the claim's description of the code.
2. Trace the claim to the code that would make it true. Resolve names the way this language and framework do:
   imports and module scope, inheritance and mixins, decorators and middleware, and the framework conventions
   the codebase relies on.
3. Look for what contradicts the claim: a definition the reviewer missed, a base class or helper that already
   provides it, a guard clause elsewhere, a test that covers it.
