---
name: explore-api-behavior
description: Explore an initialized REST API by finding reproducible happy paths, including resource identifiers from different observed states, then performing exceptional testing with replay-confirmed Bug evidence. Use when the Task Executor must choose happy_path or exceptional Batches, expand useful positive Generator candidates through a Patch Subagent, and maximize distinct confirmed API bugs without persisting a test plan.
---

# Explore API Behavior

Keep the private Plan small and evidence-driven. OpenAPI describes possible
behavior; only executed Batch outcomes and final Oracle assessments prove it.

1. List operations and choose a safe order. Prefer read-only discovery before
   mutations that can create resource IDs or state needed by later operations.
2. Call `test_case.run_batch` with `test_mode="happy_path"`. A 2xx result is a
   happy-path reward for the positive candidates that actually participated.
3. When an input lacks working values, inspect its state and exact response or
   resource evidence. Use `resolve-operation-failures` for diagnosis; delegate
   a confirmed Generator repair to the Patch child. A Patch may install one to
   eight complete positive candidates for an input.
4. Exercise resource identifiers from meaningfully different current resource
   states when evidence makes those states available. Do not invent or persist
   a separate state-level arm; the resource Generator selects complete current
   instances at Batch time.
5. After a useful happy path is found—or sooner when exceptional evidence is
   already worthwhile—call `test_case.run_batch` with
   `test_mode="exceptional"`. Happy-path discovery is guidance, not a gate.
6. Exceptional Batches mechanically split between a negative Generator and an
   ignored Constraint. Treat skipped slots as unavailable actions, not evidence.
   A Bug counts only when `bug_found` is true after replay confirmation.
7. Use returned Bug categories, requests, responses, and Batch queries to avoid
   equivalent probes. Continue while a different operation, state, candidate,
   or Constraint can reasonably add coverage.
8. Finish with the happy paths found, replay-confirmed Bug categories and Batch
   evidence, skipped or blocked areas, and remaining uncertainty.

Never persist Agent reasoning, queues, plans, candidate statistics, or generated
samples. Never treat a 2xx exceptional response as a Bug unless the returned
final Oracle verdict says so.
