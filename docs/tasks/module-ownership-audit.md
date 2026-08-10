# RESTScope Module Naming and Ownership Audit

Status: Implemented and verified

Subsequent change: ADR 0003 retires the temporary Operation Smoke and named
Agent Modules that this audit intentionally left in place. Counts and dependency
observations below describe the recorded audit baseline, not the current tree.

Baseline commit: `9f3e50e55f48a7fda85d9d4e7f89304efa33989c`

## Objective

Audit every production Python module in RESTScope for naming clarity, domain
ownership, Interface depth, dependency direction, public-facade size, and
beginner readability, then implement the approved staged route without changing
the Main Agent's product behavior or the temporary named Agent workflows.

The audit uses three complementary methods:

- **Codebase design:** apply the deletion test, prefer deep Modules, and place
  each Interface at the seam that gives callers leverage and maintainers
  locality.
- **Domain modeling:** distinguish canonical domain terms from implementation
  containers, and avoid names such as `catalog`, `runtime`, or `capability`
  when the owner cannot be inferred from the qualified name.
- **Two-axis review:** report repository Standards separately from alignment
  with the approved Main Agent, Skill, Tool, and Harness design. Fowler-style
  smells are judgment calls rather than hard violations.

## Scope and evidence

The production inventory at the baseline contains:

- 199 Python files under `restscope/`;
- 45,005 physical source lines;
- 214 source-level imports crossing top-level RESTScope packages, representing
  76 distinct package-to-package directions;
- 15 package facades with explicit `__all__` declarations;
- 11 Python files directly under `restscope/`.

Every production file was parsed and classified. Tests were inspected as
executable evidence for current Interfaces and consumers, but test-file naming
is not a refactoring target. Historical task records were used only when a
newer ADR, project rule, test, or implementation had not superseded them.

The temporary Failure Resolution, Parameter Patch, Patch Review, and Compact
Agent implementations are not near-term refactoring targets. Their imports are
included when describing the current graph, but recommendations do not tidy
their internals before the approved Skill and generic Subagent migrations.

No real model, target API, MCP server, Phoenix service, or other external
system was called.

## Implementation outcome

The completed source tree contains 228 production Python files and 44,183
physical source lines. The higher file count is intentional: broad owners were
split into focused subject packages while total production size remained
effectively flat. Source inspection now finds 231 cross-top-level imports in 66
distinct directions, down from 76 directions at the baseline, and the complete
top-level production graph has no strongly connected component containing more
than one package.

The root contains only `__init__.py`, `app.py`, and `config.py`. A subprocess
test proves that importing `restscope` preserves root logger handlers and
creates no files. The root facade exports exactly `RESTScopeApp` and
`RESTScopeConfig`.

Implemented ownership changes include:

- `operation_references`, `target_http`, `openapi_audit`, and
  `request_generation` as explicit domain packages;
- `harness/operation_testing` for deterministic Batch execution, Probe evidence,
  and run-local Test Cases;
- App-owned Behavior Monitor, Operation Testing, target transport, and OpenAPI
  Audit collaborators rather than Harness-held domain services;
- Agent-owned narrow Tool-execution and tree-control ports;
- Tool-owned Worklist, Parameter-candidate, and Test Case backend contracts;
- domain-adjacent SQLAlchemy Adapters and Units of Work;
- grouped Behavior Monitor subjects and split Live Observer internals;
- behavior-specific OpenAPI input, response, and observed-field queries plus
  private shared traversal/projection;
- behavior-specific Test Case Parameter, response-field, and Failure query
  modules plus shared validation and bounded presentation;
- removal of the audited generated boilerplate paragraphs, leaving local
  domain descriptions as the maintained documentation contract;
- `SkillCatalog`, `OpenAPIToolBackend`, `ResourceToolBackend`, and
  `RequestGenerationConfigStore` terminology with no compatibility aliases.

The temporary Failure Resolution, Parameter Patch, Patch Review, and Compact
Agents retain their current runtime behavior and deterministic compilation,
sampling, candidate, finalization, and persistence boundaries.

## Executive assessment

