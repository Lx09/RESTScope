/** Connect the authoritative server snapshot and cursor stream for the live view. */

import type { Dispatch } from "react";

import type { ObserverAction } from "./state";
import type { ObserverSnapshot, StreamEventType, StreamStatus } from "./types";

const EVENT_TYPES: StreamEventType[] = [
  "run.reset",
  "run.update",
  "timeline.upsert",
  "todo.replace",
];

export interface LiveConnection {
  close: () => void;
}

export async function connectLiveRun(
  dispatch: Dispatch<ObserverAction>,
  onStatus: (status: StreamStatus) => void,
  signal?: AbortSignal,
): Promise<LiveConnection> {
  let closed = signal?.aborted ?? false;
  let source: EventSource | null = null;
  const inactiveConnection: LiveConnection = { close: () => undefined };

  if (closed) return inactiveConnection;
  onStatus("connecting");

  // React StrictMode intentionally mounts and cleans up the effect twice in
  // development. Abort owns both the fetch and the later state side effects.
  const handleAbort = () => {
    closed = true;
    source?.close();
  };
  signal?.addEventListener("abort", handleAbort, { once: true });

  try {
    const response = await fetch("/api/v1/run", { cache: "no-store", signal });
    if (closed) return inactiveConnection;
    if (!response.ok) {
      throw new Error(`Snapshot request failed with HTTP ${response.status}`);
    }
    const snapshot = (await response.json()) as ObserverSnapshot;
    if (closed) return inactiveConnection;
    if (snapshot.schema_version !== 3) {
      throw new Error(`Unsupported observer schema version ${String(snapshot.schema_version)}`);
    }
    dispatch({ type: "snapshot", snapshot });

    if (closed) return inactiveConnection;
    source = new EventSource(`/api/v1/events?after=${snapshot.latest_cursor}`);
    source.onopen = () => {
      if (!closed) onStatus("live");
    };
    source.onerror = () => {
      if (!closed) onStatus("reconnecting");
    };
    for (const eventType of EVENT_TYPES) {
      source.addEventListener(eventType, (rawEvent) => {
        if (closed) return;
        const event = rawEvent as MessageEvent<string>;
        dispatch({
          type: "stream",
          eventType,
          data: JSON.parse(event.data),
          cursor: Number(event.lastEventId || snapshot.latest_cursor),
        });
      });
    }

    return {
      close: () => {
        if (closed) return;
        closed = true;
        source?.close();
        signal?.removeEventListener("abort", handleAbort);
        onStatus("closed");
      },
    };
  } catch (error) {
    signal?.removeEventListener("abort", handleAbort);
    if (closed || (error instanceof DOMException && error.name === "AbortError")) {
      return inactiveConnection;
    }
    throw error;
  }
}
