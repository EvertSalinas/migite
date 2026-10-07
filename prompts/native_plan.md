# Native plan — explore the repository yourself, then write the plan

You are running in your CLI's read-only plan mode, with its normal tools. Nobody has explored
the codebase for you: there are no exploration reports in this prompt. Read the task below, then
explore the repository with your own subagents before you write anything.

## How to explore

- Run explore subagents in parallel, one per area the task touches. For a Rails app those are
  usually models, controllers, services, serializers, specs, migrations and schema, routes and
  config; when the task may touch the frontend, also views, components, helpers and the Stimulus
  or Turbo JavaScript. For another stack, split by its own layers.
- Start from the files the branch already changed against its base, then follow the task's own
  words to the files that matter: the classes, methods, routes and specs the change will touch,
  and the existing code it must follow as a pattern.
- Read every file you will name in the plan. Never name a file, class or method you have not seen.
- When the plan relies on what a gem or library can or cannot do, read its source rather than
  inferring it from which of its methods the app happens to call.

Where the plan format below says "exploration reports", it means what you and your subagents
read. The instruction that you have no tool access does not apply in this mode; everything else
in it does.

## What you must not do

- Do not create, edit or delete any file, and do not ask to leave plan mode. Migite writes your
  final message to `plan.md` itself.
- Do not end with a summary of the plan, a question, or an offer to implement it. Your final
  message is the complete plan document and nothing else.

## One extra section

After every section the plan format asks for, end the document with this section:

```
## Files examined
<One line per file you or your subagents read: `path`, then what it showed that matters to this
plan. Every file and method the plan names appears here. The plan refiner checks the
architecture critic's findings against this section, so state what the code contains, not what
the plan proposes to change.>
```
