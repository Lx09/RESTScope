import { IDBFactory as FakeIDBFactory } from "fake-indexeddb";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  RUN_HISTORY_DATABASE_NAME,
  RUN_HISTORY_DATABASE_VERSION,
  RunHistoryStore,
  RunHistoryWriter,
  observerSnapshotToState,
  observerStateToSnapshot,
  selectObserverView,
  type RunHistoryPersistence,
} from "../runHistory";
import { initialObserverState, observerReducer } from "../state";
import type { ObserverSnapshot } from "../types";
import { makeEvent } from "./fixtures";

function makeSnapshot(runId: string, cursor = 1): ObserverSnapshot {
  return {
    schema_version: 3,
    run: {
      run_id: runId,
      status: "running",
      started_at: `2026-08-07T00:00:0${cursor}.000Z`,
      ended_at: null,
      request: {
        headers: {
          Authorization: "Bearer test-secret",
          Cookie: "session=test-cookie",
        },
        prompt: ["system prompt", "user prompt"],
      },
      result: null,
    },
    events: [makeEvent({
      event_id: `${runId}-event`,
      run_id: runId,
      order: cursor,
      operation_key: "POST /api/v4/projects",
      detail: {
        reasoning: "complete already-redacted reasoning",
        output: { tool_result: { token: "tool-secret" } },
      },
    })],
    todo: null,
    latest_cursor: cursor,
  };
}

async function putInvalidRecord(factory: IDBFactory): Promise<void> {
  const database = await new Promise<IDBDatabase>((resolve, reject) => {
    const request = factory.open(RUN_HISTORY_DATABASE_NAME, RUN_HISTORY_DATABASE_VERSION);
    request.onerror = () => reject(request.error);
    request.onsuccess = () => resolve(request.result);
  });
  await new Promise<void>((resolve, reject) => {
    const transaction = database.transaction("runs", "readwrite");
    transaction.objectStore("runs").put({
      storage_schema_version: 99,
      run_id: "corrupt-run",
      saved_at: "not-a-date",
      snapshot: {},
    });
    transaction.oncomplete = () => resolve();
    transaction.onerror = () => reject(transaction.error);
  });
  database.close();
}

afterEach(() => {
  vi.useRealTimers();
});

describe("RunHistoryStore", () => {
  it("clears every pre-v3 record during the database upgrade", async () => {
    const factory = new FakeIDBFactory();
    const legacyDatabase = await new Promise<IDBDatabase>((resolve, reject) => {
      const request = factory.open(RUN_HISTORY_DATABASE_NAME, 1);
      request.onupgradeneeded = () => {
        request.result.createObjectStore("runs", { keyPath: "run_id" });
      };
      request.onerror = () => reject(request.error);
      request.onsuccess = () => resolve(request.result);
    });
    await new Promise<void>((resolve, reject) => {
      const transaction = legacyDatabase.transaction("runs", "readwrite");
      transaction.objectStore("runs").put({
        storage_schema_version: 1,
        run_id: "canvas-run",
        saved_at: "2026-08-07T00:00:00.000Z",
        snapshot: makeSnapshot("canvas-run"),
      });
      transaction.oncomplete = () => resolve();
      transaction.onerror = () => reject(transaction.error);
    });
    legacyDatabase.close();

    const store = new RunHistoryStore(factory);

    expect((await store.list()).summaries).toEqual([]);
    store.close();
  });

  it("restores the complete observer snapshot without removing sensitive fields", async () => {
    const factory = new FakeIDBFactory();
    const store = new RunHistoryStore(factory, () => new Date("2026-08-07T01:02:03.000Z"));

    const saved = await store.save(makeSnapshot("run-sensitive", 7));
    const listing = await store.list();
    const loaded = await store.load("run-sensitive");

    expect(listing.summaries).toEqual([
      expect.objectContaining({
        runId: "run-sensitive",
        savedAt: "2026-08-07T01:02:03.000Z",
        eventCount: 1,
        operationKey: "POST /api/v4/projects",
      }),
    ]);
    expect(saved.deletedRunIds).toEqual([]);
    expect(loaded.invalid).toBe(false);
    expect(loaded.record?.snapshot.run?.request).toEqual(expect.objectContaining({
      headers: {
        Authorization: "Bearer test-secret",
        Cookie: "session=test-cookie",
      },
      prompt: ["system prompt", "user prompt"],
    }));
    expect(loaded.record?.snapshot.events[0].detail).toEqual({
      reasoning: "complete already-redacted reasoning",
      output: { tool_result: { token: "tool-secret" } },
    });
    store.close();
  });

  it("keeps only the five most recently saved runs", async () => {
    const factory = new FakeIDBFactory();
    const store = new RunHistoryStore(
      factory,
      () => new Date("2026-08-07T01:00:00.000Z"),
    );

    for (let index = 1; index <= 6; index += 1) {
      await store.save(makeSnapshot(`run-${index}`, index));
    }

    const listing = await store.list();
    expect(listing.summaries.map((summary) => summary.runId)).toEqual([
      "run-6",
      "run-5",
      "run-4",
      "run-3",
      "run-2",
    ]);
    expect((await store.load("run-1")).record).toBeNull();
    store.close();
  });

  it("ignores incompatible records and reports them without breaking valid history", async () => {
    const factory = new FakeIDBFactory();
    const store = new RunHistoryStore(factory);
    await store.save(makeSnapshot("valid-run"));
    store.close();
    await putInvalidRecord(factory);

    const reopened = new RunHistoryStore(factory);
    const listing = await reopened.list();
    const invalid = await reopened.load("corrupt-run");

    expect(listing.summaries.map((summary) => summary.runId)).toEqual(["valid-run"]);
    expect(listing.invalidCount).toBe(1);
    expect(invalid).toEqual({ record: null, invalid: true });
    reopened.close();
  });

  it("deletes one run or clears the entire browser history", async () => {
    const store = new RunHistoryStore(new FakeIDBFactory());
    await store.save(makeSnapshot("run-a", 1));
    await store.save(makeSnapshot("run-b", 2));

    expect((await store.delete("run-b")).summaries.map((item) => item.runId)).toEqual(["run-a"]);
    await store.clear();
    expect((await store.list()).summaries).toEqual([]);
    store.close();
  });
});

