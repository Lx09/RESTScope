import { describe, expect, it } from "vitest";

import { initialObserverState, observerReducer } from "../state";
import type { ObserverState } from "../types";
import { makeEvent } from "./fixtures";

describe("observerReducer", () => {
  it("does not let an older snapshot rewind the current Worklist", () => {
    const worklist = (revision: number, activeItemId: string) => ({
      operation_key: "POST /projects",
      snapshot: {
        revision,
        active_item_id: activeItemId,
        items: [{
          item_id: activeItemId,
          source_failure_refs: ["E1"],
          test_case_refs: ["TC1"],
          suspected_parameters: [],
          candidate_refs: [],
        }],
      },
      failure_messages: { E1: "HTTP 400: invalid input" },
      decided_count: 0,
      total_count: 1,
      percent: 0,
    });
    let state = observerReducer(initialObserverState, {
      type: "snapshot",
      snapshot: {
        schema_version: 2,
        run: null,
        events: [],
        worklist: worklist(6, "WI-006"),
        latest_cursor: 12,
      },
    });

    state = observerReducer(state, {
      type: "snapshot",
      snapshot: {
        schema_version: 2,
        run: null,
        events: [],
        worklist: worklist(5, "WI-005"),
        latest_cursor: 10,
      },
    });

    expect(state.latestCursor).toBe(12);
    expect(state.worklist?.snapshot).toMatchObject({
      revision: 6,
      active_item_id: "WI-006",
    });
  });

  it("keeps a newer same-run Worklist when another snapshot has a later cursor", () => {
    const run = {
      run_id: "run-1",
      status: "running",
      started_at: "2026-08-05T09:00:00Z",
      ended_at: null,
      request: {},
      result: null,
    };
    const currentWorklist = {
      operation_key: "POST /projects",
      snapshot: { revision: 6, active_item_id: "WI-006", items: [] },
      failure_messages: {},
      decided_count: 0,
      total_count: 0,
      percent: 0,
    };
    const state: ObserverState = {
      ...initialObserverState,
      run,
      worklist: currentWorklist,
      latestCursor: 12,
    };

    const hydrated = observerReducer(state, {
      type: "snapshot",
      snapshot: {
        schema_version: 2,
        run,
        events: [],
        worklist: {
          ...currentWorklist,
          snapshot: { revision: 5, active_item_id: "WI-005", items: [] },
        },
        latest_cursor: 13,
      },
    });

    expect(hydrated.latestCursor).toBe(13);
    expect(hydrated.worklist).toBe(currentWorklist);
  });

  it("advances the cursor without accepting an older Worklist revision", () => {
    const currentWorklist = {
      operation_key: "POST /projects",
      snapshot: {
        revision: 6,
        active_item_id: "WI-006",
        items: [],
      },
      failure_messages: {},
      decided_count: 0,
      total_count: 0,
      percent: 0,
    };
    let state: ObserverState = {
      ...initialObserverState,
      worklist: currentWorklist,
      latestCursor: 12,
    };

    state = observerReducer(state, {
      type: "stream",
      eventType: "worklist.replace",
      data: {
        ...currentWorklist,
        snapshot: { revision: 5, active_item_id: "WI-005", items: [] },
      },
      cursor: 13,
    });

    expect(state.latestCursor).toBe(13);
    expect(state.worklist?.snapshot).toMatchObject({
      revision: 6,
      active_item_id: "WI-006",
    });
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
        schema_version: 2,
        run: null,
        events: [later, earlier],
        worklist: null,
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

  it("clears prior events and worklist when a new run reset arrives", () => {
    const populated = {
      ...initialObserverState,
      eventById: { old: makeEvent({ event_id: "old" }) },
      eventIds: ["old"],
      worklist: {
        operation_key: "POST /projects",
        snapshot: { revision: 1, active_item_id: null, items: [] },
        failure_messages: {},
        decided_count: 0,
        total_count: 0,
        percent: 0,
      },
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
    expect(state.worklist).toBeNull();
  });
});
