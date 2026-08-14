---
status: accepted
---

# Grant bounded database evidence queries to planning and execution

This decision narrowly supersedes the no-Tool/no-Skill Orchestrator clauses in
[ADR 0007](0007-orchestrator-ledger-long-tasks.md). ADR 0007's Ledger ownership,
fresh System Agent roots, no-child Orchestrator, Task dispatch, Replan,
completion, and non-persistence decisions remain accepted.

## Decision

RESTScope exposes one `database.query` Tool for parameterized, bounded,
read-only SQL against the current App-owned SQLite evidence database. The Tool
uses SQLite's authorizer and progress handler to deny mutations, schema or
connection-lifecycle actions, extensions, multiple statements, and over-time
queries. It returns positional rows under fixed row, cell, BLOB, and total
output limits. It neither accepts an external connection nor adds persistence.

Complete `observations.response_headers` may be read only as one direct output
column whose non-null values exactly match stored complete header mappings. The
Tool then redacts sensitive header values. Derived or mixed header projections
are rejected because arbitrary SQL does not preserve enough provenance for
name-based redaction. `observations.response_body` and
`resource_instances.current_state_json` remain readable with ordinary output
limits and no content redaction.

The standard `query-restscope-database` Skill selects among seven directly
linked query-purpose References. The Loader validates and retains those files
at startup, while `file.read` reveals only the Reference selected for the
current question.

The Orchestrator and Task Executor Profiles grant `database.query`, `file.read`,
and `query-restscope-database`. The Orchestrator keeps `test-progress` as its
sole automatic Context Source and default coverage summary; it queries durable
rows only for narrower evidence needed by planning, Replan, or completion. It
still has no child Profile, test-execution Tool, target mutation capability, or
hidden history. Parameter Patch, Resource Identifier, and Resource State
Profiles receive no database-query grant.

## Consequences

- Database rows are audit evidence, not a plan, queue, scheduler, recovery
  snapshot, or authorization to act on the target API.
- Catalog registration and Harness Binding do not grant access; exact Profile
  names remain the sole permission source.
- Model context may receive bounded raw response-body or Resource-instance
  content. This is an explicit local evidence tradeoff and must not silently
  expand to an external database or unbounded export.
- Query methodology evolves in the Skill and its References. SQLite execution
  safety and projection remain canonical in the database Tool Module.
