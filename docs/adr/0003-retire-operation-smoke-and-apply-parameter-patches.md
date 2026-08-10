---
status: accepted
---

# Retire Operation Smoke and apply Parameter Patches through generic Tools

Operation Smoke and its specialized Failure Resolution, Parameter Patch,
Patch Review, Compact, candidate, Finalizer, Memory, and evaluation Modules are
retired without compatibility aliases. The six Generator/Smoke database tables
are also removed. Reusable diagnosis and Patch methodology belongs in standard
Skills used by generic Agents and Subagents.

Request Generation owns one revisioned, mutable, non-persistent configuration
Store for each initialized OpenAPI operation. A Batch freezes one complete
revision before generation. `request_generation.validate_patch` compiles and
samples a complete semantic replacement without mutation, and
`parameter_patch.apply` is the sole Tool that may atomically change the Store.
Application revalidates the exact request, registers response-value sources,
checks the validation digest, and advances the revision or changes nothing.

`apply-parameter-patch` replaces `build-parameter-patch`. Its method includes
state inspection, complete Patch construction, deterministic validation,
value-level semantic review, application, and post-application confirmation.
`resolve-operation-failures` consumes bounded inline Batch evidence and may
delegate a fixed repair to an authorized child Profile selecting that Skill.
There is no candidate `P*` registry, Worklist, Test Case registry, persistent
Failure memory, automatic rollback, or specialized Agent fallback.

This decision refines [ADR 0002](0002-main-agent-owns-testing-decisions.md):
deterministic Batch execution remains a Harness responsibility, while generation
state and Patch mechanics belong to Request Generation and Tool Modules. Main
and child LLMs continue to own diagnosis, Skill choice, semantic review,
delegation, retry, and stopping decisions. The initial production Main Profile
remains plan-only, so the new bindings do not activate testing authority.

Patch application changes only RESTScope's future request generation. It does
not send HTTP or prove a target Failure resolved. A later complete Batch supplies
new target evidence, and a failed Batch does not roll back the applied revision.
