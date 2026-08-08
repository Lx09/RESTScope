import { describe, expect, it } from "vitest";

import {
  buildCanvasModel,
  AGENT_MESSAGE_COLLAPSED_CONTENT_HEIGHT,
  EVENT_COLLAPSED_CONTENT_HEIGHT,
  compactMessagePreview,
  EVENT_NODE_HEIGHT,
  INLINE_AGENT_DETAIL_HEIGHT,
  INLINE_EVENT_DETAIL_HEIGHT,
  MESSAGE_PREVIEW_LIMIT,
  eventDetailKey,
  messageDetailKey,
  messagePortPlacement,
} from "../canvasModel";
import {
  CALL_GROUP_GAP,
  COLUMN_GAP,
  CONNECTION_OFFSET_Y,
  layoutCanvasModel,
} from "../canvasLayout";
import { EMPTY_FILTERS } from "../presentation";
import { makeEvent } from "./fixtures";

function agentTurn(
  eventId: string,
  order: number,
  sessionId: string,
  inputMessages: Record<string, unknown>[],
  assistant: Record<string, unknown>,
) {
  return makeEvent({
    event_id: eventId,
    order,
    agent: {
      session_id: sessionId,
      name: "FailureResolutionAgent.resolve",
      path: ["OperationSmokeCoordinator.run", "FailureResolutionAgent.resolve"],
    },
    operation_key: "POST /api/v4/projects",
    round_number: 2,
    detail: {
      input: { messages: inputMessages },
      output: {
        messages: [assistant],
        content: assistant.content ?? null,
        tool_calls: assistant.tool_calls ?? [],
        finish_reason: assistant.tool_calls ? "tool_calls" : "stop",
      },
    },
  });
}

