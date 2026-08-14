---
name: query-restscope-database
description: Choose and execute bounded read-only SQL against RESTScope's current database. Use to answer questions about database structure, API test coverage, executed test cases and HTTP results, confirmed defects, API resources and state changes, test-input data sources, or OpenAPI contract history.
---

# Query the RESTScope database

Use the database as read-only evidence about API testing. A stored row proves
only the fact represented by that row; it does not by itself prove broader API
behavior or authorize another action.

1. Classify the question by the user-facing purpose below. Call `file.read` for
   exactly that Reference before writing SQL. Load another Reference only when
   the question genuinely crosses purposes.
2. Call `database.query` with explicit columns, named parameters,
   deterministic `ORDER BY`, and a narrow `LIMIT`. Inspect database structure
   first whenever a table, column, or relationship is uncertain.
3. Follow the selected Reference's RESTScope storage mapping. Internal table
   names are implementation details, not concepts the question must already
   use.
4. Read HTTP response metadata, complete headers, and response bodies in the
   separate steps described by the test-case Reference. Never derive or combine
   a header mapping in SQL.
5. Refine or paginate when the Tool reports truncation. Never attempt a write,
   schema change, PRAGMA, attachment, transaction, or extension load.

## Reference routing

- Discover current tables, columns, and declared relationships: [database structure](references/database-structure.md)
- Find untested endpoints or compare positive and negative coverage: [test coverage](references/test-coverage.md)
- Inspect an executed test case, request, HTTP result, or transport failure: [test cases and results](references/test-cases-and-results.md)
- Find failures that were reproduced and confirmed as defects: [confirmed defects](references/confirmed-defects.md)
- Inspect learned API entities, identifiers, current state, or state history: [API resources and state](references/api-resources-and-state.md)
- Explain how future or historical test inputs obtain their values: [test inputs and data sources](references/test-inputs-and-data-sources.md)
- Inspect API endpoints, the normalized OpenAPI document, or response-contract history: [API contracts and changes](references/api-contracts-and-changes.md)
