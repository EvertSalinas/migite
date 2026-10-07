# Rails checklists

<!-- What migite-review (Phase 3), migite-pr-review and migite-audit check on the rails stack.
     Override any section from prompts.dir: put checklists/rails.md there with only the sections
     you change. Format: docs/configuration.md#checklists. -->

Expertise: Rails

## review: correctness
Implementation matches the approved plan and its amendments (an amendment supersedes the plan where they conflict, and plan.md text an amendment superseded is not a finding). No scope creep. All acceptance criteria covered. Logic is correct. No dead code or commented-out blocks.

## review: security
All controller actions authorised. Resources scoped to current_user. No N+1 queries (check .each over AR collections). No raw SQL without parameterisation. No hardcoded secrets. Strong params on every action. No SQL injection vectors. In views: no user-supplied content passed through html_safe, raw or <%== %>. Turbo Stream broadcasts (broadcasts_to, broadcast_*_to, turbo_stream_from) are scoped to the user or account allowed to see them, never a shared stream name that carries private data.

## review: test_coverage
New public methods have unit specs. New endpoints have request specs covering success, 401, 422. Actions that respond with turbo_stream have request specs asserting the <turbo-stream> action and target. Factories used (not fixtures). No real HTTP calls in specs. Spec descriptions use 'when/with/without' for context blocks.

## review: testing_plan
The testing plan document exists and is complete: contains a real Rails console seed script (generic emails only), step-by-step verification actions (curl or browser steps; for UI changes, the page, the exact action and what should change on the page), log lines to grep, and a teardown script. A missing, placeholder, or empty testing plan is a critical failure. If this task had any amendments, the testing plan must reflect the CURRENT amended behaviour, not just the original plan — flag any step that still describes pre-amendment behaviour.

## review: frontend
Hotwire and Stimulus correctness in the changed views and JavaScript. Form submissions that fail validation render with status :unprocessable_entity (422), and redirects after a successful non-GET use :see_other (303); otherwise Turbo won't show the errors or follow the redirect. Every turbo_frame_tag id and turbo_stream target the diff references exists in the rendered markup, with dom_id used the same way on both sides. Stimulus controllers are registered (importmap pin or controllers/index.js), and data-controller, data-<name>-target, data-<name>-value and data-action names match the controller's file name and its static targets/values. No inline <script> or on* attribute handlers. Partials rendered per row don't query per row (N+1 in collection rendering). Frontend lint problems that remain (log below) are warnings unless they break the page.

## pr_review: correctness
- Logic errors or bugs in the implementation
- Edge cases not handled (nil values, empty collections, boundary conditions)
- Incorrect conditionals, off-by-one errors, or flipped logic
- Methods that can return unexpected types or nils
- Unintended behaviour changes — side effects beyond the PR's stated scope

## pr_review: security
- Controller actions not covered by Pundit policy or manual authorization check
- Collections or records not scoped to current_user / current_account
- N+1 queries introduced by the PR (associations loaded inside loops or serializers)
- Raw SQL without ActiveRecord parameterisation
- Actions missing strong params or accepting raw params
- Sensitive data exposed in serializer or log output
- Hardcoded secrets, tokens, or credentials

## pr_review: test_coverage
- New public methods without unit specs
- New endpoints without request specs covering success, 401 (unauthorized), and 422 (invalid input)
- Context blocks that don't start with 'when', 'with', or 'without' (RSpec convention)
- Missing factory definitions for new models or associations
- Tests that call real external services instead of stubbing them
- Specs that assert implementation details rather than behaviour

## pr_review: conventions_and_migrations
- Business logic inline in controller actions (belongs in a service object)
- Rubocop-flagged violations in the diff
- Migrations that: add a NOT NULL column without a default or data migration, lack `down` / reversible
- Foreign keys in migrations without a corresponding `add_index`
- Background jobs that are non-idempotent or lack an explicit queue
- Rails anti-patterns: find instead of find_by!, rescue Exception, memoization with ||= on falsy values

## audit: models
Files: app/models/**/*.rb
- N+1 risks: associations loaded lazily inside loops or serializers
- Callbacks with side effects (API calls, jobs enqueued) that break idempotency
- Missing `dependent:` on has_many (orphan records risk)
- Scopes that can return unbounded result sets
- Validations that silently fail in bulk operations

## audit: controllers
Files: app/controllers/**/*.rb
- Actions missing authorization (Pundit policy, CanCanCan, or manual check)
- Collections or records not scoped to current_user / current_account
- Business logic inline in action bodies (belongs in a service)
- Actions accepting raw params — missing strong params
- Before-action filters with `:only`/`:except` that can be bypassed

## audit: services
Files: app/services/**/*.rb app/interactors/**/*.rb app/commands/**/*.rb
- Multiple DB writes outside a transaction (partial write risk)
- Exceptions rescued and swallowed silently
- God objects — services doing more than one thing
- Methods with no clear return value / result object contract

## audit: serializers
Files: app/serializers/**/*.rb
- Associations accessed without eager loading (N+1 in serializer)
- Sensitive fields exposed (tokens, internal IDs, password digests)
- Attributes that bypass authorization checks

## audit: jobs
Files: app/jobs/**/*.rb app/workers/**/*.rb
- Non-idempotent perform method (retrying changes state incorrectly)
- Heavy business logic inline in perform (should delegate to a service)
- Missing explicit queue_as
- Jobs that fan out more jobs without deduplication
- Raised exceptions not logged before re-raise

## audit: migrations
Files: db/migrate/*.rb
- Irreversible operations in `change` with no `up`/`down`
- NOT NULL column added to existing table with no default or data migration
- Foreign key added without a corresponding index
- Destructive operations (column drops, renames) missing a phased deployment plan

## audit: schema_indexes
Files: db/schema.rb
- Foreign key columns (ending in _id) without a matching index
- Columns likely used in .where / .order (status, type, state, role, created_at) without indexes
- Polymorphic type+id pairs missing a composite index
- Unique constraint candidates (email, token, slug) missing a unique index
