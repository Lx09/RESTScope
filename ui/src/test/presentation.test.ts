import { describe, expect, it } from "vitest";

import { EMPTY_FILTERS, eventMatches } from "../presentation";
import { makeEvent } from "./fixtures";

describe("semantic conversation filtering", () => {
  const tool = makeEvent({
    event_id: "tool",
    kind: "tool_call",
    name: "restscope.http.request",
    agent: { session_id: "agent-1", name: "FailureResolutionAgent.resolve", path: ["FailureResolutionAgent.resolve"] },
    attributes: { "restscope.tool.family": "http" },
  });
  const batch = makeEvent({
    event_id: "batch",
    order: 2,
    kind: "smoke_batch",
    name: "OperationTestingService.run_smoke_batch",
    parent_event_id: null,
    agent: null,
    detail: {
      operation_key: "GET /projects",
      cases: [{ case_id: "TC1", request: { url: "https://api.test/projects?state=active" } }],
    },
  });

  it("filters by family, semantic kind, status, and detail search", () => {
    expect(eventMatches(tool, { ...EMPTY_FILTERS, toolFamilies: ["openapi"] })).toBe(false);
    expect(eventMatches(batch, { ...EMPTY_FILTERS, search: "state=active" })).toBe(true);
    expect(eventMatches(batch, { ...EMPTY_FILTERS, kinds: ["smoke_batch"] })).toBe(true);
    expect(eventMatches(batch, { ...EMPTY_FILTERS, statuses: ["failed"] })).toBe(false);
  });
});