describe("Agent-session canvas model", () => {
  it("limits collapsed previews to 160 Unicode characters without splitting emoji", () => {
    const exactAscii = "a".repeat(MESSAGE_PREVIEW_LIMIT);
    const longAscii = `${exactAscii}b`;
    const exactEmoji = "🧪".repeat(MESSAGE_PREVIEW_LIMIT);
    const longEmoji = `${exactEmoji}🔬`;

    expect(compactMessagePreview(exactAscii)).toBe(exactAscii);
    expect(compactMessagePreview(longAscii)).toBe(`${exactAscii}…`);
    expect(compactMessagePreview(exactEmoji)).toBe(exactEmoji);
    expect(compactMessagePreview(longEmoji)).toBe(`${exactEmoji}…`);
    expect(compactMessagePreview("  line one\n\nline two  ")).toBe("line one line two");
    expect(compactMessagePreview("")).toBe("（空消息）");
    expect(compactMessagePreview(null)).toBe("（空消息）");
  });

  it("folds Tool results into the calling Assistant instead of separate cards", () => {
    const first = agentTurn(
      "turn-1",
      1,
      "session-1",
      [
        { role: "system", content: "Inspect the failure." },
        { role: "user", content: "Resolve E1." },
      ],
      {
        role: "assistant",
        content: "I need the schema.",
        tool_calls: [{ id: "call-schema", name: "openapi.get_input_schema", arguments: {} }],
      },
    );
    const second = agentTurn(
      "turn-2",
      3,
      "session-1",
      [
        {
          role: "assistant",
          content: "I need the schema.",
          tool_calls: [{ id: "call-schema", name: "openapi.get_input_schema", arguments: {} }],
        },
        {
          role: "tool",
          name: "openapi.get_input_schema",
          tool_call_id: "call-schema",
          content: "name is required",
        },
        { role: "user", content: "Harness feedback: continue." },
      ],
      { role: "assistant", content: "The missing name is the root cause.", tool_calls: [] },
    );

    const model = buildCanvasModel([first, second], EMPTY_FILTERS, new Set());
    const agent = model.nodes.find((node) => node.kind === "agent_session");

    expect(agent?.kind).toBe("agent_session");
    if (!agent || agent.kind !== "agent_session") return;
    expect(agent.turns.map((turn) => turn.event_id)).toEqual(["turn-1", "turn-2"]);
    expect(agent.messages.map((message) => message.role)).toEqual([
      "system",
      "user",
      "assistant",
      "user",
      "assistant",
    ]);
    expect(agent.messages.map((message) => message.id)).toEqual([
      "turn-1:input:0",
      "turn-1:input:1",
      "turn-1:output:0",
      "turn-2:input:2",
      "turn-2:output:0",
    ]);
    expect(agent.messages[2].toolResults).toEqual([{
      id: "turn-2:input:1",
      toolCallId: "call-schema",
      name: "openapi.get_input_schema",
      matched: true,
      message: expect.objectContaining({ content: "name is required" }),
    }]);
    expect(agent.messages[3]).toMatchObject({
      role: "user",
      turnEventId: "turn-2",
    });
    expect(agent.messages.every((message) => message.exactMatch === false)).toBe(true);
  });

  it("does not duplicate an Assistant tool-call message echoed in the next prompt", () => {
    const caller = {
      role: "assistant",
      content: "Inspecting both sources.",
      tool_calls: [
        { id: "call-a", name: "openapi.get_input_schema", arguments: { input: "a" } },
        { id: "call-b", name: "openapi.get_input_schema", arguments: { input: "b" } },
      ],
    };
    const first = agentTurn(
      "turn-echo-1",
      1,
      "session-echo",
      [{ role: "user", content: "Inspect." }],
      caller,
    );
    const second = agentTurn(
      "turn-echo-2",
      2,
      "session-echo",
      [
        { ...caller },
        { role: "tool", tool_call_id: "call-a", content: "{\"name\":\"a\"}" },
        { role: "tool", tool_call_id: "call-b", content: "{\"name\":\"b\"}" },
      ],
      { role: "assistant", content: "Done.", tool_calls: [] },
    );

    const model = buildCanvasModel([first, second], EMPTY_FILTERS, new Set());
    const agent = model.nodes.find((node) => node.kind === "agent_session");

    expect(agent?.kind).toBe("agent_session");
    if (agent?.kind !== "agent_session") return;
    expect(agent.messages.filter((message) => message.role === "assistant")).toHaveLength(2);
    expect(agent.messages.find((message) => message.id === "turn-echo-1:output:0")?.toolResults)
      .toEqual([
        expect.objectContaining({ toolCallId: "call-a", name: "openapi.get_input_schema" }),
        expect.objectContaining({ toolCallId: "call-b", name: "openapi.get_input_schema" }),
      ]);
    expect(agent.messages.some((message) => message.id === "turn-echo-2:input:0")).toBe(false);
  });

  it("keeps an unmatched Tool result on the nearest calling Assistant without a Tool card", () => {
    const first = agentTurn(
      "turn-unmatched-call",
      1,
      "session-unmatched-result",
      [{ role: "user", content: "Inspect." }],
      {
        role: "assistant",
        content: "Calling.",
        tool_calls: [{ name: "resource.list", arguments: {} }],
      },
    );
    const second = agentTurn(
      "turn-unmatched-result",
      2,
      "session-unmatched-result",
      [{ role: "tool", name: "resource.list", content: "[]" }],
      { role: "assistant", content: "Done.", tool_calls: [] },
    );

    const model = buildCanvasModel([first, second], EMPTY_FILTERS, new Set());
    const agent = model.nodes.find((node) => node.kind === "agent_session");

    expect(agent?.kind).toBe("agent_session");
    if (agent?.kind !== "agent_session") return;
    expect(agent.messages.some((message) => message.role === "tool")).toBe(false);
    expect(agent.messages.find((message) => message.role === "assistant")?.toolResults)
      .toEqual([expect.objectContaining({ name: "resource.list", matched: false })]);
  });

  it("keeps same-named independent sessions as separate Agent nodes", () => {
    const first = agentTurn(
      "turn-a",
      1,
      "session-a",
      [{ role: "user", content: "A" }],
      { role: "assistant", content: "A done", tool_calls: [] },
    );
    const second = agentTurn(
      "turn-b",
      2,
      "session-b",
      [{ role: "user", content: "B" }],
      { role: "assistant", content: "B done", tool_calls: [] },
    );

    const model = buildCanvasModel([first, second], EMPTY_FILTERS, new Set());

    expect(model.nodes.filter((node) => node.kind === "agent_session").map((node) => node.id))
      .toEqual(["agent:session-a", "agent:session-b"]);
  });

  it("connects parallel Tool executions to the exact calling Assistant message port", () => {
    const turn = agentTurn(
      "turn-tools",
      1,
      "session-tools",
      [{ role: "user", content: "Inspect both sources." }],
      {
        role: "assistant",
        content: "Calling both tools.",
        tool_calls: [
          { id: "call-schema", name: "openapi.get_input_schema", arguments: {} },
          { id: "call-case", name: "test_case.get", arguments: { case_id: "TC1" } },
        ],
      },
    );
    const schemaTool = makeEvent({
      event_id: "tool-schema",
      order: 2,
      kind: "tool_call",
      name: "openapi.get_input_schema",
      parent_event_id: "turn-tools",
      agent: turn.agent,
      detail: { input: {}, output: { tool_call_id: "call-schema", status: "succeeded" } },
    });
    const caseTool = makeEvent({
      event_id: "tool-case",
      order: 3,
      kind: "tool_call",
      name: "test_case.get",
      parent_event_id: "turn-tools",
      agent: turn.agent,
      detail: { input: {}, output: { tool_call_id: "call-case", status: "succeeded" } },
    });

    const model = buildCanvasModel([turn, schemaTool, caseTool], EMPTY_FILTERS, new Set());
    const agent = model.nodes
      .find((node) => node.kind === "agent_session" && node.sessionId === "session-tools");
    const assistant = agent?.kind === "agent_session"
      ? agent.messages.find((message) => message.role === "assistant")
      : undefined;
    const toolEdges = model.edges.filter((edge) => edge.relationship === "tool_call");

    expect(assistant).toBeDefined();
    expect(toolEdges).toHaveLength(2);
    expect(toolEdges.map((edge) => edge.source)).toEqual([
      "agent:session-tools",
      "agent:session-tools",
    ]);
    expect(toolEdges.map((edge) => edge.sourcePort)).toEqual([
      assistant?.portKey,
      assistant?.portKey,
    ]);
    expect(toolEdges.map((edge) => edge.target)).toEqual([
      "event:tool-schema",
      "event:tool-case",
    ]);
    expect(toolEdges.every((edge) => edge.fallback === false)).toBe(true);
  });

  it("falls back to the parent turn Assistant and never draws a Tool-result return edge", () => {
    const first = agentTurn(
      "turn-parent",
      1,
      "session-fallback",
      [{ role: "user", content: "Call it." }],
      { role: "assistant", content: "Calling now.", tool_calls: [{ name: "resource.list", arguments: {} }] },
    );
    const tool = makeEvent({
      event_id: "tool-fallback",
      order: 2,
      kind: "tool_call",
      name: "resource.list",
      parent_event_id: "turn-parent",
      agent: first.agent,
      detail: { input: {}, output: { status: "succeeded" } },
    });
    const next = agentTurn(
      "turn-result",
      3,
      "session-fallback",
      [{ role: "tool", name: "resource.list", tool_call_id: "missing-id", content: "[]" }],
      { role: "assistant", content: "No resources found.", tool_calls: [] },
    );

    const model = buildCanvasModel([first, tool, next], EMPTY_FILTERS, new Set());
    const edge = model.edges.find((candidate) => candidate.target === "event:tool-fallback");

    expect(edge).toMatchObject({
      source: "agent:session-fallback",
      relationship: "tool_call",
      fallback: false,
    });
    expect(model.edges.some((candidate) => candidate.source === "event:tool-fallback")).toBe(false);
  });

  it("keeps a calling Agent and highlights its source message as Tool-filter context", () => {
    const turn = agentTurn(
      "turn-http",
      1,
      "session-http",
      [{ role: "user", content: "Probe the endpoint." }],
      {
        role: "assistant",
        content: "I will probe.",
        tool_calls: [{ id: "call-http", name: "restscope.http.request", arguments: {} }],
      },
    );
    const tool = makeEvent({
      event_id: "tool-http",
      order: 2,
      kind: "tool_call",
      name: "restscope.http.request",
      parent_event_id: "turn-http",
      agent: turn.agent,
      attributes: { "restscope.tool.family": "http" },
      detail: { output: { tool_call_id: "call-http", status: "succeeded" } },
    });

    const model = buildCanvasModel(
      [turn, tool],
      { ...EMPTY_FILTERS, toolFamilies: ["http"] },
      new Set(["session-http"]),
    );
    const agent = model.nodes.find((node) => node.kind === "agent_session");

    expect(model.matchCount).toBe(1);
    expect(agent?.kind).toBe("agent_session");
    if (!agent || agent.kind !== "agent_session") return;
    expect(agent.contextOnly).toBe(true);
    expect(agent.collapsed).toBe(false);
    expect(agent.messages.find((message) => message.role === "assistant")?.connectionContext)
      .toBe(true);
  });

  it("honors manual collapse in the unfiltered view and hides the Agent Tool subtree", () => {
    const turn = agentTurn(
      "turn-collapse",
      1,
      "session-collapse",
      [{ role: "user", content: "Call it." }],
      {
        role: "assistant",
        content: "Calling.",
        tool_calls: [{ id: "call-collapse", name: "resource.list", arguments: {} }],
      },
    );
    const tool = makeEvent({
      event_id: "tool-collapse",
      order: 2,
      kind: "tool_call",
      name: "resource.list",
      parent_event_id: turn.event_id,
      agent: turn.agent,
      detail: { output: { tool_call_id: "call-collapse" } },
    });

    const model = buildCanvasModel(
      [turn, tool],
      EMPTY_FILTERS,
      new Set(["session-collapse"]),
    );
    const agent = model.nodes.find((node) => node.kind === "agent_session");

    expect(agent?.kind).toBe("agent_session");
    if (agent?.kind !== "agent_session") return;
    expect(agent.collapsed).toBe(true);
    expect(agent.hiddenDescendantCount).toBe(1);
    expect(model.nodes.some((node) => node.id === "event:tool-collapse")).toBe(false);
    expect(model.edges).toHaveLength(0);
  });

  it("uses the Agent header fallback when the calling Assistant message is unavailable", () => {
    const turn = agentTurn(
      "turn-without-output",
      1,
      "session-no-output",
      [{ role: "user", content: "Call the tool." }],
      {},
    );
    turn.detail.output = null;
    const tool = makeEvent({
      event_id: "tool-without-call",
      order: 2,
      kind: "tool_call",
      name: "resource.list",
      parent_event_id: turn.event_id,
      agent: turn.agent,
    });

    const model = buildCanvasModel([turn, tool], EMPTY_FILTERS, new Set());

    expect(model.edges[0]).toMatchObject({
      sourcePort: "agent_header",
      fallback: true,
    });
  });

  it("leaves an Agent-less Tool as a root and connects a nested Agent from its parent message", () => {
    const parent = agentTurn(
      "turn-parent-agent",
      1,
      "session-parent",
      [{ role: "user", content: "Delegate." }],
      { role: "assistant", content: "Starting a nested Agent.", tool_calls: [] },
    );
    const child = agentTurn(
      "turn-child-agent",
      2,
      "session-child",
      [{ role: "system", content: "Investigate." }],
      { role: "assistant", content: "Done.", tool_calls: [] },
    );
    child.parent_event_id = parent.event_id;
    const rootTool = makeEvent({
      event_id: "root-tool",
      order: 3,
      kind: "tool_call",
      name: "mcp.external.inspect",
      parent_event_id: null,
      agent: null,
    });

    const model = buildCanvasModel([parent, child, rootTool], EMPTY_FILTERS, new Set());

    expect(model.edges).toHaveLength(1);
    expect(model.edges[0]).toMatchObject({
      source: "agent:session-parent",
      target: "agent:session-child",
      relationship: "nested_agent",
    });
    expect(model.edges.some((edge) => edge.target === "event:root-tool")).toBe(false);
  });

  it("keeps the exact Agent message to Tool to child Agent chain", () => {
    const parent = agentTurn(
      "turn-parent-chain",
      1,
      "session-parent-chain",
      [{ role: "user", content: "Draft a patch." }],
      {
        role: "assistant",
        content: "Starting patch work.",
        tool_calls: [{ id: "call-patch", name: "failure_resolution.draft_parameter_patch", arguments: {} }],
      },
    );
    const tool = makeEvent({
      event_id: "tool-patch-chain",
      order: 2,
      kind: "tool_call",
      name: "failure_resolution.draft_parameter_patch",
      parent_event_id: parent.event_id,
      agent: parent.agent,
      detail: { output: { tool_call_id: "call-patch", status: "succeeded" } },
    });
    const child = agentTurn(
      "turn-child-chain",
      3,
      "session-child-chain",
      [{ role: "system", content: "Propose the patch." }],
      { role: "assistant", content: "Patch ready.", tool_calls: [] },
    );
    child.parent_event_id = tool.event_id;
    child.agent = {
      ...child.agent!,
      parent_session_id: parent.agent!.session_id,
      path: [...parent.agent!.path, "ParameterPatchAgent.propose"],
    };

    const model = buildCanvasModel([parent, tool, child], EMPTY_FILTERS, new Set());

    expect(model.edges).toHaveLength(2);
    expect(model.edges[0]).toMatchObject({
      source: "agent:session-parent-chain",
      target: "event:tool-patch-chain",
      relationship: "tool_call",
    });
    expect(model.edges[1]).toMatchObject({
      source: "event:tool-patch-chain",
      sourcePort: "output",
      target: "agent:session-child-chain",
      relationship: "nested_agent",
      fallback: false,
    });
    expect(model.edges.some((edge) => (
      edge.source === "agent:session-parent-chain"
      && edge.target === "agent:session-child-chain"
    ))).toBe(false);
    const layout = layoutCanvasModel(model);
    expect(layout.positions.get("agent:session-parent-chain")?.column).toBe(0);
    expect(layout.positions.get("event:tool-patch-chain")?.column).toBe(1);
    expect(layout.positions.get("agent:session-child-chain")?.column).toBe(2);
  });

  it("falls back from the parent Agent header for a direct nested Agent", () => {
    const parent = agentTurn(
      "turn-parent-direct",
      1,
      "session-parent-direct",
      [{ role: "user", content: "Compact this." }],
      { role: "assistant", content: "Compaction is needed.", tool_calls: [] },
    );
    const child = agentTurn(
      "turn-child-direct",
      2,
      "session-child-direct",
      [{ role: "system", content: "Compact." }],
      { role: "assistant", content: "Summary", tool_calls: [] },
    );
    child.agent = {
      ...child.agent!,
      parent_session_id: parent.agent!.session_id,
      path: [...parent.agent!.path, "FailureResolutionCompactAgent.run"],
    };

    const model = buildCanvasModel([parent, child], EMPTY_FILTERS, new Set());

    expect(model.edges).toHaveLength(1);
    expect(model.edges[0]).toMatchObject({
      source: "agent:session-parent-direct",
      sourcePort: "agent_header",
      target: "agent:session-child-direct",
      relationship: "nested_agent",
      fallback: true,
    });
    expect(layoutCanvasModel(model).positions.get("agent:session-child-direct")?.column)
      .toBe(1);
  });

  it("assigns stable columns per Assistant message call group", () => {
    const first = agentTurn(
      "turn-group-one",
      1,
      "session-groups",
      [{ role: "user", content: "Inspect both." }],
      {
        role: "assistant",
        content: "First group.",
        tool_calls: [
          { id: "call-a", name: "resource.list", arguments: {} },
          { id: "call-b", name: "openapi.list_inputs", arguments: {} },
        ],
      },
    );
    const firstTool = makeEvent({
      event_id: "tool-group-a",
      order: 2,
      kind: "tool_call",
      name: "resource.list",
      parent_event_id: first.event_id,
      agent: first.agent,
      detail: { output: { tool_call_id: "call-a" } },
    });
    const parallelTool = makeEvent({
      event_id: "tool-group-b",
      order: 3,
      kind: "tool_call",
      name: "openapi.list_inputs",
      parent_event_id: first.event_id,
      agent: first.agent,
      detail: { output: { tool_call_id: "call-b" } },
    });
    const second = agentTurn(
      "turn-group-two",
      4,
      "session-groups",
      [{ role: "tool", tool_call_id: "call-a", content: "[]" }],
      {
        role: "assistant",
        content: "Second group.",
        tool_calls: [{ id: "call-c", name: "test_case.get", arguments: {} }],
      },
    );
    const laterTool = makeEvent({
      event_id: "tool-group-c",
      order: 5,
      kind: "tool_call",
      name: "test_case.get",
      parent_event_id: second.event_id,
      agent: second.agent,
      detail: { output: { tool_call_id: "call-c" } },
    });

    const unfiltered = buildCanvasModel(
      [first, firstTool, parallelTool, second, laterTool],
      EMPTY_FILTERS,
      new Set(),
    );
    const filtered = buildCanvasModel(
      [first, firstTool, parallelTool, second, laterTool],
      { ...EMPTY_FILTERS, search: "test_case.get" },
      new Set(),
    );
    const unfilteredLayout = layoutCanvasModel(unfiltered);
    const filteredLayout = layoutCanvasModel(filtered);
    const revisedLater = {
      ...laterTool,
      revision: laterTool.revision + 1,
      detail: { ...laterTool.detail, output: { tool_call_id: "call-c", status: "succeeded" } },
    };
    const revised = buildCanvasModel(
      [first, firstTool, parallelTool, second, revisedLater],
      EMPTY_FILTERS,
      new Set(),
    );

    expect(unfilteredLayout.positions.get("agent:session-groups")?.column).toBe(0);
    expect(unfilteredLayout.positions.get("event:tool-group-a")?.column).toBe(1);
    expect(unfilteredLayout.positions.get("event:tool-group-b")?.column).toBe(1);
    // The first parallel group still extends below the second Assistant
    // message, so first-fit moves the complete later group one column right.
    expect(unfilteredLayout.positions.get("event:tool-group-c")?.column).toBe(2);
    expect(filteredLayout.positions.get("event:tool-group-c")?.column).toBe(2);
    expect(layoutCanvasModel(revised).positions.get("event:tool-group-c")?.column).toBe(2);
  });

  it("keeps message IDs and order stable when an SSE revision updates content", () => {
    const original = agentTurn(
      "turn-revision",
      1,
      "session-revision",
      [{ role: "system", content: "System" }, { role: "user", content: "Before" }],
      { role: "assistant", content: "Working", tool_calls: [] },
    );
    const revised = agentTurn(
      "turn-revision",
      1,
      "session-revision",
      [{ role: "system", content: "System" }, { role: "user", content: "Before" }],
      { role: "assistant", content: "Complete", tool_calls: [] },
    );
    revised.revision = 4;

    const before = buildCanvasModel([original], EMPTY_FILTERS, new Set());
    const after = buildCanvasModel([revised], EMPTY_FILTERS, new Set());
    const beforeAgent = before.nodes.find((node) => node.kind === "agent_session");
    const afterAgent = after.nodes.find((node) => node.kind === "agent_session");

    expect(beforeAgent?.kind).toBe("agent_session");
    expect(afterAgent?.kind).toBe("agent_session");
    if (beforeAgent?.kind !== "agent_session" || afterAgent?.kind !== "agent_session") return;
    expect(afterAgent.messages.map((message) => message.id))
      .toEqual(beforeAgent.messages.map((message) => message.id));
    expect(afterAgent.messages.at(-1)?.preview).toBe("Complete");
  });

  it("highlights only the concrete message whose text matches a search", () => {
    const turn = agentTurn(
      "turn-search",
      1,
      "session-search",
      [{ role: "system", content: "General rules" }, { role: "user", content: "Find needle-value" }],
      { role: "assistant", content: "No match here", tool_calls: [] },
    );

    const model = buildCanvasModel(
      [turn],
      { ...EMPTY_FILTERS, search: "needle-value" },
      new Set(),
    );
    const agent = model.nodes.find((node) => node.kind === "agent_session");

    expect(agent?.kind).toBe("agent_session");
    if (agent?.kind !== "agent_session") return;
    expect(agent.messages.filter((message) => message.exactMatch).map((message) => message.role))
      .toEqual(["user"]);
  });

  it("adds vertical detail space in place and keeps later message ports aligned", () => {
    const turn = agentTurn(
      "turn-expand",
      1,
      "session-expand",
      [{ role: "system", content: "Full prompt" }, { role: "user", content: "Inspect" }],
      { role: "assistant", content: "Done", tool_calls: [] },
    );
    const tool = makeEvent({
      event_id: "tool-expand",
      order: 2,
      kind: "tool_call",
      name: "resource.list",
      parent_event_id: turn.event_id,
      agent: turn.agent,
    });
    const collapsed = buildCanvasModel([turn, tool], EMPTY_FILTERS, new Set());
    const expanded = buildCanvasModel(
      [turn, tool],
      EMPTY_FILTERS,
      new Set(),
      new Set([
        messageDetailKey("turn-expand:input:0"),
        eventDetailKey("tool-expand"),
      ]),
    );
    const collapsedAgent = collapsed.nodes.find((node) => node.kind === "agent_session");
    const collapsedTool = collapsed.nodes.find((node) => node.kind === "tool_call");
    const expandedAgent = expanded.nodes.find((node) => node.kind === "agent_session");
    const expandedTool = expanded.nodes.find((node) => node.kind === "tool_call");

    expect(collapsedAgent?.kind).toBe("agent_session");
    expect(expandedAgent?.kind).toBe("agent_session");
    expect(collapsedTool?.kind).toBe("tool_call");
    expect(expandedTool?.kind).toBe("tool_call");
    if (
      collapsedAgent?.kind !== "agent_session"
      || collapsedTool?.kind !== "tool_call"
      || expandedAgent?.kind !== "agent_session"
      || expandedTool?.kind !== "tool_call"
    ) return;
    expect(expandedAgent.height - collapsedAgent.height)
      .toBe(INLINE_AGENT_DETAIL_HEIGHT - AGENT_MESSAGE_COLLAPSED_CONTENT_HEIGHT);
    expect(expandedTool.height).toBe(
      EVENT_NODE_HEIGHT + INLINE_EVENT_DETAIL_HEIGHT - EVENT_COLLAPSED_CONTENT_HEIGHT,
    );
    expect(collapsedTool.height).toBe(EVENT_NODE_HEIGHT - EVENT_COLLAPSED_CONTENT_HEIGHT);
    expect(expandedAgent.messages[0].expanded).toBe(true);
    expect(expandedTool.expanded).toBe(true);
    expect(messagePortPlacement(expandedAgent, 1)[1])
      .toBeGreaterThan(messagePortPlacement(collapsedAgent, 1)[1]);
    expect(messagePortPlacement(expandedAgent, 0)[1] * expandedAgent.height)
      .toBeCloseTo(messagePortPlacement(collapsedAgent, 0)[1] * collapsedAgent.height);
  });
});