RESTScope has several strong, deep Modules. `AgentContext`, the OpenAPI parser,
request and response semantic references, Tool contract validation, Generator
and Constraint evaluation, and the standard Skill loader all hide substantial
rules behind focused Interfaces. Their implementation size alone is not a
reason to split them.

At the audited baseline, the largest ownership problem was composition direction
rather than file size. `agent`, `api_behavior_monitor`, `db`, `harness`,
`operation_smoke`, and `tools` formed one strongly connected component.
Removing the temporary Operation Smoke roles and their dedicated Tools still
left a long-term five-package component: `agent`, `api_behavior_monitor`, `db`,
`harness`, and `tools`. The principal causes were:

- `Agent` depends on the concrete Tool toolbox while the Subagent Tool depends
  on Agent completion contracts;
- `HarnessRuntime` stores Behavior Monitor and Operation Testing objects in
  addition to mechanical Agent runtime state;
- domain factories construct SQLAlchemy Adapters, while SQLAlchemy Adapters
  correctly depend back on domain records and ports;
- some Tool Bindings import concrete Harness or workflow stores instead of
  accepting a narrow injected backend;
- App-owned OpenAPI audit operations are reached through a chain of Harness
  and Behavior Monitor implementation attributes.

The implementation changed those directions as well as the vocabulary; the
current graph result is recorded in Implementation outcome above.

## Baseline Standards review

The S1–S7 findings below preserve the evidence recorded before implementation.
Their present-tense wording describes that fixed baseline, not the completed
tree summarized above.

### S1. Package import has filesystem and process-wide side effects

**Hard standards issue.** `restscope.__init__` imports logging configuration and
calls `setup_logging()`. That import loads the environment-backed global
`CONFIG`, replaces root logger handlers, creates the log directory, and opens a
log file. This contradicts the facade docstring's claim that focused imports do
not eagerly bootstrap the application.

**Proposal:** remove global `CONFIG`, configure logging only after an App has an
explicit `RESTScopeConfig`, and make importing `restscope` free of filesystem
and root-logger mutation.

### S2. Beginner-readable documentation is present syntactically but often empty semantically

**Hard standards issue.** Static inspection found 126 instances across 34
production files of generated phrases such as “the class owns any required
collaborators” or “the annotated arguments and return type define the data
boundary.” These phrases do not explain the actual argument meaning, result,
state transition, failure mode, or safety constraint required by `AGENTS.md`.

**Proposal:** replace them package by package while touching the affected
Module. Do not run a mechanical docstring generator or mix the complete cleanup
into an unrelated behavior change.

### S3. The code-reading guide contains retired runtime paths

**Hard standards issue.** `docs/code-reading-guide.md` still directs readers to
the deleted `restscope/harness/run.py`, skips a step number, and presents
temporary Operation Smoke internals as a continuation of the current blocking
Main entry even though the production Main currently has no testing Skills.

**Proposal:** describe the current App path first, then place the focused legacy
workflows in a clearly labeled migration section.

### S4. HarnessRuntime has divergent reasons to change

**Judgment call — possible Divergent Change.** `HarnessRuntime` owns mechanical
Agent startup and context binding, but it also stores Operation Testing,
Behavior Monitor, target HTTP, OpenAPI query, resource query, and external Tool
objects. A change to any of these domains can require editing Harness state.

**Proposal:** App composition owns domain objects. Harness owns lifecycle,
authorization, Context, Tool Binding, model execution, cancellation, budgets,
tracing, and cleanup of its own resources.

### S5. App reaches through a message chain for OpenAPI audit

**Judgment call — Message Chain and Feature Envy.** App initialization and
export navigate from Harness to Behavior Monitor to Contract Tracker to its
`catalog`. OpenAPI initialization and export are App concerns, yet the App must
know three internal ownership hops.

**Proposal:** App and `ResponseContractTracker` receive the same explicit
OpenAPI Audit Module; App calls its Interface directly.

### S6. Root bootstrap is a shallow Middle Man

**Judgment call — Middle Man.** `bootstrap.py` contains two pass-through
builders. One has no production consumer; the other is used only by `app.py`.
Deleting the module removes indirection rather than redistributing meaningful
complexity.

