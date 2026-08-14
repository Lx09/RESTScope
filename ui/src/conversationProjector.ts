/** Convert schema-v4 Agent turns into a quiet prompt-and-response document flow.
 *
 * The backend remains authoritative for event order and revisions. This pure
 * frontend projector shows incremental LLM prompt messages and responses as
 * document text. Model messages that represent Tool calls or Tool results are
 * excluded from that text because the matching Tool event owns their complete
 * collapsed detail. Orchestration state selects root sessions by exact ID;
 * this module never treats a reusable Profile name as an Agent identity.
 */

import { eventMatches } from "./presentation";
import type {
  AgentIdentity,
  OrchestrationSessionRecord,
  OrchestrationState,
  TimelineEvent,
  TimelineFilters,
} from "./types";

export type ConversationItemKind =
  | "prompt"
  | "reasoning"
  | "commentary"
  | "final_answer"
  | "tool"
  | "subagent";

export interface ConversationItem {
  id: string;
  kind: ConversationItemKind;
  order: number;
  sessionId: string;
  event?: TimelineEvent;
  taskId?: string;
  objective?: string;
  message?: Record<string, unknown>;
  childSessionId?: string;
  childProfileName?: string;
  childStatus?: string;
  systemAgents?: SystemAgentActivity[];
}

export interface SystemAgentActivity {
  sessionId: string;
  profileName: string;
  status: string;
}

export interface RootSessionView extends Omit<OrchestrationSessionRecord, "status"> {
  status: OrchestrationSessionRecord["status"] | "running";
  order: number;
  startedAt: string | null;
}

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function taskDetail(event: TimelineEvent): { taskId?: string; objective?: string } {
  const task = record(event.detail.task);
  return {
    taskId: typeof task?.task_id === "string" ? task.task_id : event.agent?.task_id,
    objective: typeof task?.objective === "string" ? task.objective : undefined,
  };
}

function inputMessages(event: TimelineEvent): Record<string, unknown>[] {
  const input = record(event.detail.input);
  return Array.isArray(input?.messages)
    ? input.messages.filter(
      (message): message is Record<string, unknown> => record(message) !== null,
    )
    : [];
}

function isVisiblePromptMessage(message: Record<string, unknown>): boolean {
  const role = message.role;
  if (role === "tool") return false;
  if (role === "assistant" && Array.isArray(message.tool_calls) && message.tool_calls.length > 0) {
    return false;
  }
  return ["system", "developer", "user", "assistant"].includes(String(role))
    && message.content !== null
    && message.content !== undefined
    && String(message.content).trim().length > 0;
}

function outputContainsToolCall(event: TimelineEvent): boolean {
  const output = record(event.detail.output);
  return (
    output?.finish_reason === "tool_calls"
    || (Array.isArray(output?.tool_calls) && output.tool_calls.length > 0)
  );
}

function subagentFacts(event: TimelineEvent): Array<{
  sessionId: string;
  profileName?: string;
  status?: string;
}> {
  const detailInput = record(event.detail.input);
  const argumentsValue = record(detailInput?.arguments) ?? detailInput;
  const output = record(event.detail.output);
  const structured = record(output?.structured);
  const facts = new Map<string, { sessionId: string; profileName?: string; status?: string }>();

  const remember = (sessionId: unknown, profileName?: unknown, status?: unknown) => {
    if (typeof sessionId !== "string" || !sessionId) return;
    const current = facts.get(sessionId);
    facts.set(sessionId, {
      sessionId,
      profileName: typeof profileName === "string" ? profileName : current?.profileName,
      status: typeof status === "string" ? status : current?.status,
    });
  };

  remember(
    structured?.subagent_id ?? argumentsValue?.subagent_id,
    structured?.profile_name ?? argumentsValue?.profile_name,
    structured?.status,
  );
  if (Array.isArray(argumentsValue?.subagent_ids)) {
    argumentsValue.subagent_ids.forEach((sessionId) => remember(sessionId));
  }
  if (Array.isArray(structured?.agents)) {
    structured.agents.forEach((value) => {
      const agent = record(value);
      remember(agent?.subagent_id, agent?.profile_name, agent?.status);
    });
  }
  return [...facts.values()];
}

function searchableItem(item: ConversationItem): string {
  return JSON.stringify({
    kind: item.kind,
    objective: item.objective,
    message: item.message,
    childProfileName: item.childProfileName,
    event: item.event,
  }).toLocaleLowerCase();
}

