import { describe, expect, it } from "vitest";

import {
  projectConversation,
  projectRootSessions,
  taskExecutorSessionId,
} from "../conversationProjector";
import { EMPTY_FILTERS } from "../presentation";
import type { OrchestrationState } from "../types";
import { makeEvent } from "./fixtures";

const orchestratorAgent = {
  session_id: "orchestrator-1",
  parent_session_id: null,
  name: "orchestrator",
  profile_name: "orchestrator",
  lifecycle: "system" as const,
  path: ["orchestrator"],
};

const orchestration = {
  revision: 4,
  goal: { mission: "Explore", focus: null, success_criteria: [] },
  ledger: {
    plan_revision: 1,
    run_status: "running",
    plan_revisions: [],
    milestones: [],
    tasks: [],
    attempts: [],
  },
  sessions: [{
    session_id: "orchestrator-1",
    profile_name: "orchestrator",
    role: "orchestrator",
    sequence: 1,
    status: "completed",
    decision_kind: "replan",
    task_id: null,
    attempt_id: null,
  }],
} satisfies OrchestrationState;

describe("conversation projector", () => {
  it("keeps same-profile root sessions separate and preserves stable numbering", () => {
    const first = makeEvent({ event_id: "first", agent: orchestratorAgent });
    const second = makeEvent({
      event_id: "second",
      order: 2,
      agent: { ...orchestratorAgent, session_id: "orchestrator-2" },
    });
    const state: OrchestrationState = {
      ...orchestration,
      sessions: [
        ...orchestration.sessions,
        { ...orchestration.sessions[0], session_id: "orchestrator-2", sequence: 2 },
      ],
    };

    expect(projectRootSessions([first, second], state).map((item) => (
      [item.session_id, item.sequence]
    ))).toEqual([["orchestrator-1", 1], ["orchestrator-2", 2]]);
  });

  it("places system and user prompts, reasoning, and response in stable order", () => {
    const turn = makeEvent({
      event_id: "turn-1",
      agent: orchestratorAgent,
      detail: {
        task: { task_id: "task-1", objective: "Inspect the API" },
        input: { messages: [
          { role: "system", content: "Be careful" },
          { role: "user", content: "Inspect the API" },
        ] },
        reasoning: "Check the schema first.",
        phase: "final_answer",
        output: { content: "Complete" },
      },
    });

    const projected = projectConversation([turn], "orchestrator-1", EMPTY_FILTERS);

    expect(projected.map((item) => [item.id, item.kind])).toEqual([
      ["prompt:turn-1:0", "prompt"],
      ["prompt:turn-1:1", "prompt"],
      ["reasoning:turn-1", "reasoning"],
      ["final:turn-1", "final_answer"],
    ]);
  });

  it("omits empty completed reasoning but shows a running reasoning status", () => {
    const completed = makeEvent({
      event_id: "completed",
      agent: orchestratorAgent,
      detail: { input: { messages: [] }, output: { content: "done" }, phase: "commentary" },
    });
    const running = makeEvent({
      event_id: "running",
      order: 2,
      status: "running",
      agent: orchestratorAgent,
      detail: { input: { messages: [] }, output: null, phase: "commentary" },
    });

    expect(projectConversation([completed], "orchestrator-1").map((item) => item.kind)).toEqual([
      "commentary",
    ]);
    expect(projectConversation([running], "orchestrator-1").map((item) => item.id)).toEqual([
      "reasoning:running",
    ]);
  });

  it("keeps Tool and Subagent calls as compact conversation items", () => {
    const start = makeEvent({
      event_id: "start",
      kind: "tool_call",
      name: "subagent.start",
      agent: orchestratorAgent,
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
      agent: orchestratorAgent,
    });
    const items = projectConversation([start, worklist], "orchestrator-1");

    expect(items.map((item) => [item.id, item.kind])).toEqual([
      ["subagent:child-1", "subagent"],
      ["tool:worklist", "tool"],
    ]);
    expect(items[0]).toMatchObject({
      childSessionId: "child-1",
      childProfileName: "researcher",
    });
  });

  it("nests independent System Agent sessions under the causal HTTP Tool", () => {
    const http = makeEvent({
      event_id: "http-1",
      kind: "tool_call",
      name: "restscope.http.request",
      agent: orchestratorAgent,
    });
    const systemAgent = {
      session_id: "system-1",
      parent_session_id: null,
      name: "resource-identifier-selector",
      profile_name: "resource-identifier-selector",
      lifecycle: "system" as const,
      path: ["resource-identifier-selector"],
    };
    const systemTurn = makeEvent({
      event_id: "system-turn",
      order: 2,
      parent_event_id: "http-1",
      agent: systemAgent,
    });

    const projection = projectConversation([http, systemTurn], "orchestrator-1");

    expect(projection[0].systemAgents).toEqual([{
      sessionId: "system-1",
      profileName: "resource-identifier-selector",
      status: "succeeded",
    }]);
  });

  it("does not repeat Tool Call or Tool Result messages as body text", () => {
    const turn = makeEvent({
      event_id: "tool-turn",
      agent: orchestratorAgent,
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

    const items = projectConversation([turn], "orchestrator-1");

    expect(items.map((item) => item.kind)).toEqual(["prompt"]);
    expect(items[0].message?.content).toBe("Continue");
  });

  it("routes a Task to its exact accepted Executor session", () => {
    const sessions = projectRootSessions([], {
      ...orchestration,
      sessions: [{
        session_id: "executor-2",
        profile_name: "task-executor",
        role: "task_executor",
        sequence: 2,
        status: "completed",
        decision_kind: null,
        task_id: "task_7",
        attempt_id: "attempt_4",
      }],
    });

    expect(taskExecutorSessionId("task_7", "completed", sessions)).toBe("executor-2");
    expect(taskExecutorSessionId("task_8", "completed", sessions)).toBeNull();
  });
});