**Proposal:** delete it and keep one private composition path inside `app.py`.

### S7. Wide facades encourage Shotgun Surgery

**Judgment call — Shotgun Surgery.** The root facade exports 15 names and the
`tools` facade exports 35, including subject-specific Tool names, Specs, and
backends. Adding or renaming one Tool can require editing its implementation,
subject facade, root Tool facade, and built-in Catalog.

**Proposal:** root `restscope` exposes only the application entry and its
configuration. Root `restscope.tools` exposes only global Tool runtime and
Catalog concepts; callers import subject behavior from
`restscope.tools.<subject>`.

**Standards summary:** 7 findings. The worst structural issue is the long-term
core dependency cycle; the most immediate correctness issue is process-wide
work performed by a package import.

## Baseline Spec review

The SP1–SP3 findings below likewise describe the fixed audit baseline. The
implementation outcome records how their ownership directions were changed.

The governing specification is the accepted Main Agent and Harness direction
in `AGENTS.md`, ADR 0001, ADR 0002, and
`docs/agent-rules/source-and-decisions.md`.

### Aligned behavior

- The product entry starts one blocking generic Main Agent and does not restore
  the retired FIFO or Run Harness DTOs.
- Main, Subagent, Skill, Tool, Profile, Context, and Harness remain explicit
  runtime concepts rather than class-per-role inheritance.
- Standard Skills are package data discovered without granting access.
- Tool Catalog membership remains distinct from Profile authorization.
- Current named Failure Resolution, Compact, Parameter Patch, and Review Agents
  remain explicit temporary exceptions rather than templates for new roles.
- No audited Module introduces persistent Plans, queues, conversations, or
  speculative Agent memory.

### SP1. Harness contains domain objects beyond its approved mechanical role

`HarnessRuntime.operation_testing_service` and
`HarnessRuntime.api_behavior_monitor_coordinator` expose domain collaborators
through the mechanical runtime. They also let App and Tool assembly discover
state by attribute navigation.

### SP2. Some Tool implementations know concrete workflow storage

The Worklist, Parameter candidate, Test Case, and operation-scoped HTTP Probe
Tool modules import concrete workflow or Harness state. This weakens the rule
that Tool Modules own the model contract while Harness or workflows inject
state. The Worklist and Parameter cases are migration debt and should be
removed with their temporary workflow rather than polished now. Shared Test
Case and target HTTP bindings should eventually receive narrow backends.

### SP3. Current documentation overstates the available Main workflow

The code-reading guide's combined step sequence can be read as if Main already
invokes Operation Smoke. The approved production Main has only the Plan Tool
pair and cannot yet test an API.

**Spec summary:** 3 deviations. The worst is domain state exposed through the
mechanical Harness; the approved Main lifecycle and authorization model
otherwise remain intact.

## Root-directory ownership decisions

The root directory should be an application-facing entry, not a holding area
for cross-package utilities. After the proposed migrations it contains only
`__init__.py`, `app.py`, and `config.py`.

| Current file | Evidence and deletion test | Proposed disposition |
| --- | --- | --- |
| `__init__.py` | Real App-facing facade, but currently exports unrelated domain and logging names. | Keep; remove import side effects and expose only `RESTScopeApp` and `RESTScopeConfig`. |
| `app.py` | Unique product composition and lifetime owner; deleting it redistributes substantial startup and cleanup rules. | Keep at the root as requested; merge its duplicate default-construction paths internally. Do not create a `restscope.app` package. |
| `restscope_config.py` | Shared typed composition input used by App, DB, Monitor, and transitional workflows. | Rename to `config.py`; keep `RESTScopeConfig` at the root-level configuration seam. |
| `bootstrap.py` | Two shallow builders, with only one production caller. | Delete; App performs the one default composition path. |
| `operations.py` | `OperationReference` has no production or test consumer beyond facade exposure. | Delete without a compatibility alias. Do not confuse it with the proposed semantic Operation Reference term. |
| `http_transport.py` | Seven production consumers share target URL safety, request preparation, bounded response handling, observation, and transport behavior. | Move to `target_http/`; separate request preparation, transport, and observation internals behind one subject facade. |
| `logging_config.py` | Process-wide observability setup; its current dependency on global config causes import work. | Move to `observability/logging.py`; accept explicit logging settings and path. Delete `get_logger`. |
| `redaction.py` | All production consumers are App or Observability; other Modules receive it through `TracingRuntime`. | Move to `observability/redaction.py` and export `Redactor` from the Observability facade. |
| `randomness.py` | Reproducible values serve Generator behavior; App only establishes the root seed. | Move with the future `request_generation` domain as `request_generation/randomness.py`. |
| `request_inputs.py` | Three production consumers use one deep semantic request-input grammar. | Move to `operation_references/request.py`. |
| `response_fields.py` | Five production consumers use one deep handle/selector conversion grammar. It belongs to neither Parser, Monitor, nor Tools alone. | Move to `operation_references/response.py`. |