function itemMatches(item: ConversationItem, filters?: TimelineFilters): boolean {
  if (!filters) return true;
  const search = filters.search.trim().toLocaleLowerCase();
  if (search && !searchableItem(item).includes(search)) return false;
  if (!item.event) return true;
  return eventMatches(item.event, filters);
}

function systemAgentsForTool(
  events: TimelineEvent[],
  toolEvent: TimelineEvent,
  sessions: Record<string, AgentIdentity>,
): SystemAgentActivity[] {
  // System roots remain independent sessions; only their first visible turn's
  // parent event supplies the causal HTTP Tool placement.
  const sessionIds = new Set(
    events
      .filter((event) => (
        event.parent_event_id === toolEvent.event_id
        && event.agent?.lifecycle === "system"
      ))
      .map((event) => event.agent?.session_id)
      .filter((value): value is string => typeof value === "string"),
  );
  return [...sessionIds].map((sessionId) => {
    const activity = events.filter((event) => event.agent?.session_id === sessionId);
    const status = activity.some((event) => event.status === "running")
      ? "running"
      : activity.some((event) => event.status === "failed")
        ? "failed"
        : activity.some((event) => event.status === "warning")
          ? "warning"
          : "succeeded";
    const identity = sessions[sessionId];
    return {
      sessionId,
      profileName: identity?.profile_name ?? identity?.name ?? sessionId,
      status,
    };
  });
}

/** Return every explicit generic Agent identity indexed by stable session ID. */
export function collectSessionAgents(events: TimelineEvent[]): Record<string, AgentIdentity> {
  const sessions: Record<string, AgentIdentity> = {};
  for (const event of events) {
    const agent = event.agent;
    if (agent?.session_id && agent.lifecycle) sessions[agent.session_id] = agent;
  }
  return sessions;
}

function rootStatus(events: TimelineEvent[], sessionId: string): RootSessionView["status"] {
  if (events.some((event) => (
    event.agent?.session_id === sessionId && event.status === "running"
  ))) return "running";
  return events.some((event) => (
    event.agent?.session_id === sessionId && event.status === "failed"
  )) ? "failed" : "completed";
}

/** Merge accepted Ledger links with live roots that have not returned yet. */
export function projectRootSessions(
  events: TimelineEvent[],
  orchestration: OrchestrationState | null,
): RootSessionView[] {
  if (!orchestration) return [];
  const firstEvent = new Map<string, TimelineEvent>();
  for (const event of [...events].sort((left, right) => left.order - right.order)) {
    const sessionId = event.agent?.session_id;
    if (sessionId && !firstEvent.has(sessionId)) firstEvent.set(sessionId, event);
  }
  const acceptedIds = new Set(orchestration.sessions.map((session) => session.session_id));
  const roots: RootSessionView[] = orchestration.sessions.map((session, index) => ({
    ...session,
    order: firstEvent.get(session.session_id)?.order
      ?? Number.MAX_SAFE_INTEGER - orchestration.sessions.length + index,
    startedAt: firstEvent.get(session.session_id)?.started_at ?? null,
  }));
  const nextSequence = {
    orchestrator: Math.max(0, ...roots.filter((item) => item.role === "orchestrator").map((item) => item.sequence)),
    task_executor: Math.max(0, ...roots.filter((item) => item.role === "task_executor").map((item) => item.sequence)),
  };

  for (const [sessionId, event] of firstEvent) {
    const profileName = event.agent?.profile_name ?? event.agent?.name;
    const role = profileName === "orchestrator"
      ? "orchestrator"
      : profileName === "task-executor"
        ? "task_executor"
        : null;
    if (!role || acceptedIds.has(sessionId) || event.agent?.lifecycle !== "system") continue;
    nextSequence[role] += 1;
    roots.push({
      session_id: sessionId,
      profile_name: profileName ?? role,
      role,
      sequence: nextSequence[role],
      status: rootStatus(events, sessionId),
      decision_kind: null,
      task_id: null,
      attempt_id: null,
      order: event.order,
      startedAt: event.started_at,
    });
  }
  return roots.sort((left, right) => left.order - right.order || left.sequence - right.sequence);
}

/** Resolve one Task to its accepted or currently running Executor session. */
export function taskExecutorSessionId(
  taskId: string,
  taskStatus: string,
  sessions: RootSessionView[],
): string | null {
  const accepted = sessions.find((session) => (
    session.role === "task_executor" && session.task_id === taskId
  ));
  if (accepted) return accepted.session_id;
  if (taskStatus !== "running") return null;
  return [...sessions].reverse().find((session) => (
    session.role === "task_executor" && session.task_id === null
  ))?.session_id ?? null;
}

