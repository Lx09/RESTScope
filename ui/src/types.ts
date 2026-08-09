/** Wire contracts emitted by the App-owned current-run observer. */

export type EventKind = "agent_turn" | "tool_call" | "smoke_batch";

export type EventStatus = "running" | "succeeded" | "warning" | "failed";

export interface AgentIdentity {
  session_id: string;
  parent_session_id?: string | null;
  name: string;
  lifecycle?: "main" | "subagent";
  profile_name?: string;
  task_id?: string;
  path: string[];
}

export interface TimelineEvent {
  event_id: string;
  run_id: string | null;
  order: number;
  revision: number;
  kind: EventKind;
  name: string;
  status: EventStatus;
  started_at: string;
  ended_at: string | null;
  duration_ms: number | null;
  parent_event_id: string | null;
  agent: AgentIdentity | null;
  operation_key: string | null;
  round_number: number | null;
  summary: string;
  attributes: Record<string, unknown>;
  detail: Record<string, any>;
}

export interface RunState {
  run_id: string;
  status: string;
  started_at: string;
  ended_at: string | null;
  request: unknown;
  result: unknown;
}

export interface TodoItem {
  step: string;
  status: "pending" | "in_progress" | "completed";
}

export interface TodoState {
  revision: number;
  agent: AgentIdentity;
  explanation: string | null;
  items: TodoItem[];
  completed_count: number;
  total_count: number;
  active_step: string | null;
  percent: number;
}

export interface ObserverSnapshot {
  schema_version: 2;
  run: RunState | null;
  events: TimelineEvent[];
  todo: TodoState | null;
  latest_cursor: number;
}

export interface ObserverState {
  run: RunState | null;
  eventById: Record<string, TimelineEvent>;
  eventIds: string[];
  todo: TodoState | null;
  latestCursor: number;
}

export type StreamEventType =
  | "run.reset"
  | "run.update"
  | "timeline.upsert"
  | "todo.replace";

export type StreamStatus = "connecting" | "live" | "reconnecting" | "closed";

export interface TimelineFilters {
  search: string;
  kinds: EventKind[];
  toolFamilies: string[];
  statuses: EventStatus[];
}