The new `operation_references` facade exposes exactly:

- `RequestInputLocation`;
- `RequestInputReference`;
- `ResponseFieldReference`.

The package name deliberately avoids `api_references`, which would be confused
with OpenAPI `$ref`. A private shared path implementation is justified only if
request and response behavior can share it without conditional flags or a
larger Interface. Moving both Modules does not require deduplicating their
different parsing rules.

## Top-level package disposition

| Package | Assessment | Proposed ownership action |
| --- | --- | --- |
| `agent` | Correct core noun and deep runtime Interface. | Keep. Replace concrete toolbox/tree dependencies with Agent-owned narrow Protocols when cutting cycles. |
| `api_behavior_monitor` | Correct long-term domain, but internally flat across three distinct evidence areas. | Keep facade; group response contracts, resource identifiers, and response values internally. |
| `builtin_skills` | Correct package-data owner and intentionally has no Python facade surface. | Keep unchanged. |
| `catalog` | Not orphaned, but the bare name hides that it stores normalized OpenAPI audit/export state. | Rename to `openapi_audit`; rename `OpenAPICatalog` to `OpenAPIAudit`. |
| `context` | Small public Interface hiding substantial bounding and encoding behavior. | Keep. Size of `writer.py` alone is not a split reason. |
| `db` | Appropriate infrastructure owner, but its facade and shared Unit of Work file span many domains. | Keep package; place each SQLAlchemy Repository and Unit of Work together by domain and shrink the facade. |
| `harness` | Correct mechanical runtime owner. | Keep and narrow. Split Generator domain language from operation execution. |
| `llm` | Clear provider/model contract owner. | Keep. Qualified `schemas`, `runtime`, and `config` names are sufficiently precise. |
| `observability` | Correct owner of tracing, live projections, logging, and redaction. | Keep; absorb logging/redaction and split `live.py` internally. |
| `openapi_parser` | Clear and cohesive Parser/IR/document owner. | Keep. Do not split deep Parser Modules only because they are large. |
| `operation_smoke` | Transitional focused workflow still used by tests and internal callers. | Defer internal restructuring until standard Skills and generic Subagents replace it. |
| `skills` | Clear standard runtime-definition owner. | Keep; consider `SkillCatalog` in place of `SkillRegistry` so the public builder and returned type agree. |
| `tools` | Correct global Tool contract owner, but its root facade and several subject files are too wide. | Keep; shrink root facade and deepen subject Modules. |
| `ui` | Small, precise loopback observer Adapter. | Keep. |

## Large-module assessment

Line count is evidence for inspection, not an automatic split rule.

