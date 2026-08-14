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

function snapshot(runId: string, cursor: number): ObserverSnapshot {
  return {
    schema_version: 4,
    run: {
      run_id: runId,
      status: "running",
      started_at: "2026-08-07T00:00:00.000Z",
      ended_at: null,
      request: {},
      result: null,
    },
    events: [makeEvent({
      event_id: `${runId}-event`,
      run_id: runId,
      order: cursor,
      name: `${runId}-main`,
      agent: {
        session_id: `${runId}-session`,
        parent_session_id: null,
        name: `${runId}-main`,
        profile_name: `${runId}-main`,
        lifecycle: "system",
        task_id: `${runId}-task`,
        path: [`${runId}-main`],
      },
      detail: {
        task: { task_id: `${runId}-task`, objective: `Observe ${runId}` },
        input: { messages: [] },
        output: { content: `Response for ${runId}` },
        phase: "final_answer",
      },
    })],
    orchestration: {
      revision: 1,
      goal: {
        mission: `Observe ${runId}`,
        focus: null,
        success_criteria: [{ criterion_id: "goal_1", description: "Run is visible" }],
      },
      ledger: {
        plan_revision: 1,
        run_status: "running",
        plan_revisions: [],
        milestones: [],
        tasks: [],
        attempts: [],
      },
      sessions: [{
        session_id: `${runId}-session`,
        profile_name: `${runId}-main`,
        role: "orchestrator",
        sequence: 1,
        status: "completed",
        decision_kind: "replan",
        task_id: null,
        attempt_id: null,
      }],
    },
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
    expect(screen.getByTestId("conversation-surface")).toHaveAttribute("data-run-id", "cached-run");
    expect(screen.getByTestId("conversation-surface")).toHaveAttribute("data-cursor", "7");
    expect(screen.getAllByText("历史快照 / 可能已中断").length).toBeGreaterThan(0);
  });

  it("lets a current server Run replace automatic history while retaining the cached choice", async () => {
    const store = new RunHistoryStore(factory, () => new Date("2026-08-07T00:05:00.000Z"));
    await store.save(snapshot("cached-run", 7));
    store.close();
    streamFixture.snapshot = snapshot("live-run", 12);

    render(<StrictMode><ObserverApp /></StrictMode>);

    await waitFor(() => expect(screen.getByTestId("conversation-surface")).toHaveAttribute("data-run-id", "live-run"));
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
    await waitFor(() => expect(screen.getByTestId("conversation-surface")).toHaveAttribute("data-run-id", "cached-run"));

    act(() => {
      streamFixture.dispatch?.({
        type: "stream",
        eventType: "run.reset",
        data: snapshot("new-live-run", 13).run,
        cursor: 13,
      });
    });

    expect(screen.getByTestId("conversation-surface")).toHaveAttribute("data-run-id", "cached-run");
    await userEvent.click(screen.getByRole("button", { name: "返回实时" }));
    expect(screen.getByText("等待 Orchestrator 会话")).toBeVisible();
  });
});
