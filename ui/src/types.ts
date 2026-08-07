/** Wire contracts emitted by the App-owned current-run observer. */

export type EventKind = "agent_turn" | "tool_call" | "smoke_batch";

export type EventStatus = "running" | "succeeded" | "warning" | "failed";

export interface AgentIdentity {
  session_id: string;
  parent_session_id?: string | null;
  name: string;
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

export interface WorklistItem {
  item_id: string;
  source_failure_refs: string[];
  test_case_refs: string[];
  suspected_parameters: string[];
  progress?: string | null;
  root_cause?: string | null;
  candidate_refs?: string[];
  decision?: unknown;
  [key: string]: unknown;
}

export interface WorklistSnapshot {
  revision: number;
  active_item_id: string | null;
  items: WorklistItem[];
}

export interface WorklistState {
  operation_key: string | null;
  snapshot: WorklistSnapshot;
  failure_messages: Record<string, string>;
  decided_count: number;
  total_count: number;
  percent: number;
}

export interface ObserverSnapshot {
  schema_version: 2;
  run: RunState | null;
  events: TimelineEvent[];
  worklist: WorklistState | null;
  latest_cursor: number;
}

export interface ObserverState {
  run: RunState | null;
  eventById: Record<string, TimelineEvent>;
  eventIds: string[];
  worklist: WorklistState | null;
  latestCursor: number;
}

export type StreamEventType =
  | "run.reset"
  | "run.update"
  | "timeline.upsert"
  | "worklist.replace";

export type StreamStatus = "connecting" | "live" | "reconnecting" | "closed";

export interface TimelineFilters {
  search: string;
  agents: string[];
  kinds: EventKind[];
  toolFamilies: string[];
  statuses: EventStatus[];
}