| Module | Lines | Assessment and proposal |
| --- | ---: | --- |
| `api_behavior_monitor/resource_identifier.py` | 1,499 | Tracker orchestration, evidence extraction, grouping, selector validation, and prompt preparation change for different reasons. Split inside a `resource_identifiers` subpackage while keeping one tracker Interface. |
| `tools/openapi/lookup.py` | 1,416 | Owns five separate model Tools plus schema traversal and presentation. Split by Tool behavior, with private shared schema projection. |
| `observability/live.py` | 1,360 | Owns observer state, span projection, HTTP exchanges, and presentation helpers. Split those internals while preserving `LiveRunObserver`. |
| `operation_smoke/failure_resolution/agent.py` | 1,193 | Temporary named Agent. Record only; do not restructure before migration. |
| `harness/testing/generation.py` | 1,046 | Core Generator implementation with a focused external Interface, but its owner is mixed with execution. Move to `request_generation`; split further only around proven internal seams. |
| `operation_smoke/parameter_patch/coordinator.py` | 1,004 | Temporary Patch flow. Record only; do not restructure before migration. |
| `harness/testing/constraints.py` | 987 | A deep recursive DSL and evaluator. Move ownership with Request Generation; keep the complete DSL local rather than fragmenting by node type. |
| `harness/testing/catalog.py` | 908 | Mutable current Generator configuration rather than definition discovery. Move and rename to `RequestGenerationConfigStore`. |
| `openapi_parser/document_builder.py` | 906 | One deep conversion Interface with cohesive normalization knowledge. Keep unless a concrete change reveals independent reasons to vary. |
| `harness/testing/snapshot.py` | 903 | Request-generation schema freezing and default strategy selection. Move to Request Generation. |
| `api_behavior_monitor/response_value.py` | 843 | Move into the response-values subdomain; separate extraction helpers only if tests can remain at the tracker Interface. |
| `db/repositories/resource_catalog_repo.py` | 812 | Large concrete Adapter. First colocate its Unit of Work; do not split query methods merely to reduce lines. |
| `app.py` | 753 | Correct root owner but contains duplicate default composition. Keep the public class and consolidate one private construction path. |
| `harness/testing/constraint_solver.py` | 699 | Cohesive solver Implementation. Move with Request Generation and retain a small solve Interface. |
| `api_behavior_monitor/coordinator.py` | 650 | Its name is justified because it sequences contract, resource, and response-value Modules. Keep the coordinator thin after internal grouping. |
| `http_transport.py` | 645 | Multiple target-HTTP responsibilities justify the proposed `target_http` package. |
| `openapi_parser/ir.py` | 610 | Qualified collection of related immutable IR records. Keep. |
| `context/writer.py` | 565 | Deep bounded Markdown writer. Keep. |
| `harness/agent_runtime.py` | 562 | Cohesive Profile graph resolution and Agent assembly, but some Harness-owned Bindings can become private collaborators after cycle removal. |

## Implemented staged route

The user approved these stages as one structural implementation after first
committing the pre-existing working-tree change. Git commit, merge, and cleanup
for this feature remain separately gated.

### Phase 0 — restore truthful and side-effect-free entrypoints

1. Correct the code-reading guide's current Main path and migration section.
2. Remove global configuration and package-import logging setup.
3. Delete `operations.py`, `OperationReference`, and `bootstrap.py`.
4. Shrink the root facade to `RESTScopeApp` and `RESTScopeConfig`.
5. Replace boilerplate documentation in bounded package batches.

### Phase 1 — organize root-level ownership

1. Create `operation_references` and move request/response semantic references.
2. Create `target_http` and move target-specific HTTP behavior.
3. Move logging and redaction into Observability.
4. Rename `restscope_config.py` to `config.py`.
5. Keep `app.py` at the root and consolidate one private composition path.

### Phase 2 — establish explicit OpenAPI audit ownership

1. Rename `catalog` to `openapi_audit` and `OpenAPICatalog` to
   `OpenAPIAudit`.
2. Construct one audit Module in App composition.
3. Inject it into `ResponseContractTracker` and retain it directly on App.
4. Remove App access through Harness and Monitor implementation attributes.

### Phase 3 — cut long-term core dependency cycles

1. Reduce `HarnessRuntime` to mechanical state and lifecycle.
2. Let Tool Binding factories capture App-owned domain backends.
3. Define narrow Tool-execution and tree-control Protocols at the Agent seam.
4. Replace concrete Tool-to-Harness state imports with Tool-owned backend
   Protocols or bounded callbacks.
5. Move database construction out of domain factories and into App
   composition.
6. Regenerate the source-level dependency graph after every replacement.

### Phase 4 — separate Request Generation from operation execution

