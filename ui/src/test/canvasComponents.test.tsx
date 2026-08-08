import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { buildCanvasModel, eventDetailKey, messageDetailKey } from "../canvasModel";
import { BRANCH_OFFSET_X, BRANCH_RADIUS, CONNECTION_OFFSET_Y } from "../canvasLayout";
import { AgentSessionNodeView, EventCanvasNodeView } from "../components/CanvasNodes";
import { AgentMessageBody } from "../components/EventCard";
import {
  canvasNavigationBehaviors,
  detailGraphMotionOptions,
  graphDataForModel,
  liveFocusIds,
  renderStructuralGraphUpdate,
} from "../components/EventCanvas";
import { DETAIL_CLOSE_DURATION_MS, DETAIL_OPEN_DURATION_MS } from "../components/InlineReveal";
import {
  renderReactNodeSynchronously,
  unmountReactNodeSynchronously,
} from "../components/SynchronousReactNode";
import { EMPTY_FILTERS } from "../presentation";
import { makeEvent } from "./fixtures";

function canvasFixture() {
  const turn = makeEvent({
    event_id: "turn-canvas",
    order: 1,
    operation_key: "GET /projects",
    round_number: 3,
    detail: {
      input: {
        messages: [
          { role: "system", content: "Inspect safely." },
          { role: "user", content: "Probe TC1." },
        ],
      },
      output: {
        messages: [{
          role: "assistant",
          content: "I will issue one request.",
          tool_calls: [{ id: "call-http", name: "restscope.http.request", arguments: {} }],
        }],
        content: "I will issue one request.",
        tool_calls: [{ id: "call-http", name: "restscope.http.request", arguments: {} }],
        finish_reason: "tool_calls",
      },
    },
  });
  const tool = makeEvent({
    event_id: "tool-http",
    order: 2,
    kind: "tool_call",
    name: "restscope.http.request",
    parent_event_id: turn.event_id,
    agent: turn.agent,
    attributes: { "restscope.tool.family": "http" },
    detail: {
      input: {
        arguments: { method: "GET", path: "/projects" },
        request: { method: "GET", url: "https://api.test/projects", headers: {}, query: [], body: null },
      },
      output: {
        tool_result: { tool_call_id: "call-http", status: "succeeded", structured: { case_id: "TC1" } },
        response: { status_code: 200, headers: {}, body: { format: "json", value: [] } },
      },
    },
  });
  return { events: [turn, tool], model: buildCanvasModel([turn, tool], EMPTY_FILTERS, new Set()) };
}

