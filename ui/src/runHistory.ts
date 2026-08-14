/**
 * Persist and restore the observer's already-redacted browser payloads.
 *
 * The live backend remains the authority for a running test. This module only
 * gives the loopback UI a browser-local history: it converts normalized React
 * state back to the schema-v4 snapshot, stores at most five complete runs in
 * IndexedDB, and rejects incompatible records before they reach render code.
 */

import type {
  ObserverSnapshot,
  ObserverState,
  OrchestrationState,
  TimelineEvent,
} from "./types";

export const RUN_HISTORY_DATABASE_NAME = "restscope-live-observer";
export const RUN_HISTORY_DATABASE_VERSION = 4;
const RUN_HISTORY_STORE_NAME = "runs";
const RUN_HISTORY_INDEX_NAME = "saved_at";
const RUN_HISTORY_RECORD_VERSION = 4;
const MAX_SAVED_RUNS = 5;

export type HistoryViewMode = "auto" | "live" | "history";
export type ObserverViewSource = "live" | "history";
export type RunHistoryStorageStatus = "loading" | "ready" | "saving" | "saved" | "error";

/** A complete browser-local record for one observer Run. */
export interface StoredRunRecord {
  storage_schema_version: 4;
  run_id: string;
  saved_at: string;
  snapshot: ObserverSnapshot;
}

/** Small metadata shown in the history selector without exposing payloads. */
export interface RunHistorySummary {
  runId: string;
  savedAt: string;
  startedAt: string;
  status: string;
  operationKey: string | null;
  eventCount: number;
}

/** Valid history choices plus the number of records skipped as incompatible. */
export interface RunHistoryListing {
  summaries: RunHistorySummary[];
  invalidCount: number;
}

/** Small result returned after a save, avoiding a reread of large Run bodies. */
export interface RunHistorySaveResult {
  summary: RunHistorySummary;
  deletedRunIds: string[];
}

/** Result of loading one key, distinguishing a missing key from bad data. */
export interface LoadedRunHistory {
  record: StoredRunRecord | null;
  invalid: boolean;
}

/** Minimal storage contract used by the write coalescer and the React shell. */
export interface RunHistoryPersistence {
  list(): Promise<RunHistoryListing>;
  load(runId: string): Promise<LoadedRunHistory>;
  save(snapshot: ObserverSnapshot): Promise<RunHistorySaveResult>;
  delete(runId: string): Promise<RunHistoryListing>;
  clear(): Promise<void>;
  close(): void;
}

export interface RunHistoryWriterOptions {
  delayMs?: number;
  onSaving?: () => void;
  onSaved?: (result: RunHistorySaveResult) => void;
  onError?: (message: string) => void;
}

export interface SelectedObserverView {
  source: ObserverViewSource;
  state: ObserverState;
  snapshot: ObserverSnapshot | null;
}

function requestValue<Value>(request: IDBRequest<Value>): Promise<Value> {
  return new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error ?? new Error("IndexedDB request failed"));
  });
}

function transactionFinished(transaction: IDBTransaction): Promise<void> {
  return new Promise((resolve, reject) => {
    transaction.oncomplete = () => resolve();
    transaction.onabort = () => reject(transaction.error ?? new Error("IndexedDB transaction aborted"));
    transaction.onerror = () => reject(transaction.error ?? new Error("IndexedDB transaction failed"));
  });
}

