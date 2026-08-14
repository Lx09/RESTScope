import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { AgentDrawer } from "../components/AgentDrawer";
import { OrchestrationRail } from "../components/OrchestrationRail";
import { OrchestratorWorkspace } from "../components/OrchestratorWorkspace";
import { projectRootSessions } from "../conversationProjector";
import { EMPTY_FILTERS } from "../presentation";
import type { OrchestrationState } from "../types";
import { makeEvent } from "./fixtures";

const orchestration: OrchestrationState = {
  revision: 8,
  goal: {
    mission: "Explore the target API",
    focus: "Projects",
    success_criteria: [{ criterion_id: "goal_1", description: "Happy path works" }],
  },
  ledger: {
    plan_revision: 2,
    run_status: "running",
    plan_revisions: [],
    milestones: [{
      milestone_id: "milestone_1",
      plan_revision: 2,
      title: "Understand projects",
      purpose: "Find project behavior",
      success_criteria: ["Can list projects"],
      status: "pending",
      supersedes_milestone_id: null,
    }],
    tasks: [{
      task_id: "task_1",
      milestone_id: "milestone_1",
      plan_revision: 2,
      objective: "List projects",
      purpose: "Collect evidence",
      success_criteria: [{ criterion_id: "criterion_1", description: "GET succeeds" }],
      related_attempt_ids: [],
      retry_reason: null,
      status: "completed",
    }],
    attempts: [{
      attempt_id: "attempt_1",
      task_id: "task_1",
      plan_revision: 2,
      outcome: "completed",
      result: {
        task_id: "task_1",
        outcome: "completed",
        criteria: [{
          criterion_id: "criterion_1",
          status: "met",
          explanation: "Received HTTP 200",
          evidence_refs: ["observation_1"],
        }],
        findings: [],
        unresolved_issues: [],
        target_state_changes: [],
      },
      failure_code: null,
      failure_message: null,
    }],
  },
  sessions: [
    {
      session_id: "orch-a1b2c3",
      profile_name: "orchestrator",
      role: "orchestrator",
      sequence: 1,
      status: "completed",
      decision_kind: "replan",
      task_id: null,
      attempt_id: null,
    },
    {
      session_id: "executor-d4e5f6",
      profile_name: "task-executor",
      role: "task_executor",
      sequence: 1,
      status: "completed",
      decision_kind: null,
      task_id: "task_1",
      attempt_id: "attempt_1",
    },
    {
      session_id: "orch-g7h8i9",
      profile_name: "orchestrator",
      role: "orchestrator",
      sequence: 2,
      status: "completed",
      decision_kind: "dispatch_task",
      task_id: "task_1",
      attempt_id: null,
    },
  ],
};

describe("orchestration workspace", () => {
  it("renders the text hierarchy and opens the exact Task Executor session", async () => {
    const openAgent = vi.fn();
    const sessions = projectRootSessions([], orchestration);

    render(
      <OrchestrationRail
        onOpenAgent={openAgent}
        orchestration={orchestration}
        sessions={sessions}
      />,
    );

    expect(screen.getByText("Explore the target API")).toBeVisible();
    expect(screen.getByText("Understand projects")).toBeVisible();
    expect(screen.getByText("Attempt 1 · 已完成")).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: /List projects/ }));
    expect(openAgent).toHaveBeenCalledWith("executor-d4e5f6");
  });

  it("segments same-name Orchestrators by session without mixing responses", () => {
    const first = makeEvent({
      event_id: "first",
      agent: {
        session_id: "orch-a1b2c3",
        parent_session_id: null,
        name: "orchestrator",
        profile_name: "orchestrator",
        lifecycle: "system",
        path: ["orchestrator"],
      },
      detail: { input: { messages: [] }, output: { content: "First response" } },
    });
    const second = makeEvent({
      event_id: "second",
      order: 2,
      agent: {
        ...first.agent!,
        session_id: "orch-g7h8i9",
      },
      detail: { input: { messages: [] }, output: { content: "Second response" } },
    });
    const sessions = projectRootSessions([first, second], orchestration);

    render(
      <OrchestratorWorkspace
        events={[first, second]}
        filters={EMPTY_FILTERS}
        onOpenAgent={vi.fn()}
        orchestration={orchestration}
        sessions={sessions}
      />,
    );

    expect(screen.getByText("Orchestrator #1 · orch-a")).toBeVisible();
    expect(screen.getByText("Orchestrator #2 · orch-g")).toBeVisible();
    expect(screen.getByText("First response")).toBeVisible();
    expect(screen.getByText("Second response")).toBeVisible();
  });

  it("uses one Drawer and a breadcrumb path for Executor and Subagent sessions", async () => {
    const navigate = vi.fn();
    const executorEvent = makeEvent({
      event_id: "executor",
      agent: {
        session_id: "executor-d4e5f6",
        parent_session_id: null,
        name: "task-executor",
        profile_name: "task-executor",
        lifecycle: "system",
        path: ["task-executor"],
      },
    });
    const childEvent = makeEvent({
      event_id: "child",
      order: 2,
      agent: {
        session_id: "child-z9y8x7",
        parent_session_id: "executor-d4e5f6",
        name: "parameter-patch",
        profile_name: "parameter-patch",
        lifecycle: "subagent",
        path: ["task-executor", "parameter-patch"],
      },
    });

    render(
      <AgentDrawer
        events={[executorEvent, childEvent]}
        filters={EMPTY_FILTERS}
        onClose={vi.fn()}
        onNavigate={navigate}
        onOpenAgent={vi.fn()}
        openPath={["executor-d4e5f6", "child-z9y8x7"]}
        orchestration={orchestration}
        rootSessions={projectRootSessions([executorEvent, childEvent], orchestration)}
      />,
    );

    expect(screen.getByText("Subagent #1 · child-")).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: /Task Executor #1/ }));
    expect(navigate).toHaveBeenCalledWith(0);
  });
});
