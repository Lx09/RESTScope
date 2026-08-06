import { expect, it, vi } from "vitest";

import { connectLiveRun } from "../stream";
import { makeEvent } from "./fixtures";

it("hydrates a snapshot and lets EventSource resume named cursor events", async () => {
  const dispatch = vi.fn();
  const statuses: string[] = [];
  const listeners = new Map<string, (event: MessageEvent<string>) => void>();

  class TestEventSource {
    static instance: TestEventSource;
    onopen: (() => void) | null = null;
    onerror: (() => void) | null = null;
    readonly url: string;
    closed = false;

    constructor(url: string | URL) {
      this.url = String(url);
      TestEventSource.instance = this;
    }

    addEventListener(name: string, listener: EventListenerOrEventListenerObject) {
      listeners.set(name, listener as (event: MessageEvent<string>) => void);
    }

    close() {
      this.closed = true;
    }
  }

  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
    ok: true,
    json: async () => ({
      schema_version: 2,
      run: null,
      events: [],
      worklist: null,
      latest_cursor: 7,
    }),
  }));
  vi.stubGlobal("EventSource", TestEventSource);

  const connection = await connectLiveRun(dispatch, (status) => statuses.push(status));
  const source = TestEventSource.instance;
  source.onopen?.();
  listeners.get("timeline.upsert")?.(
    new MessageEvent("timeline.upsert", {
      data: JSON.stringify(makeEvent({ event_id: "event-8", order: 8 })),
      lastEventId: "8",
    }),
  );
  source.onerror?.();
  connection.close();

  expect(source.url).toBe("/api/v1/events?after=7");
  expect(dispatch).toHaveBeenNthCalledWith(1, expect.objectContaining({ type: "snapshot" }));
  expect(dispatch).toHaveBeenNthCalledWith(2, {
    type: "stream",
    eventType: "timeline.upsert",
    data: expect.objectContaining({ event_id: "event-8" }),
    cursor: 8,
  });
  expect(statuses).toEqual(["connecting", "live", "reconnecting", "closed"]);
  expect(source.closed).toBe(true);
  vi.unstubAllGlobals();
});

it("rejects an obsolete observer schema instead of guessing compatibility", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
    ok: true,
    json: async () => ({
      schema_version: 1,
      run: null,
      events: [],
      worklist: null,
      latest_cursor: 0,
    }),
  }));

  await expect(connectLiveRun(vi.fn(), vi.fn())).rejects.toThrow(
    "Unsupported observer schema version 1",
  );
  vi.unstubAllGlobals();
});

it("does not hydrate or open SSE after the initial snapshot is cancelled", async () => {
  const dispatch = vi.fn();
  const statuses: string[] = [];
  const controller = new AbortController();
  let resolveFetch: ((value: unknown) => void) | undefined;
  const pendingFetch = new Promise((resolve) => {
    resolveFetch = resolve;
  });
  const eventSource = vi.fn();

  // The mock deliberately resolves after abort. This reproduces a transport
  // that cannot stop promptly and proves the client still ignores its reply.
  vi.stubGlobal("fetch", vi.fn().mockReturnValue(pendingFetch));
  vi.stubGlobal("EventSource", eventSource);

  const connecting = connectLiveRun(
    dispatch,
    (status) => statuses.push(status),
    controller.signal,
  );
  controller.abort();
  resolveFetch?.({
    ok: true,
    json: async () => ({
      schema_version: 2,
      run: null,
      events: [],
      worklist: null,
      latest_cursor: 9,
    }),
  });
  await connecting;

  expect(dispatch).not.toHaveBeenCalled();
  expect(eventSource).not.toHaveBeenCalled();
  expect(statuses).toEqual(["connecting"]);
  vi.unstubAllGlobals();
});