1. Create `request_generation` for Generator strategies, Constraint DSL,
   compilation, solving, snapshots, serialization, configuration ports, and
   deterministic randomness.
2. Rename `GeneratorConfigCatalog` to `RequestGenerationConfigStore`.
3. Keep Batch execution and run-local Test Case state under a precise Harness
   operation-testing package.
4. Revise the governing rule and ADR so domain language belongs to Request
   Generation while Harness owns deterministic execution lifecycle.

### Phase 5 — deepen subject packages and vocabulary

1. Shrink the root Tool facade and split OpenAPI/Test Case Tools by behavior.
2. Rename `OpenAPICapability` to `OpenAPIToolBackend` and
   `ResourceIdentifierCapability` to `ResourceToolBackend`.
3. Group Behavior Monitor internals and Live Observer internals.
4. Colocate each SQLAlchemy Repository and Unit of Work by domain.
5. Add OpenAPI Audit, Operation Reference, Catalog, Store, Registry, Backend,
   and Coordinator to `CONTEXT.md` after their implementation.
6. Rename `SkillRegistry` to `SkillCatalog`.

No compatibility aliases are proposed. RESTScope is exploratory, and parallel
old/new names would obscure the single source of truth.

## Public Interface changes

The implementation intentionally:

- remove root exports other than `RESTScopeApp` and `RESTScopeConfig`;
- replace `restscope.restscope_config` with `restscope.config`;
- replace `restscope.request_inputs` and `restscope.response_fields` with
  `restscope.operation_references`;
- replace `restscope.http_transport` with `restscope.target_http`;
- replace `restscope.catalog.OpenAPICatalog` with
  `restscope.openapi_audit.OpenAPIAudit`;
- replace `SkillRegistry` with `SkillCatalog`;
- replace broad Tool capability names with subject-specific Tool backend names.

The changes are atomic across production, tests, current documentation,
package data, and package-boundary assertions. Historical task records retain
their original terminology unless a short supersession note is necessary.

## Verification strategy

Final implementation verification:

```text
uv run pytest -q tests/test_workflow_package_boundaries.py \
  tests/test_tools_catalog.py tests/test_agent_profile.py \
  tests/test_builtin_skill_loader.py
uv run python -m compileall -q restscope tests
git diff --check
```

Also verify Markdown links, wheel contents, import side effects, facade exports,
retired paths, and the top-level production dependency graph.

Every future structural stage adds tests for its exact seam:

- root-directory allowlist and exact facade exports;
- importing `restscope` does not read configuration, create files, or replace
  root logger handlers;
- old modules and compatibility aliases are absent;
- subject facades expose only approved Interfaces;
- package dependency directions remain acyclic after temporary workflow edges
  are excluded;
- focused behavior tests, full `pytest`, `compileall`, wheel contents, and
  import smoke checks pass.

## Verification results

Fresh verification on the completed feature tree produced:

- `uv run pytest -q`: 821 passed, 3 skipped after installing the repository's
  evaluation dependency group;
- the four planned Profile, Skill, Tool Catalog, and package-boundary test
  modules: 44 passed;
- `uv run python -m compileall -q restscope tests evaluations`: passed;
- `uv run --group evaluation python -m evaluations list`: passed and listed
  all three checked-in Resolution scenarios without contacting Phoenix;
- `git diff --check`: passed;
- wheel build and content inspection: passed, including both packaged standard
  Skills and all renamed/split packages, with no retired root or package paths;
- current-source terminology and retired-path scans: no matches;
- root allowlist, side-effect-free import, exact facades, dependency acyclicity,
  and generated-docstring checks: enforced by the passing boundary tests.
- one independent Standards/Spec review: no remaining actionable findings.

No real model, target API, MCP server, Phoenix service, or other external
system was contacted.

## Preserved boundaries

- No new compatibility alias, persistence record, DTO, Agent, Profile, Skill,
  Tool, Context Source, scheduler, or external dependency is introduced.
- No temporary named Agent flow is cleaned up ahead of its approved migration.
- No real model, target API, MCP server, Phoenix service, or other external
  system is called during verification.
