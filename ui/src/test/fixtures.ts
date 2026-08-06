import type { TimelineEvent } from "../types";

export function makeEvent(overrides: Partial<TimelineEvent> = {}): TimelineEvent {
  return {
    event_id: "event-1",
    run_id: "run-1",
    order: 1,
    revision: 1,
    kind: "agent_turn",
    name: "FailureResolutionAgent.resolve",
    status: "succeeded",
    started_at: "2026-08-05T08:00:00.000Z",
    ended_at: "2026-08-05T08:00:01.000Z",
    duration_ms: 1000,
    parent_event_id: null,
    agent: {
      session_id: "agent-1",
      name: "FailureResolutionAgent.resolve",
      path: ["FailureResolutionAgent.resolve"],
    },
    operation_key: null,
    round_number: null,
    summary: "Agent turn · FailureResolutionAgent.resolve",
    attributes: {},
    detail: { input: { messages: [] }, output: null },
    ...overrides,
  };
}
