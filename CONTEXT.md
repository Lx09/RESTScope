# RESTScope domain language

RESTScope explores an API with generated requests and changes future request
generation only when current evidence supports a bounded Parameter Patch.

**OpenAPI Audit**
The current normalized OpenAPI document plus append-only response-contract
change events. It supports audit and export; it does not restore an App.

**Operation Reference**
A stable semantic path to one request input or response field inside one
OpenAPI Operation. Examples are `query.sort`, `body.project.startDate`, and
`body.items[].id`.

**Parameter**
One request input within one Operation, identified to Agents by an Operation
Reference. A repeated field name in another location or Operation is a
different Parameter.

**Generator**
The complete current rule for whether one input is present and which values it
can produce. A Generator owns single-input value and presence rules.

**Constraint**
A typed Boolean relationship among two or more request inputs generated in the
same request. Constraints do not invent values; participating Generators must
already expose values that can satisfy the relationship.

**Generation State**
One immutable view of all Generators, active Constraints, reference bindings,
revision, and digest for an Operation. The App-lifetime
`RequestGenerationConfigStore` holds only the latest state.

**Reference Value Pool**
A bounded set of typed values supplied to a reference-backed Generator.
Resource pools contain learned canonical identifiers. Response pools contain
values materialized from one explicitly selected Response Value Source.

**Response Value Source**
The exact producer Operation, response status, media type, and field selector
feeding one consumer input's Reference Value Pool. A Parameter Patch replaces
the complete source identity; it never appends an implicit alternative.

**Parameter Patch**
A complete semantic replacement for the changed Generators and the full active
Constraint closure intersecting its affected inputs. It also establishes the
final reference bindings for changed inputs. A Patch changes future RESTScope
request generation and does not itself send an HTTP request.

**Patch Validation**
Deterministic compilation, source revalidation, Constraint solving, domain
analysis, and sample generation against one exact Generation State revision.
The validation digest binds the complete semantic Patch, state, sources, seed,
sample count, and witnesses. Validation is read-only.

**Applied Revision**
The next Generation State published after the same validated Patch is
recompiled and its durable response-pool replacement commits. Publication and
pool commit are atomic within the running App: either both become visible or
the previous state remains visible.

**Batch Evidence**
Bounded inline requests and HTTP or transport outcomes from 1–5 generated
cases. A Batch freezes one Generation State and all named Reference Value
Pools before generating its first case. It creates no persistent Test Case,
Failure, candidate, or Agent memory record.

## Core relationships

- A Generator owns a single Parameter's possible values and presence.
- A Constraint owns only cross-Parameter relationships.
- Generation State includes exact reference bindings, so changing only a
  Response Value Source changes the state digest and advances the revision.
- Patch Validation supplies proof material but does not mutate state.
- Applying a Parameter Patch atomically replaces in-memory Generation State
  and the affected durable response pools. A database commit failure restores
  the old in-memory state before the Operation lock is released.
- An Applied Revision proves only that RESTScope changed future generation.
  A later Batch provides new evidence about the target API.
