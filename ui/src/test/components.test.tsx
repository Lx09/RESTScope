import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { readThemePreference } from "../App";
import { EventCard } from "../components/EventCard";
import { CodeView } from "../components/ValueViews";
import { WorklistPanel } from "../components/WorklistPanel";
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
    expect(screen.getByText("Agent · FailureResolutionAgent.resolve")).toBeVisible();
    expect(screen.getAllByText("FailureResolutionAgent.resolve", { exact: true })).toHaveLength(1);
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
    expect(screen.getByText("Agent · FailureResolutionAgent.resolve")).toBeVisible();
    expect(screen.queryByText("FailureResolutionAgent.resolve", { exact: true })).not.toBeInTheDocument();
    await openCard();
    expect(screen.getByText("GET")).toBeInTheDocument();
    expect(screen.getByText("https://api.test/projects")).toBeInTheDocument();
    await userEvent.click(screen.getByText("Output"));
    expect(screen.getByText(/HTTP 422 Unprocessable/)).toBeInTheDocument();
    expect(screen.getByText(/invalid/)).toBeInTheDocument();
  });

  it("shows a Smoke Batch table and expands complete Test Case evidence", async () => {
    render(
      <EventCard
        event={makeEvent({
          kind: "smoke_batch",
          name: "OperationTestingService.run_smoke_batch",
          agent: null,
          operation_key: "POST /projects",
          status: "warning",
          detail: {
            run_id: "test-run-1",
            seed: 42,
            constraint_count: 2,
            case_count: 2,
            success_count: 1,
            cases: [
              {
                case_index: 0,
                case_id: "TC1",
                method: "POST",
                url: "https://api.test/projects?case=0",
                status: "succeeded",
                duration_ms: 12,
                request: { method: "POST", url: "https://api.test/projects?case=0", headers: {}, query: [], body: { format: "json", value: { name: "demo" } } },
                response: { status_code: 201, reason_phrase: "Created", headers: {}, body: { format: "json", value: { id: 1 } }, size_bytes: 8, body_truncated: false },
                transport_error: null,
              },
              {
                case_index: 1,
                case_id: "TC2",
                method: "POST",
                url: "https://api.test/projects?case=1",
                status: "failed",
                duration_ms: 30_000,
                request: { method: "POST", url: "https://api.test/projects?case=1", headers: {}, query: [], body: null },
                response: null,
                transport_error: { type: "TargetHTTPTimeout", message: "HTTP request timed out" },
              },
            ],
          },
        })}
      />,
    );

    expect(screen.getByText("Smoke Batch")).toBeVisible();
    await openCard();
    expect(screen.getByText("TC1")).toBeInTheDocument();
    expect(screen.getByText("TC2")).toBeInTheDocument();
    expect(screen.getByText("1 / 2 成功")).toBeInTheDocument();
    const expandButtons = screen.getAllByRole("button", { name: /Expand row/ });
    await userEvent.click(expandButtons[0]);
    expect(screen.getByText(/Request/)).toBeVisible();
    expect(screen.getByText(/Response/)).toBeVisible();
    expect(screen.getByText(/demo/)).toBeVisible();
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

describe("copy, follow, theme, and Worklist reading", () => {
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

  it("shows Failure evidence and every reference in separate readable sections", () => {
    const failureMessage = "HTTP 400: the expiration policy conflicts with the selected template";
    const longParameter = "request.body.template.configuration.expiration_policy.retention_period_in_days";
    render(
      <WorklistPanel
        worklist={{
          operation_key: "POST /api/v4/projects",
          snapshot: {
            revision: 4,
            active_item_id: "WI-002",
            items: [
              {
                item_id: "WI-002",
                source_failure_refs: ["E2", "E3"],
                test_case_refs: ["TC7", "TC8"],
                suspected_parameters: [longParameter],
                candidate_refs: ["P3", "P4"],
                progress: "Verified with a resolution probe",
                root_cause: "Enum value drifted",
                decision: { selected: "P3" },
              },
            ],
          },
          failure_messages: { E2: failureMessage },
          decided_count: 1,
          total_count: 1,
          percent: 100,
        }}
      />,
    );

    expect(screen.getByText("Revision 4")).toBeVisible();
    expect(screen.getByText("POST /api/v4/projects")).toBeVisible();
    expect(screen.getByText(/Active item/)).toHaveTextContent("WI-002");
    expect(screen.getByText("Failure")).toBeVisible();
    expect(screen.getByText(failureMessage)).toBeVisible();
    expect(screen.getByText("E2")).toBeVisible();
    expect(screen.getByText("E3")).toBeVisible();
    expect(screen.getByText("Failure detail unavailable")).toBeVisible();
    expect(screen.getByText("Test cases")).toBeVisible();
    expect(screen.getByText("TC7")).toBeVisible();
    expect(screen.getByText("TC8")).toBeVisible();
    expect(screen.getByText("Suspected parameters")).toBeVisible();
    expect(screen.getByText(longParameter)).toBeVisible();
    expect(screen.getByText("Patch candidates")).toBeVisible();
    expect(screen.getByText("P3")).toBeVisible();
    expect(screen.getByText("P4")).toBeVisible();
    expect(screen.getByText("Enum value drifted")).toBeVisible();
    expect(screen.getByText("Decision")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "复制 decision" }));
  });
});
