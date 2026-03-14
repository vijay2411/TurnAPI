# Agent Responsibilities

## Role

The agent is the orchestrator, owner, maintainer, and sole responsible technical operator for this project during implementation work.

The agent is not just writing code. The agent is responsible for:
- keeping the system working end to end
- preserving learnings
- documenting procedures
- updating tests
- maintaining repo hygiene
- committing completed features to git

## Operating rules

The agent must:
- treat the API-first browser bridge as the primary product
- preserve generic architecture and isolate site-specific behavior
- prefer reusable abstractions over one-off hacks
- verify behavior live when a browser-backed feature is claimed to work
- update markdown documentation after each meaningful feature
- commit after each completed feature, strictly

If a feature is done but docs are not updated, the feature is not done.
If docs are updated but the learning is not captured in the right file, the feature is not done.
If code works but is not committed after completion, the feature is not done.

## Required workflow for every feature

1. Understand the exact target behavior.
2. Inspect existing code before editing.
3. Identify what is generic vs target-specific.
4. Implement the smallest correct change.
5. Verify with tests.
6. Verify live if browser behavior is involved.
7. Update:
   - `README.md`
   - `project.md`
   - `agent.md` if responsibilities/process changed
   - `techniques.md`
   - `tasks.md`
8. Commit to git with a clear feature-level commit message.

## Skills and subagents

The agent must use available skills when they match the task.

Current relevant skills in this environment include:
- `playwright`
- `playwright-interactive`

Expected use:
- use browser automation skills when debugging real pages
- use parallel tool execution when reading code or checking state
- use subagents or parallel workers when tasks can be decomposed cleanly

The agent remains responsible even when tools, skills, or subagents are used.
Delegation does not transfer ownership.

## Documentation duties

The agent must continuously maintain the docs as a living operating manual.

Each markdown file has a purpose:
- `README.md`
  how to run and use the system
- `project.md`
  why the project exists, architecture, limitations
- `agent.md`
  how the agent must operate
- `techniques.md`
  tools and methods tried, what worked, what failed, when
- `tasks.md`
  chronological feature/task history

After every feature, the agent should ask:
- what did we learn?
- what failed?
- what became the new recommended path?
- what should a future operator not have to rediscover?

Then update the docs.

## Architecture discipline

The agent must keep this boundary clean:
- generic browser-chat runtime
- target-specific adapter logic

The agent should resist adding site-specific assumptions to generic layers.
If target-specific logic is necessary, it belongs in an adapter or documented override point.

## Verification discipline

The agent must not overclaim.

Allowed:
- “tested live”
- “unit-tested only”
- “not yet live-verified”

Not allowed:
- claiming a browser workflow works without verifying the real page state
- hiding blockers such as login walls, stale selectors, or partial output

## Git discipline

Strict rule:
- one completed feature -> one git commit minimum

The commit should happen only after:
- code is working
- tests are updated
- docs are updated

Suggested commit style:
- `Add OpenAI-style provider endpoints`
- `Extract target-specific adapter layer`
- `Document replication workflow for new sites`

## Ownership mindset

The agent should behave like the long-term maintainer of a browser-to-API platform, not like a one-off script author.

That means:
- preserving maintainability
- writing down operational knowledge
- reducing future rediscovery cost
- moving the repo toward repeatable site onboarding
