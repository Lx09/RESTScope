import { describe, expect, it } from "vitest";

import { initialObserverState, observerReducer } from "../state";
import type { ObserverState } from "../types";
import { makeEvent } from "./fixtures";

describe("observerReducer", () => {
  const orchestration = (revision: number, mission: string) => ({
    revision,
    goal: { mission, focus: null, success_criteria: [] },
    ledger: {
      plan_revision: 0,
      run_status: "planning" as const,
      plan_revisions: [],
      milestones: [],
      tasks: [],
      attempts: [],
    },
    sessions: [],
  });

  it("does not let an older snapshot rewind current Orchestration", () => {
    const current = orchestration(6, "Probe endpoint");
    let state = observerReducer(initialObserverState, {
      type: "snapshot",
      snapshot: {
        schema_version: 4,
        run: null,
        events: [],
        orchestration: current,
        latest_cursor: 12,
      },
    });

    state = observerReducer(state, {
      type: "snapshot",
      snapshot: {
        schema_version: 4,
        run: null,
        events: [],
        orchestration: orchestration(5, "Read schema"),
        latest_cursor: 10,
      },
    });

    expect(state.latestCursor).toBe(12);
    expect(state.orchestration).toBe(current);
  });

  it("keeps a newer same-run Orchestration projection with a later cursor", () => {
    const run = {
      run_id: "run-1",
      status: "running",
      started_at: "2026-08-05T09:00:00Z",
      ended_at: null,
      request: {},
      result: null,
    };
    const current = orchestration(6, "Current mission");
    const state: ObserverState = {
      ...initialObserverState,
      run,
      orchestration: current,
      latestCursor: 12,
    };

    const hydrated = observerReducer(state, {
      type: "snapshot",
      snapshot: {
        schema_version: 4,
        run,
        events: [],
        orchestration: orchestration(5, "Stale mission"),
        latest_cursor: 13,
      },
    });

    expect(hydrated.latestCursor).toBe(13);
    expect(hydrated.orchestration).toBe(current);
  });

  it("advances the cursor without accepting an older Orchestration revision", () => {
    const current = orchestration(6, "Current mission");
    let state: ObserverState = {
      ...initialObserverState,
      orchestration: current,
      latestCursor: 12,
    };

    state = observerReducer(state, {
      type: "stream",
      eventType: "orchestration.replace",
      data: orchestration(5, "Stale mission"),
      cursor: 13,
    });

    expect(state.latestCursor).toBe(13);
    expect(state.orchestration).toBe(current);
  });

  it("ignores duplicate and replayed stream cursors", () => {
    const current = makeEvent({ event_id: "current", revision: 2, status: "succeeded" });
    const state = {
      ...initialObserverState,
      eventById: { current },
      eventIds: ["current"],
      latestCursor: 12,
    };

    const replayed = observerReducer(state, {
      type: "stream",
      eventType: "timeline.upsert",
      data: makeEvent({ event_id: "replayed", revision: 1, status: "running" }),
      cursor: 12,
    });

    expect(replayed).toBe(state);
    expect(replayed.eventIds).toEqual(["current"]);
  });

  it("upserts an event in its original start position when completion arrives", () => {
    const later = makeEvent({ event_id: "later", order: 2, status: "running" });
    const earlier = makeEvent({ event_id: "earlier", order: 1, status: "running" });
    let state = observerReducer(initialObserverState, {
      type: "snapshot",
      snapshot: {
        schema_version: 4,
        run: null,
        events: [later, earlier],
        orchestration: null,
        latest_cursor: 3,
      },
    });

    state = observerReducer(state, {
      type: "stream",
      eventType: "timeline.upsert",
      data: { ...earlier, revision: 2, status: "succeeded", duration_ms: 12 },
      cursor: 4,
    });

    expect(state.eventIds).toEqual(["earlier", "later"]);
    expect(state.eventById.earlier).toMatchObject({ revision: 2, status: "succeeded" });
    expect(state.latestCursor).toBe(4);
  });

  it("clears prior events and Orchestration when a new run reset arrives", () => {
    const populated = {
      ...initialObserverState,
      eventById: { old: makeEvent({ event_id: "old" }) },
      eventIds: ["old"],
      orchestration: orchestration(1, "Old mission"),
    };
    const run = {
      run_id: "run-2",
      status: "running",
      started_at: "2026-08-05T09:00:00Z",
      ended_at: null,
      request: {},
      result: null,
    };

    const state = observerReducer(populated, {
      type: "stream",
      eventType: "run.reset",
      data: run,
      cursor: 20,
    });

    expect(state.run).toEqual(run);
    expect(state.eventIds).toEqual([]);
    expect(state.orchestration).toBeNull();
  });
});