/** Project one exact root or Subagent session in original event-start order. */
export function projectConversation(
  events: TimelineEvent[],
  sessionId: string,
  filters?: TimelineFilters,
): ConversationItem[] {
  const ordered = [...events].sort((left, right) => (
    left.order - right.order || left.event_id.localeCompare(right.event_id)
  ));
  const seenTasks = new Set<string>();
  const sessions = collectSessionAgents(ordered);
  const activityByChild = new Map<string, ConversationItem>();
  const items: ConversationItem[] = [];

  for (const event of ordered) {
    if (event.agent?.session_id !== sessionId) continue;

    if (event.kind === "agent_turn") {
      const task = taskDetail(event);
      const taskKey = task.taskId ?? `legacy:${event.event_id}`;
      const messages = inputMessages(event).filter(isVisiblePromptMessage);
      messages.forEach((message, index) => {
        items.push({
          id: `prompt:${event.event_id}:${index}`,
          kind: "prompt",
          order: event.order - 0.4 + index / Math.max(messages.length, 100),
          sessionId,
          event,
          taskId: task.taskId,
          message,
        });
      });
      const hasUserPrompt = messages.some((message) => message.role === "user");
      if (!seenTasks.has(taskKey) && task.objective && !hasUserPrompt) {
        items.push({
          id: `task:${taskKey}`,
          kind: "prompt",
          order: event.order - 0.4 + messages.length / Math.max(messages.length + 1, 100),
          sessionId,
          event,
          taskId: task.taskId,
          objective: task.objective,
          message: { role: "user", content: task.objective },
        });
      }
      seenTasks.add(taskKey);
      if (
        (typeof event.detail.reasoning === "string" && event.detail.reasoning.trim())
        || event.status === "running"
      ) {
        items.push({
          id: `reasoning:${event.event_id}`,
          kind: "reasoning",
          order: event.order - 0.2,
          sessionId,
          event,
        });
      }
      if (
        event.detail.output !== null
        && event.detail.output !== undefined
        && !outputContainsToolCall(event)
      ) {
        items.push({
          id: `${event.detail.phase === "final_answer" ? "final" : "commentary"}:${event.event_id}`,
          kind: event.detail.phase === "final_answer" ? "final_answer" : "commentary",
          order: event.order,
          sessionId,
          event,
        });
      }
      continue;
    }

    if (event.kind === "tool_call" && event.name.startsWith("subagent.")) {
      for (const fact of subagentFacts(event)) {
        const child = sessions[fact.sessionId];
        const current = activityByChild.get(fact.sessionId);
        if (current) {
          current.event = event;
          current.childProfileName = child?.profile_name
            ?? child?.name
            ?? fact.profileName
            ?? current.childProfileName;
          current.childStatus = fact.status ?? event.status;
          continue;
        }
        const activity: ConversationItem = {
          id: `subagent:${fact.sessionId}`,
          kind: "subagent",
          order: event.order,
          sessionId,
          event,
          childSessionId: fact.sessionId,
          childProfileName: child?.profile_name ?? child?.name ?? fact.profileName ?? fact.sessionId,
          childStatus: fact.status ?? event.status,
        };
        activityByChild.set(fact.sessionId, activity);
        items.push(activity);
      }
      continue;
    }

    if (event.kind === "tool_call") {
      items.push({
        id: `tool:${event.event_id}`,
        kind: "tool",
        order: event.order,
        sessionId,
        event,
        systemAgents: systemAgentsForTool(ordered, event, sessions),
      });
    }
  }

  // The child task can become visible before its start Tool result arrives in
  // an SSE replacement. Its explicit parent relationship still provides one
  // stable Drawer entry without exposing the lifecycle protocol name.
  for (const child of Object.values(sessions)) {
    if (child.parent_session_id !== sessionId || activityByChild.has(child.session_id)) continue;
    const firstChildEvent = ordered.find((event) => event.agent?.session_id === child.session_id);
    if (!firstChildEvent) continue;
    items.push({
      id: `subagent:${child.session_id}`,
      kind: "subagent",
      order: firstChildEvent.order,
      sessionId,
      childSessionId: child.session_id,
      childProfileName: child.profile_name ?? child.name,
      childStatus: firstChildEvent.status,
    });
  }

  return items
    .sort((left, right) => left.order - right.order || left.id.localeCompare(right.id))
    .filter((item) => itemMatches(item, filters));
}
