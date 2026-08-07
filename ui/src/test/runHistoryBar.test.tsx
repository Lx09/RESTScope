import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { RunHistoryBar } from "../components/RunHistoryBar";
import type { RunHistorySummary } from "../runHistory";

const summaries: RunHistorySummary[] = [{
  runId: "run-history",
  savedAt: "2026-08-07T01:02:03.000Z",
  startedAt: "2026-08-07T01:00:00.000Z",
  status: "succeeded",
  operationKey: "POST /api/v4/projects",
  eventCount: 17,
}];

describe("RunHistoryBar", () => {
  it("labels a restored running record as a possibly interrupted history snapshot", () => {
    render(
      <RunHistoryBar
        summaries={summaries}
        selectedRunId="run-history"
        source="history"
        liveRunId={null}
        storageStatus="saved"
        historyRunStatus="running"
        canClear
        onSelect={vi.fn()}
        onReturnLive={vi.fn()}
        onDeleteSelected={vi.fn()}
        onClear={vi.fn()}
      />,
    );

    expect(screen.getByText("本地历史")).toBeVisible();
    expect(screen.getByText("历史快照 / 可能已中断")).toBeVisible();
    expect(screen.getByText(/包含请求凭据、Prompt、Tool 与 HTTP 详情/)).toBeVisible();
  });

  it("returns to live and confirms deletion without changing runtime state", async () => {
    const onReturnLive = vi.fn();
    const onDeleteSelected = vi.fn();
    render(
      <RunHistoryBar
        summaries={summaries}
        selectedRunId="run-history"
        source="history"
        liveRunId="run-live"
        storageStatus="saved"
        historyRunStatus="succeeded"
        canClear
        onSelect={vi.fn()}
        onReturnLive={onReturnLive}
        onDeleteSelected={onDeleteSelected}
        onClear={vi.fn()}
      />,
    );

    await userEvent.click(screen.getByRole("button", { name: "返回实时" }));
    await userEvent.click(screen.getByRole("button", { name: "删除当前历史" }));
    await userEvent.click(screen.getByRole("button", { name: "确认删除" }));

    expect(onReturnLive).toHaveBeenCalledTimes(1);
    expect(onDeleteSelected).toHaveBeenCalledTimes(1);
  });

  it("shows storage failures while leaving the history controls available", () => {
    render(
      <RunHistoryBar
        summaries={summaries}
        selectedRunId={null}
        source="live"
        liveRunId="run-live"
        storageStatus="error"
        historyRunStatus={null}
        canClear
        onSelect={vi.fn()}
        onReturnLive={vi.fn()}
        onDeleteSelected={vi.fn()}
        onClear={vi.fn()}
      />,
    );

    expect(screen.getByText("保存失败")).toBeVisible();
    expect(screen.getByRole("combobox", { name: "选择实时或历史运行" })).toBeEnabled();
  });
});
