/** Show any non-Orchestrator session in one reusable right-side Drawer. */

import { Breadcrumb, Drawer, Empty, Typography } from "antd";

import {
  collectSessionAgents,
  projectConversation,
  type RootSessionView,
} from "../conversationProjector";
import type {
  AgentIdentity,
  AttemptRecord,
  OrchestrationState,
  TaskRecord,
  TimelineEvent,
  TimelineFilters,
} from "../types";
import { ConversationView } from "./ConversationView";

const { Text, Title } = Typography;
const CRITERION_LABELS = { met: "满足", not_met: "未满足", unknown: "未知" } as const;

function sessionLabel(
  sessionId: string,
  rootSessions: RootSessionView[],
  agents: Record<string, AgentIdentity>,
  events: TimelineEvent[],
): string {
  const root = rootSessions.find((session) => session.session_id === sessionId);
  if (root) {
    const role = root.role === "task_executor" ? "Task Executor" : "Orchestrator";
    return `${role} #${root.sequence} · ${sessionId.slice(0, 6)}`;
  }
  const agent = agents[sessionId];
  const sameLifecycle = Object.values(agents)
    .filter((item) => item.lifecycle === agent?.lifecycle)
    .sort((left, right) => {
      const leftOrder = events.find((event) => event.agent?.session_id === left.session_id)?.order ?? 0;
      const rightOrder = events.find((event) => event.agent?.session_id === right.session_id)?.order ?? 0;
      return leftOrder - rightOrder;
    });
  const sequence = Math.max(1, sameLifecycle.findIndex((item) => item.session_id === sessionId) + 1);
  const role = agent?.lifecycle === "subagent" ? "Subagent" : "System Agent";
  return `${role} #${sequence} · ${sessionId.slice(0, 6)}`;
}

function ExecutorSummary({
  orchestration,
  session,
}: {
  orchestration: OrchestrationState;
  session: RootSessionView;
}) {
  const task: TaskRecord | undefined = orchestration.ledger.tasks.find(
    (item) => item.task_id === session.task_id,
  );
  const attempt: AttemptRecord | undefined = orchestration.ledger.attempts.find(
    (item) => item.attempt_id === session.attempt_id,
  );
  const milestone = orchestration.ledger.milestones.find(
    (item) => item.milestone_id === task?.milestone_id,
  );
  if (!task) return null;

  return (
    <section aria-label="Task Executor 结果" className="executor-summary">
      <Text className="decision-label">{milestone?.title ?? task.milestone_id}</Text>
      <Title level={5}>{task.objective}</Title>
      <Text type="secondary">Attempt：{attempt?.attempt_id ?? "运行中"}</Text>
      {attempt?.result?.criteria.map((criterion) => (
        <Text key={criterion.criterion_id}>
          条件 {criterion.criterion_id} · {CRITERION_LABELS[criterion.status]}：
          {criterion.explanation}
        </Text>
      ))}
      {attempt?.result?.findings.map((finding, index) => (
        <Text key={`${finding.title}:${index}`}>发现：{finding.title} — {finding.detail}</Text>
      ))}
      {attempt?.result?.unresolved_issues.map((issue, index) => (
        <Text key={`${issue}:${index}`}>未解决：{issue}</Text>
      ))}
      {attempt?.failure_message && <Text className="status-failed">失败：{attempt.failure_message}</Text>}
    </section>
  );
}

/** Keep one Drawer mounted while breadcrumb navigation changes its exact session. */
export function AgentDrawer({
  openPath,
  orchestration,
  events,
  filters,
  rootSessions,
  onClose,
  onNavigate,
  onOpenAgent,
}: {
  openPath: string[];
  orchestration: OrchestrationState | null;
  events: TimelineEvent[];
  filters: TimelineFilters;
  rootSessions: RootSessionView[];
  onClose: () => void;
  onNavigate: (depth: number) => void;
  onOpenAgent: (sessionId: string) => void;
}) {
  const sessionId = openPath.at(-1) ?? null;
  const agents = collectSessionAgents(events);
  const root = rootSessions.find((session) => session.session_id === sessionId);
  const exists = sessionId !== null && (root !== undefined || agents[sessionId] !== undefined);
  const items = exists ? projectConversation(events, sessionId, filters) : [];

  return (
    <Drawer
      className="agent-drawer"
      focusable={{ trap: true, focusTriggerAfterClose: true }}
      onClose={onClose}
      open={exists}
      placement="right"
      size={720}
      title={(
        <Breadcrumb
          items={openPath.map((pathSessionId, index) => ({
            title: index === openPath.length - 1
              ? sessionLabel(pathSessionId, rootSessions, agents, events)
              : (
                <button
                  className="breadcrumb-button"
                  onClick={() => onNavigate(index)}
                  type="button"
                >
                  {sessionLabel(pathSessionId, rootSessions, agents, events)}
                </button>
              ),
          }))}
        />
      )}
    >
      {!exists ? (
        <Empty description="此 Agent session 不在当前快照中" />
      ) : (
        <>
          {orchestration && root?.role === "task_executor" && (
            <ExecutorSummary orchestration={orchestration} session={root} />
          )}
          <ConversationView
            emptyDescription="此 Agent 尚无会话内容"
            items={items}
            onOpenSubagent={onOpenAgent}
            onOpenSystemAgent={onOpenAgent}
          />
        </>
      )}
    </Drawer>
  );
}