describe("message-anchored first-fit canvas layout", () => {
  it("top-aligns one message's parallel children in one ordered call group", () => {
    const turn = agentTurn(
      "turn-parallel-layout",
      1,
      "session-parallel-layout",
      [{ role: "user", content: "Inspect both." }],
      {
        role: "assistant",
        content: "Calling both.",
        tool_calls: [
          { id: "call-a", name: "resource.list", arguments: {} },
          { id: "call-b", name: "openapi.list_inputs", arguments: {} },
        ],
      },
    );
    const toolA = makeEvent({
      event_id: "tool-layout-a",
      order: 2,
      kind: "tool_call",
      name: "resource.list",
      parent_event_id: turn.event_id,
      agent: turn.agent,
      detail: { output: { tool_call_id: "call-a" } },
    });
    const toolB = makeEvent({
      event_id: "tool-layout-b",
      order: 3,
      kind: "tool_call",
      name: "openapi.list_inputs",
      parent_event_id: turn.event_id,
      agent: turn.agent,
      detail: { output: { tool_call_id: "call-b" } },
    });

    const layout = layoutCanvasModel(
      buildCanvasModel([turn, toolA, toolB], EMPTY_FILTERS, new Set()),
    );
    const agent = layout.positions.get("agent:session-parallel-layout")!;
    const first = layout.positions.get("event:tool-layout-a")!;
    const second = layout.positions.get("event:tool-layout-b")!;
    const group = layout.connectionGroups[0];

    expect(group.targets).toEqual(["event:tool-layout-a", "event:tool-layout-b"]);
    expect(first.column).toBe(agent.column + 1);
    expect(second.column).toBe(first.column);
    expect(first.top).toBe(group.sourceTop);
    expect(second.top).toBe(first.top + first.height + CALL_GROUP_GAP);
    expect(group.sourceY).toBe(group.sourceTop + CONNECTION_OFFSET_Y);
    expect(first.left - (agent.left + agent.width)).toBe(COLUMN_GAP);
  });

  it("moves a blocked later group right and reuses the left column once it is clear", () => {
    const first = agentTurn(
      "turn-first-layout",
      1,
      "session-first-fit",
      [{ role: "user", content: "First." }],
      {
        role: "assistant",
        content: "First call.",
        tool_calls: [{ id: "call-first", name: "resource.list", arguments: {} }],
      },
    );
    const second = agentTurn(
      "turn-second-layout",
      3,
      "session-first-fit",
      [{ role: "tool", tool_call_id: "call-first", content: "[]" }],
      {
        role: "assistant",
        content: "Second call.",
        tool_calls: [{ id: "call-second", name: "openapi.list_inputs", arguments: {} }],
      },
    );
    const third = agentTurn(
      "turn-third-layout",
      5,
      "session-first-fit",
      [
        { role: "tool", tool_call_id: "call-second", content: "[]" },
        { role: "user", content: "Enough vertical distance." },
      ],
      {
        role: "assistant",
        content: "Third call.",
        tool_calls: [{ id: "call-third", name: "test_case.get", arguments: {} }],
      },
    );
    const tools = [
      makeEvent({
        event_id: "tool-first-fit-a",
        order: 2,
        kind: "tool_call",
        name: "resource.list",
        parent_event_id: first.event_id,
        agent: first.agent,
        detail: { output: { tool_call_id: "call-first" } },
      }),
      makeEvent({
        event_id: "tool-first-fit-a-parallel",
        order: 2.5,
        kind: "tool_call",
        name: "resource.get",
        parent_event_id: first.event_id,
        agent: first.agent,
        detail: { output: { tool_call_id: "call-first" } },
      }),
      makeEvent({
        event_id: "tool-first-fit-b",
        order: 4,
        kind: "tool_call",
        name: "openapi.list_inputs",
        parent_event_id: second.event_id,
        agent: second.agent,
        detail: { output: { tool_call_id: "call-second" } },
      }),
      makeEvent({
        event_id: "tool-first-fit-c",
        order: 6,
        kind: "tool_call",
        name: "test_case.get",
        parent_event_id: third.event_id,
        agent: third.agent,
        detail: { output: { tool_call_id: "call-third" } },
      }),
    ];

    const layout = layoutCanvasModel(
      buildCanvasModel(
        [first, tools[0], tools[1], second, tools[2], third, tools[3]],
        EMPTY_FILTERS,
        new Set(),
      ),
    );

    expect(layout.positions.get("event:tool-first-fit-a")?.column).toBe(1);
    expect(layout.positions.get("event:tool-first-fit-b")?.column).toBe(2);
    expect(layout.positions.get("event:tool-first-fit-c")?.column).toBe(1);
  });

  it("temporarily pushes a later same-column group down and restores it after collapse", () => {
    const first = agentTurn(
      "turn-expand-layout-a",
      1,
      "session-expand-layout",
      [{ role: "user", content: "First." }],
      {
        role: "assistant",
        content: "Call A.",
        tool_calls: [{ id: "call-expand-a", name: "resource.list", arguments: {} }],
      },
    );
    const second = agentTurn(
      "turn-expand-layout-b",
      3,
      "session-expand-layout",
      [
        { role: "tool", tool_call_id: "call-expand-a", content: "[]" },
        { role: "user", content: "Continue after enough space." },
      ],
      {
        role: "assistant",
        content: "Call B.",
        tool_calls: [{ id: "call-expand-b", name: "openapi.list_inputs", arguments: {} }],
      },
    );
    const toolA = makeEvent({
      event_id: "tool-expand-layout-a",
      order: 2,
      kind: "tool_call",
      name: "resource.list",
      parent_event_id: first.event_id,
      agent: first.agent,
      detail: { output: { tool_call_id: "call-expand-a" } },
    });
    const toolB = makeEvent({
      event_id: "tool-expand-layout-b",
      order: 4,
      kind: "tool_call",
      name: "openapi.list_inputs",
      parent_event_id: second.event_id,
      agent: second.agent,
      detail: { output: { tool_call_id: "call-expand-b" } },
    });
    const events = [first, toolA, second, toolB];
    const collapsedModel = buildCanvasModel(events, EMPTY_FILTERS, new Set());
    const expandedModel = buildCanvasModel(
      events,
      EMPTY_FILTERS,
      new Set(),
      new Set([eventDetailKey(toolA.event_id)]),
    );
    const collapsed = layoutCanvasModel(collapsedModel);
    const expanded = layoutCanvasModel(expandedModel);
    const restored = layoutCanvasModel(collapsedModel);
    const collapsedLater = collapsed.positions.get("event:tool-expand-layout-b")!;
    const expandedFirst = expanded.positions.get("event:tool-expand-layout-a")!;
    const expandedLater = expanded.positions.get("event:tool-expand-layout-b")!;

    expect(collapsedLater.column).toBe(1);
    expect(expandedLater.column).toBe(collapsedLater.column);
    expect(expandedLater.top).toBeGreaterThanOrEqual(
      expandedFirst.top + expandedFirst.height + CALL_GROUP_GAP,
    );
    expect(restored.positions.get("event:tool-expand-layout-b")?.top)
      .toBe(collapsedLater.top);
  });
});
