# Findings: Agent Tool Runtime Simplification

## Current Evidence

- The default capability runtime registers HTTP and OpenAPI tools, optionally
  Resource Lookup and MCP tools, in one App-wide Registry.
- Production Agents do not use `ToolSelector`; Dedup and Solve manually choose
  specs and dispatch private tools.
- `ToolPolicy.state` is unused, and the existing policy allows the high-risk
  raw HTTP tool for every role.
- `ToolCallValidator` checks registration and policy but does not validate the
  advertised input schema. `ToolExecutor` does not validate output schemas.
- Tool registration silently replaces specs and can retain an old handler.
- Local handlers receive the complete App ToolContext even when they do not
  need IR, target address, or authentication headers.
- Solve already scopes the shared HTTP implementation to the current operation
  and projects raw responses through the run-local Test Case Catalog.
- Dedup manually restricts a broad OpenAPI lookup to the current operation.
- Dedup currently accepts one tool call; Solve accepts grouped read-only calls
  but executes them sequentially and its Parameter Memory handler mutates
  session bookkeeping during execution.
- The default App registers Resource Lookup, but no production Agent exposes it
  to a model.

## Implementation Constraints

- Preserve the user's unrelated GitLab OpenAPI and live-test changes in the
  main worktree.
- Production modules, public Interfaces, and non-trivial helpers require
  beginner-readable docstrings and intent comments.
- Historical task records remain historical evidence; update current docs
  rather than rewriting old completed records.
- No live network or model verification is authorized.

## Phase 1 discovery

- Runtime validation must consume arbitrary JSON Schema because MCP input
  contracts are not necessarily generated from RESTScope Pydantic models.
- `jsonschema` 4.26.0 is already present in `uv.lock` as a transitive
  dependency, but RESTScope does not declare it directly. Importing it from
  production code therefore requires an explicit direct dependency decision.
- `TracingRuntime` already exposes the App-owned `Redactor`, and `TraceSpan`
  can record a redacted unexpected exception event without returning that text
  to the model. Tool-specific trace inputs still need preservation because the
  scoped HTTP Probe deliberately traces operation identity rather than raw
  model request values.
- Dedup's current constructor receives the entire global `ToolExecutor`; its
  test factory must build and bind a complete capability runtime only to expose
  OpenAPI lookup. The approved scoped design can instead bind the current IR
  operation and run-local Catalog when `deduplicate` starts.
- Dedup's existing correction helper rejects any response containing more than
  one tool call. The Agent loop already appends one provider-required tool
  result per call, so the public behavior can change vertically without a
  generic reasoning-loop abstraction.
- App composition currently builds Operation Smoke before `initialize` binds
  the target OpenAPI IR. The final scoped OpenAPI tool therefore needs an
  explicit late-bound operation provider or a narrower construction-time
  change; it cannot simply capture an `OperationIR` in the current factory.
- The Catalog tool already has a strict Pydantic input schema and a bounded
  structured result, but it currently performs its own tracing, validation,
  and `ToolResult` construction. Migration should retain only Catalog query
  semantics and let `AgentToolbox` own the mechanical boundary.
- App composition currently uses `ToolExecutor` for two unrelated jobs: an
  executable global registry and the once-bound target/OpenAPI context store.
  Removing the global registry requires moving only the latter lifecycle into
  `CapabilityRuntime` or another existing App-owned seam.
- The HTTP implementation itself already owns `TargetHTTPTransport`; its only
  call-time dependency is the bound `ToolContext`. The scoped Probe can keep
  binding exactly that explicit dependency without making every tool receive
  it.
- The HTTP regression suite was coupled to the deleted global Executor even
  though its real subject is the target-bound implementation. A small
  test-local Agent toolbox preserves schema/error/redaction coverage while
  asserting the new explicit dependency boundary.
- MCP annotations previously drove a central risk classifier, but no Agent used
  that selector in production. MCP can preserve its discovered input/output
  contracts and source identity while Agent composition decides whether to
  include the tool at all.
