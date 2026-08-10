import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ConversationView } from "../components/ConversationView";
import { FloatingTodo } from "../components/FloatingTodo";
import type { ConversationItem } from "../conversationProjector";
import { makeEvent } from "./fixtures";

describe("Codex-style conversation components", () => {
  it("shows italic raw Reasoning by default and lets the user collapse it", async () => {
    const event = makeEvent({
      event_id: "reasoned-turn",
      detail: { reasoning: "Full private reasoning", output: null },
    });
    const items: ConversationItem[] = [{
      id: "reasoning:reasoned-turn",
      kind: "reasoning",
      order: 1,
      sessionId: "main-1",
      event,
    }];

    render(<ConversationView items={items} virtualize={false} />);

    expect(screen.queryByText("Reasoning")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("bulb")).not.toBeInTheDocument();
    expect(document.querySelector(".ant-collapse-arrow")).not.toBeInTheDocument();
    expect(screen.getAllByText("Full private reasoning")).toHaveLength(1);
    expect(screen.getByText("Full private reasoning", { selector: ".reasoning-prose *" }))
      .toBeVisible();
    expect(document.querySelector(".reasoning-prose")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "复制 Reasoning" })).not.toBeInTheDocument();
    const reasoningToggle = document.querySelector<HTMLElement>(".reasoning-content-toggle");
    expect(reasoningToggle).not.toBeNull();
    if (!reasoningToggle) throw new Error("Reasoning toggle was not rendered");
    expect(reasoningToggle).toHaveAttribute("aria-expanded", "true");
    await userEvent.click(reasoningToggle);
    expect(screen.getByLabelText("展开推理内容")).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText("Full private reasoning")).not.toBeInTheDocument();
  });

  it("shows prompt and response content without message-type annotations", () => {
    render(
      <ConversationView
        items={[
          {
            id: "task:one",
            kind: "prompt",
            order: 1,
            sessionId: "main-1",
            message: { role: "user", content: "Inspect the API" },
          },
          {
            id: "final:one",
            kind: "final_answer",
            order: 2,
            sessionId: "main-1",
            event: makeEvent({ detail: { output: { content: "Inspection complete" } } }),
          },
        ]}
        virtualize={false}
      />,
    );

    expect(screen.getByText("Inspect the API")).toBeVisible();
    expect(screen.getByText("Inspection complete")).toBeVisible();
    expect(screen.queryByText("User Task")).not.toBeInTheDocument();
    expect(screen.queryByText("Final Answer")).not.toBeInTheDocument();
    expect(screen.queryByText("Commentary Update")).not.toBeInTheDocument();
  });

  it("keeps a Tool call collapsed without a chevron and expands its detail", async () => {
    render(
      <ConversationView
        items={[{
          id: "tool:one",
          kind: "tool",
          order: 1,
          sessionId: "main-1",
          event: makeEvent({
            event_id: "tool-one",
            kind: "tool_call",
            name: "restscope.http.request",
            detail: { input: { method: "GET" }, output: { status: 200 } },
          }),
        }]}
        virtualize={false}
      />,
    );

    expect(screen.getByText("restscope.http.request")).toBeVisible();
    expect(document.querySelector(".tool-item .ant-collapse-item-active")).not.toBeInTheDocument();
    expect(document.querySelector(".ant-collapse-arrow")).not.toBeInTheDocument();
    await userEvent.click(screen.getByText("restscope.http.request"));
    expect(document.querySelector(".tool-item .ant-collapse-item-active")).toBeInTheDocument();
  });

  it("shows System Agent status inside the HTTP Tool and opens its session", async () => {
    const openSystemAgent = vi.fn();
    render(
      <ConversationView
        items={[{
          id: "tool:http",
          kind: "tool",
          order: 1,
          sessionId: "main-1",
          event: makeEvent({
            event_id: "http",
            kind: "tool_call",
            name: "restscope.http.request",
          }),
          systemAgents: [{
            sessionId: "system-1",
            profileName: "resource-identifier-selector",
            status: "succeeded",
          }],
        }]}
        onOpenSystemAgent={openSystemAgent}
        virtualize={false}
      />,
    );

    expect(screen.getByTitle("1 个 System Agent 会话")).toBeVisible();
    await userEvent.click(screen.getByText("restscope.http.request"));
    expect(screen.getByText("成功")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", {
      name: "打开 resource-identifier-selector System Agent 会话",
    }));
    expect(openSystemAgent).toHaveBeenCalledWith("system-1");
  });

  it("shows the child Agent name and opens its Drawer target without protocol text", async () => {
    const openSubagent = vi.fn();
    render(
      <ConversationView
        items={[{
          id: "subagent:child-1",
          kind: "subagent",
          order: 1,
          sessionId: "main-1",
          childSessionId: "child-1",
          childProfileName: "researcher",
        }]}
        onOpenSubagent={openSubagent}
        virtualize={false}
      />,
    );

    expect(screen.getByText("researcher")).toBeVisible();
    expect(screen.queryByText("subagent.start")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "打开 researcher 子会话" }));
    expect(openSubagent).toHaveBeenCalledWith("child-1");
  });
});

describe("floating Todo", () => {
  it("is absent without state and opens a historical read-only Drawer", async () => {
    const { rerender } = render(<FloatingTodo historical={false} todo={null} />);
    expect(screen.queryByRole("button", { name: /打开 Todo/ })).not.toBeInTheDocument();

    rerender(
      <FloatingTodo
        historical
        todo={{
          revision: 3,
          agent: { session_id: "main-1", name: "main", path: ["main"], lifecycle: "main" },
          explanation: "Verify before reporting.",
          items: [
            { step: "Read schema", status: "completed" },
            { step: "Probe endpoint", status: "in_progress" },
          ],
          completed_count: 1,
          total_count: 2,
          active_step: "Probe endpoint",
          percent: 50,
        }}
      />,
    );
    await userEvent.click(screen.getByRole("button", { name: /打开 Todo/ }));

    expect(await screen.findByText("历史 · 只读")).toBeVisible();
    expect(screen.getByText("Revision 3")).toBeVisible();
    expect(screen.queryByText("当前：Probe endpoint")).not.toBeInTheDocument();
  });
});