describe("canvas node presentation", () => {
  it("commits a React node before returning control to the G6 line renderer", () => {
    const container = document.createElement("div");

    renderReactNodeSynchronously(<div>Assistant card ready</div>, container);
    expect(container).toHaveTextContent("Assistant card ready");

    renderReactNodeSynchronously(<div>Tool card updated</div>, container);
    expect(container).toHaveTextContent("Tool card updated");

    unmountReactNodeSynchronously(container);
    expect(container).toBeEmptyDOMElement();
  });

  it("scrolls for two-finger movement and zooms only for trackpad pinch", () => {
    const behaviors = canvasNavigationBehaviors();
    const scroll = behaviors.find((behavior) => (
      typeof behavior === "object" && behavior.type === "scroll-canvas"
    ));
    const zoom = behaviors.find((behavior) => (
      typeof behavior === "object" && behavior.type === "zoom-canvas"
    ));
    expect(scroll).toMatchObject({ type: "scroll-canvas", range: Infinity });
    expect(zoom).toMatchObject({ type: "zoom-canvas", trigger: [], animation: false });
    if (typeof scroll !== "object" || typeof zoom !== "object") return;

    const scrollEnabled = scroll.enable as (event: { ctrlKey?: boolean }) => boolean;
    const zoomEnabled = zoom.enable as (event: { ctrlKey?: boolean }) => boolean;
    expect(scrollEnabled({ ctrlKey: false })).toBe(true);
    expect(zoomEnabled({ ctrlKey: false })).toBe(false);
    expect(scrollEnabled({ ctrlKey: true })).toBe(false);
    expect(zoomEnabled({ ctrlKey: true })).toBe(true);
  });

  it("renders one session header and separate chronological role message cards", async () => {
    const { model } = canvasFixture();
    const agent = model.nodes.find((node) => node.kind === "agent_session");
    expect(agent?.kind).toBe("agent_session");
    if (agent?.kind !== "agent_session") return;
    const openMessage = vi.fn();
    const toggle = vi.fn();

    render(
      <AgentSessionNodeView
        node={agent}
        onOpenMessage={openMessage}
        onToggleSession={toggle}
        themeMode="dark"
      />,
    );

    expect(screen.getAllByText("Agent · FailureResolutionAgent.resolve")).toHaveLength(1);
    expect(screen.getByRole("button", { name: /System 消息/ })).toHaveTextContent("Turn 1");
    expect(screen.getByRole("button", { name: /User \/ Harness 消息/ })).toBeVisible();
    expect(screen.getByText("1 个 Tool call")).toBeVisible();
    expect(screen.getByRole("button", { name: /System 消息/ })).not.toHaveTextContent(/Input|Output/);
    expect(screen.getByRole("button", { name: /Assistant 消息/ })).not.toHaveTextContent(/Input|Output/);
    expect(document.querySelectorAll(".canvas-message-foot-spacer")).toHaveLength(3);
    await userEvent.click(screen.getByRole("button", { name: /Assistant 消息/ }));
    expect(openMessage).toHaveBeenCalledWith(
      expect.objectContaining({ id: "turn-canvas:output:0" }),
      true,
    );
    await userEvent.click(screen.getByRole("button", { name: "折叠 Agent 会话" }));
    expect(toggle).toHaveBeenCalledWith("agent-1", true);
  });

  it("shows HTTP method, final URL and response status in one Tool node", async () => {
    const { model } = canvasFixture();
    const tool = model.nodes.find((node) => node.kind === "tool_call");
    expect(tool?.kind).toBe("tool_call");
    if (tool?.kind !== "tool_call") return;
    const open = vi.fn();

    render(<EventCanvasNodeView node={tool} onOpen={open} themeMode="light" />);

    expect(screen.getByText("HTTP")).toBeVisible();
    expect(screen.getByText("GET")).toBeVisible();
    expect(screen.getByText("https://api.test/projects")).toBeVisible();
    expect(screen.getByText("HTTP 200")).toBeVisible();
    await userEvent.click(screen.getByRole("button"));
    expect(open).toHaveBeenCalledWith(tool.event, true);
  });

  it("keeps ordinary Tool input and output hidden until the node is expanded", () => {
    const event = makeEvent({
      event_id: "tool-collapsed",
      kind: "tool_call",
      name: "resource.list",
      attributes: { "restscope.tool.family": "resource" },
      detail: {
        input: { arguments: { resource_type: "project" } },
        output: { tool_result: { status: "succeeded", structured: ["project-1"] } },
      },
    });
    const collapsed = buildCanvasModel([event], EMPTY_FILTERS, new Set());
    const collapsedTool = collapsed.nodes.find((node) => node.kind === "tool_call");
    expect(collapsedTool?.kind).toBe("tool_call");
    if (collapsedTool?.kind !== "tool_call") return;

    const { rerender } = render(
      <EventCanvasNodeView node={collapsedTool} onOpen={vi.fn()} themeMode="dark" />,
    );

    expect(screen.queryByText(/^Input$/)).not.toBeInTheDocument();
    expect(screen.queryByText(/^Output$/)).not.toBeInTheDocument();
    expect(screen.queryByText(/resource_type/)).not.toBeInTheDocument();
    expect(document.querySelector(".canvas-event-node .inline-reveal"))
      .toHaveStyle({ height: "0px" });

    const expanded = buildCanvasModel(
      [event],
      EMPTY_FILTERS,
      new Set(),
      new Set([eventDetailKey(event.event_id)]),
    );
    const expandedTool = expanded.nodes.find((node) => node.kind === "tool_call");
    expect(expandedTool?.kind).toBe("tool_call");
    if (expandedTool?.kind !== "tool_call") return;
    rerender(<EventCanvasNodeView node={expandedTool} onOpen={vi.fn()} themeMode="dark" />);

    const detail = screen.getByRole("region", { name: "resource.list 完整详情" });
    expect(within(detail).getByRole("tab", { name: "Input" })).toBeVisible();
    expect(within(detail).getByRole("tab", { name: "Output" })).toBeVisible();
    expect(within(detail).getByText(/resource_type/)).toBeVisible();
  });

  it("puts the Tool edge on the exact Assistant message port in G6 data", () => {
    const { model } = canvasFixture();
    const data = graphDataForModel(model, "dark", {
      openEvent: vi.fn(),
      openMessage: vi.fn(),
      toggleSession: vi.fn(),
    });
    const agent = model.nodes.find((node) => node.kind === "agent_session");
    expect(agent?.kind).toBe("agent_session");
    if (agent?.kind !== "agent_session") return;
    const assistant = agent.messages.find((message) => message.role === "assistant");
    const agentData = data.nodes?.find((node) => node.id === agent.id);
    const ports = agentData?.style?.ports as Array<{ key: string }>;
    const headerPort = ports.find((port) => port.key === "agent_header") as {
      key: string;
      placement: [number, number];
    };

    expect(ports.map((port) => port.key)).toContain(assistant?.portKey);
    expect(headerPort.placement[1] * agent.height).toBe(CONNECTION_OFFSET_Y);
    expect(data.edges?.[0].style?.sourcePort).toBe(assistant?.portKey);
    expect(data.edges?.[0].style?.targetPort).toBe("input");
    expect(liveFocusIds(model)).toEqual(["agent:agent-1", "event:tool-http"]);
  });

  it("draws one shared rounded branch and arrows only on child segments", () => {
    const { events } = canvasFixture();
    const assistant = (events[0].detail.output as Record<string, any>).messages[0];
    assistant.tool_calls.push({ id: "call-second", name: "resource.list", arguments: {} });
    const secondTool = makeEvent({
      event_id: "tool-second",
      order: 3,
      kind: "tool_call",
      name: "resource.list",
      parent_event_id: events[0].event_id,
      agent: events[0].agent,
      detail: { output: { tool_call_id: "call-second", status: "succeeded" } },
    });
    const model = buildCanvasModel([...events, secondTool], EMPTY_FILTERS, new Set());
    const data = graphDataForModel(model, "dark", {
      openEvent: vi.fn(),
      openMessage: vi.fn(),
      toggleSession: vi.fn(),
    });
    const connectorEdges = data.edges ?? [];
    const first = connectorEdges.find((edge) => edge.data?.connectorPart === "first");
    const spine = connectorEdges.find((edge) => edge.data?.connectorPart === "spine");
    const branch = connectorEdges.find((edge) => edge.data?.connectorPart === "branch");
    const firstTarget = data.nodes?.find((node) => node.id === "event:tool-http");
    const spineEnd = data.nodes?.find((node) => node.id.includes(":spine-end"));
    const spineStart = data.nodes?.find((node) => node.id.includes(":spine-start"));
    const firstTargetSize = firstTarget?.style?.size as [number, number];
    const expectedBranchX = Number(spineEnd?.style?.x);
    const expectedFirstY = Number(firstTarget?.style?.y) - firstTargetSize[1] / 2
      + CONNECTION_OFFSET_Y;

    expect(connectorEdges).toHaveLength(3);
    expect(first?.style).toMatchObject({
      endArrow: true,
      lineWidth: 2,
      radius: BRANCH_RADIUS,
    });
    expect(spine?.style).toMatchObject({
      lineWidth: 2,
      radius: BRANCH_RADIUS,
      controlPoints: [[expectedBranchX, expectedFirstY]],
    });
    expect(spine?.style?.endArrow).toBeUndefined();
    expect(branch?.style).toMatchObject({ endArrow: true, radius: BRANCH_RADIUS });
    expect(Number(firstTarget?.style?.x) - Number(firstTargetSize[0]) / 2
      - expectedBranchX).toBe(BRANCH_OFFSET_X);
    expect(expectedBranchX - Number(spineStart?.style?.x)).toBe(BRANCH_RADIUS);
  });

  it("keeps a single-child call as one horizontal arrow without a branch spine", () => {
    const { model } = canvasFixture();
    const data = graphDataForModel(model, "light", {
      openEvent: vi.fn(),
      openMessage: vi.fn(),
      toggleSession: vi.fn(),
    });

    expect(data.edges).toHaveLength(1);
    expect(data.edges?.[0].data?.connectorPart).toBe("first");
    expect(data.edges?.[0].style?.controlPoints).toEqual([]);
    expect(data.nodes?.some((node) => node.data?.connectorJunction)).toBe(false);
  });

  it("exposes a Tool output port and labels a nested Agent edge", () => {
    const { events } = canvasFixture();
    const child = makeEvent({
      event_id: "turn-child",
      order: 3,
      agent: {
        session_id: "agent-child",
        parent_session_id: events[0].agent!.session_id,
        name: "ParameterPatchAgent.propose",
        path: [...events[0].agent!.path, "ParameterPatchAgent.propose"],
      },
      parent_event_id: events[1].event_id,
      detail: {
        input: { messages: [{ role: "system", content: "Patch." }] },
        output: { messages: [{ role: "assistant", content: "Done.", tool_calls: [] }] },
      },
    });
    const model = buildCanvasModel([...events, child], EMPTY_FILTERS, new Set());
    const data = graphDataForModel(model, "dark", {
      openEvent: vi.fn(),
      openMessage: vi.fn(),
      toggleSession: vi.fn(),
    });
    const toolData = data.nodes?.find((node) => node.id === `event:${events[1].event_id}`);
    const ports = toolData?.style?.ports as Array<{ key: string }>;
    const outputPort = ports.find((port) => port.key === "output") as {
      key: string;
      placement: [number, number];
    };
    const toolSize = toolData?.style?.size as [number, number];
    const nestedEdge = data.edges?.find((edge) => edge.target === "agent:agent-child");

    expect(ports.map((port) => port.key)).toContain("output");
    expect(outputPort.placement[1] * Number(toolSize[1]))
      .toBe(CONNECTION_OFFSET_Y);
    expect(nestedEdge?.style?.sourcePort).toBe("output");
    expect(nestedEdge?.style?.labelText).toBe("启动 Agent");
  });

  it.each([
    ["system", "  {\"safe\":true}", { safe: true }],
    ["user", "[1,2]", [1, 2]],
    ["assistant", "{\"answer\":42}", { answer: 42 }],
    ["tool", `[{"id":1}]`, [{ id: 1 }]],
  ])("formats valid %s message JSON containers", (role, content, expected) => {
    render(<AgentMessageBody message={{ role, content }} />);

    expect(document.querySelector(".code-view pre")?.textContent)
      .toBe(JSON.stringify(expected, null, 2));
  });

  it("keeps invalid containers and JSON primitives as Markdown text", () => {
    const { rerender } = render(
      <AgentMessageBody message={{ role: "tool", content: "{not json}" }} />,
    );
    expect(screen.getByText("{not json}")).toBeVisible();
    expect(document.querySelector(".code-view")).toBeNull();

    rerender(<AgentMessageBody message={{ role: "assistant", content: "42" }} />);
    expect(screen.getByText("42")).toBeVisible();
    expect(document.querySelector(".code-view")).toBeNull();
  });

  it("uses the same open and close timing for G6 node, port, and edge movement", () => {
    const opening = detailGraphMotionOptions(true, ["message_assistant"]);
    const closing = detailGraphMotionOptions(false);

    expect(opening.animation).toMatchObject({ duration: DETAIL_OPEN_DURATION_MS });
    expect(closing.animation).toMatchObject({ duration: DETAIL_CLOSE_DURATION_MS });
    expect(opening.node).toMatchObject({
      animation: {
        update: expect.arrayContaining([
          expect.objectContaining({ fields: ["x", "y"] }),
          expect.objectContaining({ shape: "key", fields: ["x", "y", "width", "height"] }),
          expect.objectContaining({ shape: "port-message_assistant", fields: ["transform"] }),
        ]),
      },
    });
    expect(opening.edge).toMatchObject({
      animation: { update: [expect.objectContaining({ fields: ["sourceNode", "targetNode"] })] },
    });
  });

  it("installs G6 motion before changing data and flushes the static geometry afterward", async () => {
    const calls: string[] = [];
    const graph = {
      destroyed: false,
      setOptions: (options: { animation?: false | { duration?: number }; layout?: unknown }) => {
        if ("layout" in options) calls.push("layout-off");
        else calls.push(options.animation === false ? "static" : "motion");
      },
      setData: () => calls.push("data"),
      render: async () => { calls.push("render"); },
      draw: async () => { calls.push("draw"); },
    };

    await renderStructuralGraphUpdate(
      graph as never,
      { nodes: [], edges: [] },
      { expanded: true, portKeys: ["message_assistant"] },
    );

    expect(calls).toEqual(["motion", "layout-off", "data", "render", "static", "draw"]);
  });

  it("expands only the selected message once inside the same visual card", () => {
    const { events } = canvasFixture();
    const model = buildCanvasModel(
      events,
      EMPTY_FILTERS,
      new Set(),
      new Set([messageDetailKey("turn-canvas:output:0")]),
    );
    const agent = model.nodes.find((node) => node.kind === "agent_session");
    expect(agent?.kind).toBe("agent_session");
    if (agent?.kind !== "agent_session") return;

    render(
      <AgentSessionNodeView
        node={agent}
        onOpenMessage={vi.fn()}
        onToggleSession={vi.fn()}
        themeMode="dark"
      />,
    );

    const detail = screen.getByRole("region", { name: "第 1 轮 Assistant 完整消息" });
    const messageCard = detail.closest(".canvas-message-card");
    expect(detail).toBeVisible();
    expect(messageCard).not.toBeNull();
    expect(messageCard).toHaveClass("is-expanded");
    expect(within(detail).getByText("I will issue one request.")).toBeVisible();
    expect(within(detail).getByText("Tool calls (1)")).toBeVisible();
    expect(within(detail).getByText(/restscope\.http\.request/)).toBeVisible();
    expect(within(detail).queryByText("Inspect safely.")).not.toBeInTheDocument();
    expect(within(detail).queryByText("Probe TC1.")).not.toBeInTheDocument();
    expect(screen.queryByText(/^Prompt/)).not.toBeInTheDocument();
    expect(screen.queryByText(/^响应$/)).not.toBeInTheDocument();
    expect(screen.queryByText(/^Input$/)).not.toBeInTheDocument();
    expect(screen.queryByText(/^Output$/)).not.toBeInTheDocument();
    expect(screen.getAllByText("I will issue one request.")).toHaveLength(1);
    expect(messageCard?.querySelector(".canvas-message-preview")).toBeNull();
    expect(messageCard?.querySelectorAll(".canvas-message-detail")).toHaveLength(1);
    expect(detail.querySelector(".ant-card")).toBeNull();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it.each([
    ["system", "System", "System policy appears in full."],
    ["user", "User / Harness", "Harness feedback appears in full."],
  ])("expands one %s message without adding turn-level detail", (role, roleLabel, content) => {
    const turn = makeEvent({
      event_id: `turn-${role}`,
      detail: {
        input: { messages: [{ role, content }] },
        output: { messages: [], content: null, tool_calls: [] },
      },
    });
    const model = buildCanvasModel(
      [turn],
      EMPTY_FILTERS,
      new Set(),
      new Set([messageDetailKey(`turn-${role}:input:0`)]),
    );
    const agent = model.nodes.find((node) => node.kind === "agent_session");
    expect(agent?.kind).toBe("agent_session");
    if (agent?.kind !== "agent_session") return;

    render(
      <AgentSessionNodeView
        node={agent}
        onOpenMessage={vi.fn()}
        onToggleSession={vi.fn()}
        themeMode="dark"
      />,
    );

    const detail = screen.getByRole("region", { name: `第 1 轮 ${roleLabel} 完整消息` });
    expect(within(detail).getByText(content)).toBeVisible();
    expect(detail.closest(".canvas-message-card")).not.toBeNull();
    expect(screen.queryByText(/^Prompt/)).not.toBeInTheDocument();
  });

  it("shows all Tool results as an expandable table in the calling Assistant", async () => {
    const caller = makeEvent({
      event_id: "turn-tool-caller",
      order: 1,
      detail: {
        input: { messages: [{ role: "user", content: "Inspect both." }] },
        output: {
          messages: [{
            role: "assistant",
            content: "I will inspect both.",
            tool_calls: [
              { id: "call-schema", name: "openapi.get_input_schema", arguments: {} },
              { id: "call-resource", name: "resource.list", arguments: {} },
            ],
          }],
        },
      },
    });
    const resultTurn = makeEvent({
      event_id: "turn-tool-result",
      order: 2,
      detail: {
        input: {
          messages: [
            {
              role: "tool",
              name: "openapi.get_input_schema",
              tool_call_id: "call-schema",
              content: { status: "succeeded", required: ["name"], type: "object" },
            },
            {
              role: "tool",
              name: "resource.list",
              tool_call_id: "call-resource",
              content: "status: failed\nresource unavailable",
            },
          ],
        },
        output: {
          messages: [{ role: "assistant", content: "I can continue now.", tool_calls: [] }],
          content: "I can continue now.",
          tool_calls: [],
        },
      },
    });
    const model = buildCanvasModel(
      [caller, resultTurn],
      EMPTY_FILTERS,
      new Set(),
      new Set([messageDetailKey("turn-tool-caller:output:0")]),
    );
    const agent = model.nodes.find((node) => node.kind === "agent_session");
    expect(agent?.kind).toBe("agent_session");
    if (agent?.kind !== "agent_session") return;

    render(
      <AgentSessionNodeView
        node={agent}
        onOpenMessage={vi.fn()}
        onToggleSession={vi.fn()}
        themeMode="light"
      />,
    );

    expect(screen.queryByRole("button", { name: /Tool result 消息详情/ })).not.toBeInTheDocument();
    const detail = screen.getByRole("region", { name: "第 1 轮 Assistant 完整消息" });
    expect(within(detail).getByText("Tool results (2)")).toBeVisible();
    expect(within(detail).getByText("openapi.get_input_schema")).toBeVisible();
    expect(within(detail).getByText("resource.list")).toBeVisible();
    expect(within(detail).getByText("成功")).toBeVisible();
    expect(within(detail).getByText("失败")).toBeVisible();
    expect(screen.getByText("2 个 Tool result")).toBeVisible();
    const expandButtons = within(detail).getAllByRole("button", { name: /Expand row/ });
    await userEvent.click(expandButtons[0]);
    expect(within(detail).getAllByText(/required/).at(-1)).toBeVisible();
  });

  it("expands complete Tool input and output vertically inside the Tool node", () => {
    const { events } = canvasFixture();
    const model = buildCanvasModel(
      events,
      EMPTY_FILTERS,
      new Set(),
      new Set([eventDetailKey("tool-http")]),
    );
    const tool = model.nodes.find((node) => node.kind === "tool_call");
    expect(tool?.kind).toBe("tool_call");
    if (tool?.kind !== "tool_call") return;

    render(<EventCanvasNodeView node={tool} onOpen={vi.fn()} themeMode="light" />);

    expect(screen.getByRole("region", { name: "restscope.http.request 完整详情" })).toBeVisible();
    const detail = screen.getByRole("region", { name: "restscope.http.request 完整详情" });
    expect(detail.closest(".canvas-event-node")).not.toBeNull();
    expect(detail.querySelector(".ant-card")).toBeNull();
    expect(screen.getByText("Tool arguments")).toBeVisible();
    expect(screen.getByText("Output")).toBeVisible();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("expands a Smoke Batch by stretching its original node", () => {
    const batch = makeEvent({
      event_id: "batch-one",
      order: 3,
      kind: "smoke_batch",
      name: "OperationTestingService.run_smoke_batch",
      detail: {
        run_id: "run-1",
        seed: 42,
        constraint_count: 1,
        case_count: 1,
        success_count: 1,
        cases: [{
          case_index: 0,
          case_id: "TC1",
          method: "GET",
          url: "https://api.test/projects",
          status: "succeeded",
          duration_ms: 12,
          request: { method: "GET", url: "https://api.test/projects", headers: {}, query: [], body: null },
          response: { status_code: 200, headers: {}, body: { format: "json", value: [] } },
          transport_error: null,
        }],
      },
    });
    const model = buildCanvasModel(
      [batch],
      EMPTY_FILTERS,
      new Set(),
      new Set([eventDetailKey(batch.event_id)]),
    );
    const batchNode = model.nodes.find((node) => node.kind === "smoke_batch");
    expect(batchNode?.kind).toBe("smoke_batch");
    if (batchNode?.kind !== "smoke_batch") return;

    render(<EventCanvasNodeView node={batchNode} onOpen={vi.fn()} themeMode="dark" />);

    const detail = screen.getByRole("region", { name: `${batch.name} 完整详情` });
    expect(within(detail).getByText("TC1")).toBeVisible();
    expect(detail.closest(".canvas-event-node")).not.toBeNull();
    expect(detail.querySelector(".ant-card")).toBeNull();
  });
});
