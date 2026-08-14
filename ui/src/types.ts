/** Wire contracts emitted by the App-owned current-run observer. */

export type EventKind = "agent_turn" | "tool_call";

export type EventStatus = "running" | "succeeded" | "warning" | "failed";

export interface AgentIdentity {
  session_id: string;
  parent_session_id?: string | null;
  name: string;
  lifecycle?: "subagent" | "system";
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

export interface GoalCriterion {
  criterion_id: string;
  description: string;
}

export interface GoalContract {
  mission: string;
  focus: string | null;
  success_criteria: GoalCriterion[];
}

export interface PlanRevisionRecord {
  plan_revision: number;
  reason: string;
  completed_milestone_ids: string[];
  superseded_milestone_ids: string[];
  created_milestone_ids: string[];
}

export interface MilestoneRecord {
  milestone_id: string;
  plan_revision: number;
  title: string;
  purpose: string;
  success_criteria: string[];
  status: "pending" | "completed" | "superseded";
  supersedes_milestone_id: string | null;
}

export interface TaskCriterion {
  criterion_id: string;
  description: string;
}

export type TaskStatus = "running" | "completed" | "partial" | "blocked" | "failed";

export interface TaskRecord {
  task_id: string;
  milestone_id: string;
  plan_revision: number;
  objective: string;
  purpose: string;
  success_criteria: TaskCriterion[];
  related_attempt_ids: string[];
  retry_reason: string | null;
  status: TaskStatus;
}

export interface CriterionVerdict {
  criterion_id: string;
  status: "met" | "not_met" | "unknown";
  explanation: string;
  evidence_refs: string[];
}

export interface AgentFinding {
  title: string;
  detail: string;
  confidence: "low" | "medium" | "high";
  evidence_refs: string[];
}

export interface TaskExecutionResult {
  task_id: string;
  outcome: "completed" | "partial" | "blocked";
  criteria: CriterionVerdict[];
  findings: AgentFinding[];
  unresolved_issues: string[];
  target_state_changes: string[];
}

export interface AttemptRecord {
  attempt_id: string;
  task_id: string;
  plan_revision: number;
  outcome: "completed" | "partial" | "blocked" | "failed";
  result: TaskExecutionResult | null;
  failure_code: string | null;
  failure_message: string | null;
}

export interface TaskLedgerSnapshot {
  plan_revision: number;
  run_status: "planning" | "running" | "completed";
  plan_revisions: PlanRevisionRecord[];
  milestones: MilestoneRecord[];
  tasks: TaskRecord[];
  attempts: AttemptRecord[];
}

export interface OrchestrationSessionRecord {
  session_id: string;
  profile_name: string;
  role: "orchestrator" | "task_executor";
  sequence: number;
  status: "completed" | "failed" | "cancelled" | "rollout_budget_exceeded"
    | "context_budget_exceeded" | "context_compaction_failed";
  decision_kind: "replan" | "dispatch_task" | "complete" | null;
  task_id: string | null;
  attempt_id: string | null;
}

export interface OrchestrationState {
  revision: number;
  goal: GoalContract;
  ledger: TaskLedgerSnapshot;
  sessions: OrchestrationSessionRecord[];
}

export interface ObserverSnapshot {
  schema_version: 4;
  run: RunState | null;
  events: TimelineEvent[];
  orchestration: OrchestrationState | null;
  latest_cursor: number;
}

export interface ObserverState {
  run: RunState | null;
  eventById: Record<string, TimelineEvent>;
  eventIds: string[];
  orchestration: OrchestrationState | null;
  latestCursor: number;
}

export type StreamEventType =
  | "run.reset"
  | "run.update"
  | "timeline.upsert"
  | "orchestration.replace";

export type StreamStatus = "connecting" | "live" | "reconnecting" | "closed";

export interface TimelineFilters {
  search: string;
  kinds: EventKind[];
  toolFamilies: string[];
  statuses: EventStatus[];
}
