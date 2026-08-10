import { describe, expect, it } from "vitest";

import { initialObserverState, observerReducer } from "../state";
import type { ObserverState } from "../types";
import { makeEvent } from "./fixtures";

describe("observerReducer", () => {
  it("does not let an older snapshot rewind the current Todo", () => {
    const todo = (revision: number, activeStep: string) => ({
      revision,
      agent: { session_id: "main-1", name: "main", path: ["main"], lifecycle: "main" as const },
      explanation: null,
      items: [{ step: activeStep, status: "in_progress" as const }],
      completed_count: 0,
      total_count: 1,
      active_step: activeStep,
      percent: 0,
    });
    let state = observerReducer(initialObserverState, {
      type: "snapshot",
      snapshot: {
        schema_version: 3,
        run: null,
        events: [],
        todo: todo(6, "Probe endpoint"),
        latest_cursor: 12,
      },
    });

    state = observerReducer(state, {
      type: "snapshot",
      snapshot: {
        schema_version: 3,
        run: null,
        events: [],
        todo: todo(5, "Read schema"),
        latest_cursor: 10,
      },
    });

    expect(state.latestCursor).toBe(12);
    expect(state.todo).toMatchObject({
      revision: 6,
      active_step: "Probe endpoint",
    });
  });

  it("keeps a newer same-run Todo when another snapshot has a later cursor", () => {
    const run = {
      run_id: "run-1",
      status: "running",
      started_at: "2026-08-05T09:00:00Z",
      ended_at: null,
      request: {},
      result: null,
    };
    const currentTodo = {
      revision: 6,
      agent: { session_id: "main-1", name: "main", path: ["main"], lifecycle: "main" as const },
      explanation: null,
      items: [],
      completed_count: 0,
      total_count: 0,
      active_step: null,
      percent: 0,
    };
    const state: ObserverState = {
      ...initialObserverState,
      run,
      todo: currentTodo,
      latestCursor: 12,
    };

    const hydrated = observerReducer(state, {
      type: "snapshot",
      snapshot: {
        schema_version: 3,
        run,
        events: [],
        todo: { ...currentTodo, revision: 5 },
        latest_cursor: 13,
      },
    });

    expect(hydrated.latestCursor).toBe(13);
    expect(hydrated.todo).toBe(currentTodo);
  });

  it("advances the cursor without accepting an older Todo revision", () => {
    const currentTodo = {
      revision: 6,
      agent: { session_id: "main-1", name: "main", path: ["main"], lifecycle: "main" as const },
      explanation: null,
      items: [],
      completed_count: 0,
      total_count: 0,
      active_step: null,
      percent: 0,
    };
    let state: ObserverState = {
      ...initialObserverState,
      todo: currentTodo,
      latestCursor: 12,
    };

    state = observerReducer(state, {
      type: "stream",
      eventType: "todo.replace",
      data: { ...currentTodo, revision: 5 },
      cursor: 13,
    });

    expect(state.latestCursor).toBe(13);
    expect(state.todo).toMatchObject({
      revision: 6,
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
        schema_version: 3,
        run: null,
        events: [later, earlier],
        todo: null,
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

  it("clears prior events and Todo when a new run reset arrives", () => {
    const populated = {
      ...initialObserverState,
      eventById: { old: makeEvent({ event_id: "old" }) },
      eventIds: ["old"],
      todo: {
        revision: 1,
        agent: { session_id: "main-1", name: "main", path: ["main"], lifecycle: "main" as const },
        explanation: null,
        items: [],
        completed_count: 0,
        total_count: 0,
        active_step: null,
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
    expect(state.todo).toBeNull();
  });
});
