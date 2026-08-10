import { describe, expect, it } from "vitest";

import {
  projectConversation,
  projectMainConversation,
  selectMainAgent,
} from "../conversationProjector";
import { EMPTY_FILTERS } from "../presentation";
import { makeEvent } from "./fixtures";

const mainAgent = {
  session_id: "main-1",
  parent_session_id: null,
  name: "main_profile",
  profile_name: "main_profile",
  lifecycle: "main" as const,
  task_id: "task-1",
  path: ["main_profile"],
};

describe("conversation projector", () => {
  it("refuses to reinterpret a legacy Agent as the Main Agent", () => {
    expect(selectMainAgent([makeEvent()])).toBeNull();
    expect(projectMainConversation([makeEvent()]).items).toEqual([]);
  });

  it("places system and user prompts, reasoning, and response in stable order", () => {
    const turn = makeEvent({
      event_id: "turn-1",
      agent: mainAgent,
      detail: {
        task: { task_id: "task-1", objective: "Inspect the API" },
        input: { messages: [
          { role: "system", content: "Be careful" },
          { role: "user", content: "Inspect the API" },
        ] },
        reasoning: "Check the schema first.",
        phase: "final_answer",
        output: { content: '{"summary":"Complete"}' },
      },
    });

    const projected = projectMainConversation([turn], EMPTY_FILTERS);

    expect(projected.mainAgent).toEqual(mainAgent);
    expect(projected.items.map((item) => [item.id, item.kind])).toEqual([
      ["prompt:turn-1:0", "prompt"],
      ["prompt:turn-1:1", "prompt"],
      ["reasoning:turn-1", "reasoning"],
      ["final:turn-1", "final_answer"],
    ]);
  });

  it("omits empty completed reasoning but shows a running reasoning status", () => {
    const completed = makeEvent({
      event_id: "completed",
      agent: mainAgent,
      detail: { input: { messages: [] }, output: { content: "done" }, phase: "commentary" },
    });
    const running = makeEvent({
      event_id: "running",
      order: 2,
      status: "running",
      agent: mainAgent,
      detail: { input: { messages: [] }, output: null, phase: "commentary" },
    });

    expect(projectConversation([completed], "main-1").map((item) => item.kind)).toEqual([
      "commentary",
    ]);
    expect(projectConversation([running], "main-1").map((item) => item.id)).toEqual([
      "reasoning:running",
    ]);
  });

  it("keeps Tool and Subagent calls as collapsed conversation items", () => {
    const start = makeEvent({
      event_id: "start",
      kind: "tool_call",
      name: "subagent.start",
      agent: mainAgent,
      detail: {
        input: { arguments: { profile_name: "researcher" } },
        output: { structured: { subagent_id: "child-1", profile_name: "researcher" } },
      },
    });
    const worklist = makeEvent({
      event_id: "worklist",
      order: 2,
      kind: "tool_call",
      name: "diagnosis.record",
      agent: mainAgent,
    });
    const items = projectConversation([start, worklist], "main-1");

    expect(items.map((item) => [item.id, item.kind])).toEqual([
      ["subagent:child-1", "subagent"],
      ["tool:worklist", "tool"],
    ]);
    expect(items[0]).toMatchObject({
      childSessionId: "child-1",
      childProfileName: "researcher",
    });
  });

  it("does not repeat Tool Call or Tool Result messages as body text", () => {
    const turn = makeEvent({
      event_id: "tool-turn",
      agent: mainAgent,
      detail: {
        input: { messages: [
          { role: "assistant", content: "Calling a tool", tool_calls: [{ id: "call-1" }] },
          { role: "tool", tool_call_id: "call-1", content: "Tool result" },
          { role: "user", content: "Continue" },
        ] },
        output: {
          content: "Calling another tool",
          finish_reason: "tool_calls",
          tool_calls: [{ id: "call-2" }],
        },
      },
    });

    const items = projectConversation([turn], "main-1");

    expect(items.map((item) => item.kind)).toEqual(["prompt"]);
    expect(items[0].message?.content).toBe("Continue");
  });
});
