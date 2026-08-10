import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { readThemePreference } from "../App";
import { EventCard } from "../components/EventCard";
import { CodeView } from "../components/ValueViews";
import { TodoPanel } from "../components/TodoPanel";
import { makeEvent } from "./fixtures";

async function openCard(): Promise<void> {
  await userEvent.click(screen.getByText("查看完整详情"));
}

describe("semantic event cards", () => {
  it("shows one Agent turn with a role-labelled Prompt and exact response", async () => {
    render(
      <EventCard
        event={makeEvent({
          kind: "agent_turn",
          detail: {
            input: {
              messages: [
                { role: "tool", name: "openapi.list_inputs", content: "query.state" },
                { role: "user", content: "Harness feedback" },
              ],
            },
            output: {
              content: "I will probe next.",
              structured: { next: "probe" },
              finish_reason: "tool_calls",
              tool_calls: [
                { id: "call-1", name: "restscope.http.request", arguments: { method: "GET", path: "/projects" } },
              ],
            },
          },
        })}
      />,
    );

    expect(screen.getByText("Agent")).toBeVisible();
    expect(screen.getByText("Agent · main")).toBeVisible();
    expect(screen.getAllByText("main", { exact: true })).toHaveLength(1);
    await openCard();
    expect(screen.getByText("Prompt (2)")).toBeInTheDocument();
    expect(screen.getByText("Tool result")).toBeInTheDocument();
    expect(screen.getByText("User / Harness")).toBeInTheDocument();
    await userEvent.click(screen.getByText("响应"));
    expect(screen.getByText("I will probe next.")).toBeInTheDocument();
    expect(screen.getByText("Tool calls (1)")).toBeInTheDocument();
  });

  it("keeps an HTTP tool's final Request and Response inside its Input/Output", async () => {
    render(
      <EventCard
        event={makeEvent({
          kind: "tool_call",
          name: "restscope.http.request",
          summary: "Http · restscope.http.request",
          attributes: { "restscope.tool.family": "http" },
          detail: {
            input: {
              arguments: { method: "GET", path: "/projects" },
              request: { method: "GET", url: "https://api.test/projects", query: [], headers: {}, body: null },
            },
            output: {
              tool_result: { status: "succeeded", structured: { case_id: "TC9" } },
              response: { status_code: 422, reason_phrase: "Unprocessable", headers: {}, body: { format: "json", value: { error: "invalid" } }, size_bytes: 19, body_truncated: false },
            },
          },
        })}
      />,
    );

    expect(screen.getByText("restscope.http.request")).toBeVisible();
    expect(screen.getByText("Agent · main")).toBeVisible();
    expect(screen.queryByText("main", { exact: true })).not.toBeInTheDocument();
    await openCard();
    expect(screen.getByText("GET")).toBeInTheDocument();
    expect(screen.getByText("https://api.test/projects")).toBeInTheDocument();
    await userEvent.click(screen.getByText("Output"));
    expect(screen.getByText(/HTTP 422 Unprocessable/)).toBeInTheDocument();
    expect(screen.getByText(/invalid/)).toBeInTheDocument();
  });

  it("labels stopped cards without counting them as failures visually", () => {
    render(
      <EventCard
        event={makeEvent({
          status: "warning",
          detail: { input: { messages: [] }, output: null, stopped: true },
        })}
      />,
    );

    expect(screen.getByText("已停止")).toBeVisible();
  });
});

describe("copy, follow, theme, and Todo reading", () => {
  const writeText = vi.fn().mockResolvedValue(undefined);

  beforeEach(() => {
    writeText.mockClear();
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
    window.localStorage.clear();
  });

  it("copies exact JSON text from a structured detail", async () => {
    render(<CodeView value={{ prompt: ["system", "user"] }} />);
    await userEvent.click(screen.getByRole("button", { name: "复制内容" }));

    expect(writeText).toHaveBeenCalledWith('{\n  "prompt": [\n    "system",\n    "user"\n  ]\n}');
  });

  it("defaults to dark and restores a stored light theme", () => {
    expect(readThemePreference()).toBe("dark");
    window.localStorage.setItem("restscope-observer-theme", "light");
    expect(readThemePreference()).toBe("light");
  });

  it("shows every generic Plan step and its text status", () => {
    render(
      <TodoPanel
        todo={{
          revision: 4,
          agent: { session_id: "main-1", name: "main", path: ["main"], lifecycle: "main" },
          explanation: "Follow the evidence in order.",
          items: [
            { step: "Read the schema", status: "completed" },
            { step: "Probe the endpoint", status: "in_progress" },
            { step: "Report findings", status: "pending" },
          ],
          completed_count: 1,
          total_count: 3,
          active_step: "Probe the endpoint",
          percent: 33,
        }}
      />,
    );

    expect(screen.getByText("Revision 4")).toBeVisible();
    expect(screen.getByText("Follow the evidence in order.")).toBeVisible();
    expect(screen.queryByText("当前：Probe the endpoint")).not.toBeInTheDocument();
    expect(screen.getByText("Read the schema")).toBeVisible();
    expect(screen.getByText("Probe the endpoint")).toBeVisible();
    expect(screen.getByText("Report findings")).toBeVisible();
    expect(screen.getByText("已完成")).toBeVisible();
    expect(screen.getByText("进行中")).toBeVisible();
    expect(screen.getByText("待处理")).toBeVisible();
  });
});