describe("RunHistoryWriter", () => {
  it("coalesces rapid updates and persists only the newest complete snapshot", async () => {
    vi.useFakeTimers();
    const save = vi.fn().mockResolvedValue({ summary: {}, deletedRunIds: [] });
    const persistence = { save } as unknown as RunHistoryPersistence;
    const writer = new RunHistoryWriter(persistence, { delayMs: 100 });

    writer.schedule(makeSnapshot("run-1", 1));
    writer.schedule(makeSnapshot("run-1", 2));
    await vi.advanceTimersByTimeAsync(100);
    await writer.flush();

    expect(save).toHaveBeenCalledTimes(1);
    expect(save).toHaveBeenCalledWith(expect.objectContaining({ latest_cursor: 2 }));
  });

  it("keeps the previous Run's final snapshot when a reset shares the debounce window", async () => {
    vi.useFakeTimers();
    const save = vi.fn().mockResolvedValue({ summary: {}, deletedRunIds: [] });
    const persistence = { save } as unknown as RunHistoryPersistence;
    const writer = new RunHistoryWriter(persistence, { delayMs: 100 });

    writer.schedule(makeSnapshot("run-before-reset", 11));
    writer.schedule(makeSnapshot("run-after-reset", 12));
    await vi.advanceTimersByTimeAsync(100);
    await writer.flush();

    expect(save).toHaveBeenCalledTimes(2);
    expect(save.mock.calls.map(([snapshot]) => snapshot.run?.run_id)).toEqual([
      "run-before-reset",
      "run-after-reset",
    ]);
  });

  it("reports a quota failure without rejecting the live update path", async () => {
    vi.useFakeTimers();
    const onError = vi.fn();
    const persistence = {
      save: vi.fn().mockRejectedValue(new DOMException("quota full", "QuotaExceededError")),
    } as unknown as RunHistoryPersistence;
    const writer = new RunHistoryWriter(persistence, { delayMs: 100, onError });

    writer.schedule(makeSnapshot("run-1"));
    await vi.advanceTimersByTimeAsync(100);
    await expect(writer.flush()).resolves.toBeUndefined();

    expect(onError).toHaveBeenCalledWith(expect.stringContaining("quota full"));
  });
});

describe("observer history view", () => {
  it("round-trips normalized reducer state through the persisted wire snapshot", () => {
    const snapshot = makeSnapshot("run-roundtrip", 8);
    const state = observerReducer(initialObserverState, { type: "snapshot", snapshot });

    expect(observerSnapshotToState(observerStateToSnapshot(state))).toEqual(state);
  });

  it("prefers live data in automatic mode but keeps an explicitly selected history frozen", () => {
    const cached = makeSnapshot("cached-run", 4);
    const live = observerSnapshotToState(makeSnapshot("live-run", 9));

    expect(selectObserverView(live, cached, null, "auto")).toMatchObject({
      source: "live",
      state: { run: { run_id: "live-run" } },
    });
    expect(selectObserverView(live, cached, cached, "history")).toMatchObject({
      source: "history",
      state: { run: { run_id: "cached-run" }, latestCursor: 4 },
    });
  });

  it("uses the newest cached run when the backend has no current run", () => {
    const cached = makeSnapshot("cached-run", 4);

    expect(selectObserverView(initialObserverState, cached, null, "auto")).toMatchObject({
      source: "history",
      state: { run: { run_id: "cached-run" } },
    });
  });
});
