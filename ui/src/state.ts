/** Deterministic snapshot/SSE reducer used by the interface and unit tests. */

import type {
  ObserverSnapshot,
  ObserverState,
  RunState,
  StreamEventType,
  TimelineEvent,
  TodoState,
} from "./types";

export type ObserverAction =
  | { type: "snapshot"; snapshot: ObserverSnapshot }
  | { type: "stream"; eventType: StreamEventType; data: any; cursor: number };

export const initialObserverState: ObserverState = {
  run: null,
  eventById: {},
  eventIds: [],
  todo: null,
  latestCursor: 0,
};

function replaceEvents(events: TimelineEvent[]): Pick<ObserverState, "eventById" | "eventIds"> {
  const eventById = Object.fromEntries(events.map((event) => [event.event_id, event]));
  const eventIds = [...eventByIdValues(eventById)]
    .sort(compareEvents)
    .map((event) => event.event_id);
  return { eventById, eventIds };
}

function eventByIdValues(events: Record<string, TimelineEvent>): TimelineEvent[] {
  return Object.values(events);
}

export function compareEvents(left: TimelineEvent, right: TimelineEvent): number {
  return left.order - right.order || left.started_at.localeCompare(right.started_at);
}

export function observerReducer(state: ObserverState, action: ObserverAction): ObserverState {
  if (action.type === "snapshot") {
    // StrictMode can leave two initial requests in flight. A slower response
    // must never replace state already hydrated from a newer observer cursor.
    if (action.snapshot.latest_cursor < state.latestCursor) return state;
    const sameRun = state.run?.run_id === action.snapshot.run?.run_id;
    const snapshotTodo = action.snapshot.todo;
    const keepCurrentTodo = (
      sameRun
      && state.todo !== null
      && (
        snapshotTodo === null
        || snapshotTodo.revision <= state.todo.revision
      )
    );
    return {
      run: action.snapshot.run,
      ...replaceEvents(action.snapshot.events),
      todo: keepCurrentTodo ? state.todo : snapshotTodo,
      latestCursor: action.snapshot.latest_cursor,
    };
  }

  // SSE reconnects may replay the last delivered event. Cursor order is the
  // authority for every stream mutation, so a replay cannot alter any state.
  if (action.cursor <= state.latestCursor) return state;
  const latestCursor = action.cursor;
  if (action.eventType === "run.reset") {
    return {
      ...initialObserverState,
      run: action.data as RunState,
      latestCursor,
    };
  }
  if (action.eventType === "run.update") {
    return { ...state, run: action.data as RunState, latestCursor };
  }
  if (action.eventType === "todo.replace") {
    const nextTodo = action.data as TodoState;
    // The cursor can advance while an older Todo payload is replayed. Keep the
    // latest successful Main Agent Plan projection.
    if (
      state.todo !== null
      && nextTodo.revision <= state.todo.revision
    ) {
      return { ...state, latestCursor };
    }
    return { ...state, todo: nextTodo, latestCursor };
  }

  const event = action.data as TimelineEvent;
  const eventById = { ...state.eventById, [event.event_id]: event };
  const eventIds = eventByIdValues(eventById).sort(compareEvents).map((item) => item.event_id);
  return { ...state, eventById, eventIds, latestCursor };
}
