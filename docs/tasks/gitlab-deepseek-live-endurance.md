# GitLab and DeepSeek Live Endurance Verification

Status: Stopped at the approved three-hour deadline; incomplete

## Objective

Run the current RESTScope Orchestration workflow against the authorized local
GitLab API using the configured real DeepSeek Provider. Keep investigating and
rerunning until the workflow completes without a product bug or unexpected
exception and its logs, Phoenix traces, and Live Observer UI agree, or until
three hours of attempts have elapsed.

## Authorized scope

- Real DeepSeek API calls and their normal cost.
- Real requests, including mutations and messages, against the disposable local
  `gitlab-test` target only.
- Local Phoenix export/query and Live Observer browser inspection.
- Local diagnosis, focused fixes, offline regression, and repeated live runs.
- Local commits already separately authorized by the user; no remote push.

## Safety and stop conditions

- Never print or commit the DeepSeek or GitLab credentials.
- Do not target public GitLab or a repository outside the disposable local
  container.
- Attempt window: `2026-08-14T08:27:45Z` through
  `2026-08-14T11:27:45Z`.
- Success requires clean workflow completion plus matching runtime, log, trace,
  database, and UI evidence. A green narrow test or a still-running process is
  insufficient.
- At the deadline, interrupt normally, retain available evidence, close owned
  RESTScope resources, and report any remaining mismatch truthfully.

## Current evidence

- Local `gitlab-test` container: healthy, exposed at `http://127.0.0.1:7077`.
- Local Phoenix container: running at `http://127.0.0.1:6006`.
- DeepSeek model catalog and secret: configured locally and ignored by Git.
- GitLab API credential: acquired only in memory from the disposable container
  and never printed or written to tracked artifacts.
- Current App, Agent, UI, database, and Phoenix paths were exercised by eleven
  real runs. The final run retained evidence for four of the five scoped
  operations but did not reach DELETE or clean workflow completion.

## Verification ledger

Final live execution result:
- The first current-architecture run reached DeepSeek and exposed a real Beta
  strict-schema incompatibility before any target request. A regression test
  now keeps unsupported strict JSON Schema on the standard DeepSeek endpoint
  while preserving the complete local Tool validation contract.
- Runs two through five established and repaired mixed strict projection and
  bounded missing-reasoning recovery. The current offline baseline is 732
  passed, 2 skipped, with Ruff and diff checks clean before the sixth run.
- The sixth run remained healthy at the Provider/App boundary but its Patch
  child could not name a private multipart request-body ancestor. A faithful
  red regression now passes after limiting explicit scope enforcement to
  semantic handles that the Agent can actually supply. The next run must
  verify this through the real child/Tool path and then continue to end-to-end
  completion.
- The seventh run verified that compile behavior and progressed to a real POST
  repair Patch. It found the matching projection defect: a private expanded
  ancestor was still treated as public while constructing
  `final_generators`. The same regression now crosses both compile and Tool
  output projection; the fresh offline baseline is 733 passed, 2 skipped with
  Ruff and diff checks clean.
- The eighth run verified both Patch fixes and applied the POST repair. Its
  later Task Executor continuation still exhausted eight missing-reasoning
  attempts, so the Provider guard remained terminal and no unsafe Tool call was
  executed. The user then rejected `medium` as a redundant alias: Task Executor
  is directly `high`, while Parameter Patch remains `low`. Fresh verification
  of this final effort vocabulary precedes the next live run.
- The ninth run used the short-lived medium configuration and was stopped
  normally as soon as the user superseded it. The final direct high/low Profile
  state passes 733 tests with 2 skips, Ruff, and diff checks.
- The tenth run reached a real POST 201 after two applied repair Patches, then
  stopped on the recurring missing-reasoning condition in the following Task.
  Retry reminders now appear as final Provider-only user corrections at the
  Tool continuation boundary; no rejected call can execute or enter Agent
  history. The offline baseline remains 733 passed, 2 skipped with all 39
  DeepSeek tests, Ruff, and diff checks clean.
- The eleventh run used the final direct `high` Task Executor and `low`
  Parameter Patch Profiles for 69 minutes without another terminal missing-
  reasoning failure. It retained 8 Batches, 42 Observations, and 24 resource
  instances.
- Successful target evidence covered POST (three 201), collection GET (three
  200), item GET (twenty 200), and PUT (six 200). DELETE was not scheduled.
  The remaining PUT generator work produced three 400 responses and one local
  overlong-evidence rejection before the Agent entered a long sequence of safe
  unknown-input corrections.
- Full Phoenix pagination returned 1,188 spans (1,140 OK / 48 ERROR). Every
  ERROR was attributable to correctable Tool input, rejected Patch validation,
  bounded request evidence, or deadline cancellation; no unexpected stack
  trace or rejected Tool execution was observed.
- Live Observer stayed responsive with no console warnings during the run. At
  `2026-08-14T11:27:45Z`, the approved deadline was reached before clean
  completion. The runner was interrupted normally, exited 130, recorded
  `interrupted`, closed the UI, and retained SQLite and Phoenix evidence.
- Final fresh offline verification passed Ruff, the complete suite (`733
  passed, 2 skipped`), `git diff --check`, the retired-name scan, and exact
  production Profile effort inspection.