function isObject(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function isTimelineEvent(value: unknown): value is TimelineEvent {
  if (!isObject(value)) return false;
  return (
    typeof value.event_id === "string"
    && typeof value.order === "number"
    && typeof value.started_at === "string"
    && ["agent_turn", "tool_call"].includes(String(value.kind))
    && ["running", "succeeded", "warning", "failed"].includes(String(value.status))
  );
}

function isOrchestration(value: unknown): value is OrchestrationState | null {
  if (value === null) return true;
  if (!isObject(value)) return false;
  return (
    typeof value.revision === "number"
    && isObject(value.goal)
    && typeof value.goal.mission === "string"
    && isObject(value.ledger)
    && typeof value.ledger.plan_revision === "number"
    && Array.isArray(value.ledger.milestones)
    && Array.isArray(value.ledger.tasks)
    && Array.isArray(value.ledger.attempts)
    && Array.isArray(value.sessions)
  );
}

function isObserverSnapshot(value: unknown): value is ObserverSnapshot {
  if (!isObject(value) || value.schema_version !== 4) return false;
  if (!Array.isArray(value.events) || !value.events.every(isTimelineEvent)) return false;
  if (
    typeof value.latest_cursor !== "number"
    || !isOrchestration(value.orchestration)
  ) return false;
  if (value.run === null) return true;
  return (
    isObject(value.run)
    && typeof value.run.run_id === "string"
    && typeof value.run.status === "string"
    && typeof value.run.started_at === "string"
    && (value.run.ended_at === null || typeof value.run.ended_at === "string")
  );
}

/**
 * Return whether an unknown IndexedDB value is safe to pass into the UI.
 * Validation is intentionally structural rather than semantic: the backend
 * still owns schema-v4 meaning, while this guard prevents malformed local data
 * from crashing the browser after a deployment or manual database edit.
 */
export function isStoredRunRecord(value: unknown): value is StoredRunRecord {
  if (!isObject(value)) return false;
  if (value.storage_schema_version !== RUN_HISTORY_RECORD_VERSION) return false;
  if (typeof value.run_id !== "string" || typeof value.saved_at !== "string") return false;
  if (Number.isNaN(Date.parse(value.saved_at)) || !isObserverSnapshot(value.snapshot)) return false;
  return value.snapshot.run?.run_id === value.run_id;
}

function summaryFor(record: StoredRunRecord): RunHistorySummary {
  const operationEvent = [...record.snapshot.events]
    .reverse()
    .find((event) => event.operation_key !== null);
  return {
    runId: record.run_id,
    savedAt: record.saved_at,
    startedAt: record.snapshot.run?.started_at ?? record.saved_at,
    status: record.snapshot.run?.status ?? "unknown",
    operationKey: operationEvent?.operation_key ?? null,
    eventCount: record.snapshot.events.length,
  };
}

function listingFor(values: unknown[]): RunHistoryListing {
  const valid = values.filter(isStoredRunRecord);
  valid.sort((left, right) => right.saved_at.localeCompare(left.saved_at));
  return {
    summaries: valid.map(summaryFor),
    invalidCount: values.length - valid.length,
  };
}

/** Convert the live reducer's normalized maps back into the schema-v4 wire shape. */
export function observerStateToSnapshot(state: ObserverState): ObserverSnapshot {
  return {
    schema_version: 4,
    run: state.run,
    events: state.eventIds.map((eventId) => state.eventById[eventId]),
    orchestration: state.orchestration,
    latest_cursor: state.latestCursor,
  };
}

/** Normalize a validated saved snapshot for the conversation and Drawers. */
export function observerSnapshotToState(snapshot: ObserverSnapshot): ObserverState {
  const eventById = Object.fromEntries(snapshot.events.map((event) => [event.event_id, event]));
  const eventIds = Object.values(eventById)
    .sort((left, right) => left.order - right.order || left.started_at.localeCompare(right.started_at))
    .map((event) => event.event_id);
  return {
    run: snapshot.run,
    eventById,
    eventIds,
    orchestration: snapshot.orchestration,
    latestCursor: snapshot.latest_cursor,
  };
}

/**
 * Select the state rendered by the page without stopping the live reducer.
 * An explicit history choice stays frozen. Automatic startup recovery yields
 * to a real backend Run as soon as one becomes available.
 */
export function selectObserverView(
  liveState: ObserverState,
  automaticHistory: ObserverSnapshot | null,
  selectedHistory: ObserverSnapshot | null,
  mode: HistoryViewMode,
): SelectedObserverView {
  if (mode === "history" && selectedHistory !== null) {
    return {
      source: "history",
      state: observerSnapshotToState(selectedHistory),
      snapshot: selectedHistory,
    };
  }
  if (mode === "auto" && liveState.run === null && automaticHistory !== null) {
    return {
      source: "history",
      state: observerSnapshotToState(automaticHistory),
      snapshot: automaticHistory,
    };
  }
  return { source: "live", state: liveState, snapshot: null };
}

/**
 * Own the browser's IndexedDB connection for observer history.
 *
 * The store writes one complete record per Run and prunes older valid records
 * inside the same transaction. Database errors reject to the caller so the UI
 * can warn while allowing the live testing path to continue unchanged.
 */
export class RunHistoryStore implements RunHistoryPersistence {
  private readonly factory: IDBFactory;
  private readonly now: () => Date;
  private database: IDBDatabase | null = null;
  private databasePromise: Promise<IDBDatabase> | null = null;
  private lastSavedAtMilliseconds = 0;

  constructor(factory: IDBFactory = window.indexedDB, now: () => Date = () => new Date()) {
    this.factory = factory;
    this.now = now;
  }

  private open(): Promise<IDBDatabase> {
    if (this.databasePromise !== null) return this.databasePromise;
    this.databasePromise = new Promise((resolve, reject) => {
      const request = this.factory.open(RUN_HISTORY_DATABASE_NAME, RUN_HISTORY_DATABASE_VERSION);
      request.onupgradeneeded = (event) => {
        const database = request.result;
        if (!database.objectStoreNames.contains(RUN_HISTORY_STORE_NAME)) {
          const store = database.createObjectStore(RUN_HISTORY_STORE_NAME, { keyPath: "run_id" });
          store.createIndex(RUN_HISTORY_INDEX_NAME, "saved_at");
        } else if (event.oldVersion < 4) {
          // The user explicitly chose to remove schema-v3 Main/Todo history rather
          // than mix it with the new Orchestration workspace contract.
          request.transaction?.objectStore(RUN_HISTORY_STORE_NAME).clear();
        }
      };
      request.onsuccess = () => {
        this.database = request.result;
        this.database.onversionchange = () => this.close();
        resolve(request.result);
      };
      request.onerror = () => reject(request.error ?? new Error("Unable to open IndexedDB"));
      request.onblocked = () => reject(new Error("IndexedDB upgrade is blocked by another page"));
    });
    return this.databasePromise;
  }

  private nextSavedAt(): string {
    const current = this.now().getTime();
    this.lastSavedAtMilliseconds = Math.max(current, this.lastSavedAtMilliseconds + 1);
    return new Date(this.lastSavedAtMilliseconds).toISOString();
  }

  /** List newest-first valid choices and count incompatible local records. */
  async list(): Promise<RunHistoryListing> {
    const database = await this.open();
    const transaction = database.transaction(RUN_HISTORY_STORE_NAME, "readonly");
    const values = await requestValue(transaction.objectStore(RUN_HISTORY_STORE_NAME).getAll());
    await transactionFinished(transaction);
    return listingFor(values);
  }

  /** Load one complete record; malformed data is reported rather than rendered. */
  async load(runId: string): Promise<LoadedRunHistory> {
    const database = await this.open();
    const transaction = database.transaction(RUN_HISTORY_STORE_NAME, "readonly");
    const value = await requestValue(transaction.objectStore(RUN_HISTORY_STORE_NAME).get(runId));
    await transactionFinished(transaction);
    if (value === undefined) return { record: null, invalid: false };
    return isStoredRunRecord(value)
      ? { record: value, invalid: false }
      : { record: null, invalid: true };
  }

  /** Save a complete Run and atomically remove records older than the newest five. */
  async save(snapshot: ObserverSnapshot): Promise<RunHistorySaveResult> {
    if (snapshot.run === null) throw new Error("A saved observer snapshot must contain a Run");
    const database = await this.open();
    const record: StoredRunRecord = {
      storage_schema_version: RUN_HISTORY_RECORD_VERSION,
      run_id: snapshot.run.run_id,
      saved_at: this.nextSavedAt(),
      snapshot,
    };
    const transaction = database.transaction(RUN_HISTORY_STORE_NAME, "readwrite");
    const finished = transactionFinished(transaction);
    const store = transaction.objectStore(RUN_HISTORY_STORE_NAME);
    store.put(record);
    const deletedRunIds: string[] = [];

    // A key cursor reads only the saved timestamp and primary key. Loading five
    // complete Prompt/HTTP snapshots every 100ms would make persistence itself
    // a source of UI lag during long runs.
    await new Promise<void>((resolve, reject) => {
      let position = 0;
      const request = store.index(RUN_HISTORY_INDEX_NAME).openKeyCursor(null, "prev");
      request.onerror = () => reject(request.error ?? new Error("Unable to prune IndexedDB history"));
      request.onsuccess = () => {
        const cursor = request.result;
        if (cursor === null) {
          resolve();
          return;
        }
        if (position >= MAX_SAVED_RUNS) {
          const runId = String(cursor.primaryKey);
          deletedRunIds.push(runId);
          store.delete(cursor.primaryKey);
        }
        position += 1;
        cursor.continue();
      };
    });
    await finished;
    return { summary: summaryFor(record), deletedRunIds };
  }

  /** Delete one browser-local Run and return the remaining choices. */
  async delete(runId: string): Promise<RunHistoryListing> {
    const database = await this.open();
    const transaction = database.transaction(RUN_HISTORY_STORE_NAME, "readwrite");
    transaction.objectStore(RUN_HISTORY_STORE_NAME).delete(runId);
    await transactionFinished(transaction);
    return this.list();
  }

  /** Remove every browser-local observer record without touching a live test. */
  async clear(): Promise<void> {
    const database = await this.open();
    const transaction = database.transaction(RUN_HISTORY_STORE_NAME, "readwrite");
    transaction.objectStore(RUN_HISTORY_STORE_NAME).clear();
    await transactionFinished(transaction);
  }

  /** Close this page's database handle; persisted records remain available. */
  close(): void {
    this.database?.close();
    this.database = null;
    this.databasePromise = null;
  }
}

function errorText(error: unknown): string {
  if (error instanceof Error && error.message) return error.message;
  if (isObject(error) && typeof error.message === "string" && error.message) return error.message;
  return "Browser history could not be saved";
}

/**
 * Coalesce rapid reducer revisions into complete, ordered IndexedDB writes.
 * Failures are converted to status callbacks and never propagate into SSE or
 * the test runtime; a later reducer update is free to try saving again.
 */
export class RunHistoryWriter {
  private readonly persistence: RunHistoryPersistence;
  private readonly options: Required<Pick<RunHistoryWriterOptions, "delayMs">> & RunHistoryWriterOptions;
  private readonly pendingByRun = new Map<string, ObserverSnapshot>();
  private timer: number | null = null;
  private writeChain: Promise<void> = Promise.resolve();

  constructor(persistence: RunHistoryPersistence, options: RunHistoryWriterOptions = {}) {
    this.persistence = persistence;
    this.options = { ...options, delayMs: options.delayMs ?? 100 };
  }

  /** Queue the newest complete snapshot, replacing an older unsaved revision. */
  schedule(snapshot: ObserverSnapshot): void {
    if (snapshot.run === null) return;
    // Revisions of one Run replace each other, but a reset must not erase the
    // previous Run's last unsaved state from the same 100ms window.
    this.pendingByRun.set(snapshot.run.run_id, snapshot);
    if (this.timer !== null) return;
    this.timer = window.setTimeout(() => {
      this.timer = null;
      void this.flush();
    }, this.options.delayMs);
  }

  /** Write every currently pending latest value and resolve even on storage failure. */
  async flush(): Promise<void> {
    if (this.timer !== null) {
      window.clearTimeout(this.timer);
      this.timer = null;
    }
    while (this.pendingByRun.size > 0) {
      const snapshots = [...this.pendingByRun.values()];
      this.pendingByRun.clear();
      for (const snapshot of snapshots) {
        this.writeChain = this.writeChain.then(async () => {
          this.options.onSaving?.();
          try {
          const result = await this.persistence.save(snapshot);
          this.options.onSaved?.(result);
          } catch (error) {
            this.options.onError?.(errorText(error));
          }
        });
        await this.writeChain;
      }
    }
  }

  /** Drop an unsaved timer before an explicit local delete or clear action. */
  cancelPending(): void {
    if (this.timer !== null) window.clearTimeout(this.timer);
    this.timer = null;
    this.pendingByRun.clear();
  }
}
