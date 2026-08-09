# Worklist method

## Preserve coverage and identity

Treat every initial exact `E* -> TC*` association as a coverage obligation.
Exact duplicate messages may share one `E*`, but every associated Test Case
must remain reachable from at least one worklist item.

Assign `WI-001`, `WI-002`, and later IDs contiguously when items first appear.
Keep an ID through edits, reordering, reopening, or decision changes. When
splitting, keep the original ID on the primary diagnosis and assign new IDs to
the other parts. When merging, keep the earliest ID. Never reuse a deleted ID.

## Group by semantic cause

Group sources only when one causal explanation and one terminal decision can
cover them. Similar words, equal status codes, or a shared Parameter name are
not sufficient. It is valid for investigation items to overlap temporarily.

Split an item when evidence reveals different causes, affected inputs, or
terminal decisions. Merge items when one confirmed cause makes the separation
artificial. Reopen a decision when new runtime evidence invalidates it.

## Replace the complete list

Call `failure_resolution.read_worklist` whenever the revision is uncertain.
Every `failure_resolution.write_worklist` call replaces the complete list at
`expected_revision` and must be the only Tool call in that model output. On a
revision conflict, read the latest list and apply the semantic revision again.

Select one `active_item_id` before an HTTP Probe or Patch Subagent delegation.
Read-only investigation may occur before the first write.

Store only:

- issued `E*`, `TC*`, and real `P*` references;
- valid semantic Parameter handles;
- bounded progress, root-cause, and decision text.

Never embed a request, response, Schema, Generator, Constraint, candidate,
sample, history DTO, Tool result, database row, or Subagent completion.

## Handle Probe cases

Attach a Probe Test Case only when it repeats one of the exact initial Failure
messages. Probe cases may strengthen evidence, but they never expand the
initial source-coverage obligation.

## Keep investigation honest

Repetition is allowed when it answers a different question or checks changed
runtime state. When another identical read will not reduce uncertainty, change
the evidence source, switch the active item, make a justified decision, or
leave the item undecided. Do not manufacture `no_patch` merely to empty the
worklist.
