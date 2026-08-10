# Main Agent First-Class Runtime

Status: Implemented; testing capabilities bound but intentionally unauthorized

## Outcome

RESTScope now starts one generic, Profile-authorized Main Agent through a
blocking application entry:

```python
app.initialize(...)
app.start()
```

`start()` accepts no task and returns no result. The stable `main` Profile
defines the App-lifetime mission, and the internal `AgentCompletion` contract
only tells the model loop when to stop. A non-success terminal state is raised
as a safe runtime error. The former `RESTScopeApp.run`, `RunHarness`,
`RESTScopeRunRequest`, `RESTScopeRunReport`, and `OperationAttempt` interfaces
were removed without compatibility aliases.

## Implemented Profile and prompt contract

`AgentProfile.instructions` is optional stable developer guidance:

- supplied text must be nonblank and no longer than 12,000 characters;
- it instructs that Profile's own Agent and is never parent delegation text;
- `description` remains visible only to an authorized direct parent;
- instructions are mandatory stable-prefix content and are never shortened;
- the 24,000-character stable prefix fails closed before model or Tool use if
  it cannot contain the Harness contract, complete Profile instructions, and
  all selected Skill and child names;
- optional descriptions are the only stable metadata that may be omitted.

The production Profile is named `main`, uses the `thinking` model, and grants
exactly `plan.read` and `plan.update`. It currently selects no Skills, Context
Sources, domain Tools, or child Profiles. `skill.read` therefore is not added;
`file.read` and all Subagent lifecycle Tools remain unauthorized.

This capability-light Profile is deliberate. OpenAPI discovery, request-state
inspection, Patch validation/application, and bounded Batch execution now have
production bindings, and the two standard testing Skills exist. The App owns
the single Patch runtime and passes it whole to Harness binding; Harness does
not assemble or inspect its Store. Binding does
not grant access. Until a later decision selects Skills, grants every required
Tool, and supplies any needed child Profile, Main can only plan and report that
it cannot safely test the target.

## Blocking lifecycle

Successful `initialize()` binds one target and parsed OpenAPI context. The
first `start()` asks the Harness to create `main`, installs a fixed taskless
bootstrap user message, then blocks in the existing one-Tool-or-final model
loop. The Profile instructions—not an `AgentTask`—are its objective.

The Main loop may use correction, Tool execution, compaction, cancellation,
shared rollout accounting, and tracing already owned by the generic Agent and
Harness. It can start only once per App. `KeyboardInterrupt` is re-raised while
the optional observer retains a stopped snapshot until App close. `close()`
closes the Main tree, clears bound target context, and releases owned runtime
resources.

Task-scoped `Agent.run(AgentTask)` remains available for generic internal
callers and is still the only child execution protocol. A Subagent cannot use
the taskless `start()` entry because its parent must supply a complete bounded
objective and it receives no hidden parent state.

## Main instructions and ownership

The installed Main instructions establish these continuing rules:

- work on the API target initialized for this App lifetime;
- own semantic method, ordering, retry, delegation, and stopping decisions;
- treat Skill metadata as discoverable method descriptions, not grants;
- use the private Plan as revisable working memory, never evidence or durable
  scheduler state;
- use children only for bounded work and send complete objectives;
- base factual conclusions on current authorized Tool or Subagent results;
- avoid retries without new evidence, changed state, or predicted benefit;
- report blocked and unresolved work when authorized safe actions cannot make
  meaningful progress;
- end only with the bounded internal `AgentCompletion` result.

The Harness remains mechanical: Profile validation, dependency injection,
model and Tool calls, prompt/context capacity, budgets, tracing, cancellation,
and child lifecycle. It does not restore an Operation FIFO, choose testing
coverage, or interpret a model Plan as scheduler state.

## Retired workflows and later work

Operation Smoke and its dedicated Failure Resolution, Compact, Parameter Patch,
Patch Review, candidate, Finalizer, Memory, and evaluation implementations have
been retired. Their reusable methodology now lives in the standard
`resolve-operation-failures` and `apply-parameter-patch` Skills. Neither Skill
is exposed to the initial Main Profile. There is still no `MainAgent` class,
production child Profile, fact ledger, App task DTO, or App result DTO.

Later activation work must separately design and approve:

1. the ordered standard testing Skills selected by `main`;
2. every Tool and Context Source granted to satisfy those Skills;
3. a bounded Patch child Profile selecting `apply-parameter-patch` if Failure
   Resolution is activated;
4. any deterministic evidence-reference ledger required by a future external
   product surface.

No future addition may infer Tool authorization from Skill discovery. The
Profile must still grant every ordinary Tool explicitly, `skill.read` remains
the only automatic loader exception, and `file.read` remains an ordinary
explicit grant.

## Verification

Implementation verification is offline. It covers bounded Profile guidance,
stable prompt placement, taskless Main startup, child rejection of taskless
startup, exact production Profile grants, App initialization/start/close,
trace hierarchy, removal of old public run types, Profile isolation from newly
bound testing Tools, and package boundaries. No real model, target API, MCP service,
Phoenix service, or other external system is called.
