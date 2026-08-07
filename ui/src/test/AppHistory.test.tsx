import { IDBFactory as FakeIDBFactory } from "fake-indexeddb";
import { StrictMode } from "react";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import ObserverApp from "../App";
import { RunHistoryStore } from "../runHistory";
import type { ObserverSnapshot } from "../types";
import { makeEvent } from "./fixtures";

const streamFixture = vi.hoisted(() => ({
  snapshot: null as unknown,
  dispatch: null as null | ((action: unknown) => void),
}));

vi.mock("../stream", () => ({
  connectLiveRun: vi.fn(async (dispatch, onStatus, signal?: AbortSignal) => {
    streamFixture.dispatch = dispatch;
    onStatus("connecting");
    if (!signal?.aborted && streamFixture.snapshot !== null) {
      dispatch({ type: "snapshot", snapshot: streamFixture.snapshot });
    }
    if (!signal?.aborted) onStatus("live");
    return { close: vi.fn() };
  }),
}));

vi.mock("../components/EventCanvas", () => ({
  EventCanvas: ({ runId, latestCursor }: { runId: string | null; latestCursor: number }) => (
    <div data-testid="event-canvas">{runId ?? "no-run"}:{latestCursor}</div>
  ),
}));

vi.mock("../components/WorklistPanel", () => ({
  WorklistPanel: () => <aside data-testid="worklist" />,
}));

function snapshot(runId: string, cursor: number): ObserverSnapshot {
  return {
    schema_version: 2,
    run: {
      run_id: runId,
      status: "running",
      started_at: "2026-08-07T00:00:00.000Z",
      ended_at: null,
      request: {},
      result: null,
    },
    events: [makeEvent({ event_id: `${runId}-event`, run_id: runId, order: cursor })],
    worklist: null,
    latest_cursor: cursor,
  };
}

describe("ObserverApp browser history lifecycle", () => {
  let factory: IDBFactory;

  beforeEach(() => {
    factory = new FakeIDBFactory();
    Object.defineProperty(window, "indexedDB", { configurable: true, value: factory });
    streamFixture.snapshot = null;
    streamFixture.dispatch = null;
  });

  it("survives React StrictMode and restores the newest snapshot when no backend Run exists", async () => {
    const store = new RunHistoryStore(factory, () => new Date("2026-08-07T00:05:00.000Z"));
    await store.save(snapshot("cached-run", 7));
    store.close();

    render(<StrictMode><ObserverApp /></StrictMode>);

    expect(await screen.findByText("本地历史")).toBeVisible();
    expect(screen.getByTestId("event-canvas")).toHaveTextContent("cached-run:7");
    expect(screen.getAllByText("历史快照 / 可能已中断").length).toBeGreaterThan(0);
  });

  it("lets a current server Run replace automatic history while retaining the cached choice", async () => {
    const store = new RunHistoryStore(factory, () => new Date("2026-08-07T00:05:00.000Z"));
    await store.save(snapshot("cached-run", 7));
    store.close();
    streamFixture.snapshot = snapshot("live-run", 12);

    render(<StrictMode><ObserverApp /></StrictMode>);

    await waitFor(() => expect(screen.getByTestId("event-canvas")).toHaveTextContent("live-run:12"));
    expect(screen.queryByText("本地历史")).not.toBeInTheDocument();
    const selector = screen.getByRole("combobox", { name: "选择实时或历史运行" });
    expect(selector).toBeEnabled();
    await userEvent.click(selector);
    expect(await screen.findByText(/cached-run/)).toBeInTheDocument();
  });

  it("keeps an explicitly selected history frozen while a new live Run arrives", async () => {
    const store = new RunHistoryStore(factory, () => new Date("2026-08-07T00:05:00.000Z"));
    await store.save(snapshot("cached-run", 7));
    store.close();
    streamFixture.snapshot = snapshot("live-run", 12);

    render(<StrictMode><ObserverApp /></StrictMode>);
    const selector = screen.getByRole("combobox", { name: "选择实时或历史运行" });
    await userEvent.click(selector);
    await userEvent.click(await screen.findByText(/cached-run/));
    await waitFor(() => expect(screen.getByTestId("event-canvas")).toHaveTextContent("cached-run:7"));

    act(() => {
      streamFixture.dispatch?.({
        type: "stream",
        eventType: "run.reset",
        data: snapshot("new-live-run", 13).run,
        cursor: 13,
      });
    });

    expect(screen.getByTestId("event-canvas")).toHaveTextContent("cached-run:7");
    await userEvent.click(screen.getByRole("button", { name: "返回实时" }));
    expect(screen.getByTestId("event-canvas")).toHaveTextContent("new-live-run:13");
  });
});
