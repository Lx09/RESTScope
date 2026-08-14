/** Render the Goal and Ledger as a compact Milestone → Task → Attempt outline. */

import { Empty, Typography } from "antd";

import { taskExecutorSessionId, type RootSessionView } from "../conversationProjector";
import type { OrchestrationState } from "../types";

const { Paragraph, Text, Title } = Typography;

const RUN_LABELS = { planning: "规划中", running: "运行中", completed: "已完成" } as const;
const MILESTONE_LABELS = { pending: "待处理", completed: "已完成", superseded: "已取代" } as const;
const TASK_LABELS = {
  running: "进行中",
  completed: "已完成",
  partial: "部分完成",
  blocked: "受阻",
  failed: "失败",
} as const;
const ATTEMPT_LABELS = {
  completed: "已完成",
  partial: "部分完成",
  blocked: "受阻",
  failed: "失败",
} as const;

function numberedId(value: string): string {
  const [kind, number] = value.split("_");
  return `${kind === "attempt" ? "Attempt" : kind} ${number ?? value}`;
}

/** Show orchestration hierarchy while routing clicks only by complete session ID. */
export function OrchestrationRail({
  orchestration,
  sessions,
  onOpenAgent,
}: {
  orchestration: OrchestrationState | null;
  sessions: RootSessionView[];
  onOpenAgent: (sessionId: string) => void;
}) {
  if (!orchestration) {
    return <Empty className="orchestration-empty" description="等待编排状态" />;
  }
  const { goal, ledger } = orchestration;

  return (
    <aside aria-label="编排进度" className="orchestration-rail">
      <header className="orchestration-goal">
        <Text className="rail-kicker">Goal</Text>
        <Title level={5}>{goal.mission}</Title>
        {goal.focus && <Paragraph type="secondary">重点：{goal.focus}</Paragraph>}
        <div className="rail-run-meta">
          <span className={`ledger-status status-${ledger.run_status}`}>
            {RUN_LABELS[ledger.run_status]}
          </span>
          <span>Plan Revision {ledger.plan_revision}</span>
        </div>
      </header>

      <div className="milestone-tree">
        {ledger.milestones.map((milestone) => {
          const tasks = ledger.tasks.filter((task) => task.milestone_id === milestone.milestone_id);
          return (
            <section className="milestone-node" key={milestone.milestone_id}>
              <div className="milestone-heading">
                <Text strong>{milestone.title}</Text>
                <Text className={`ledger-status status-${milestone.status}`}>
                  {MILESTONE_LABELS[milestone.status]}
                </Text>
              </div>
              <Text className="rail-purpose" type="secondary">{milestone.purpose}</Text>
              <div className="task-tree">
                {tasks.map((task) => {
                  const sessionId = taskExecutorSessionId(task.task_id, task.status, sessions);
                  const attempts = ledger.attempts.filter((attempt) => attempt.task_id === task.task_id);
                  return (
                    <div className="task-node" key={task.task_id}>
                      <button
                        aria-label={`${task.objective}，${TASK_LABELS[task.status]}`}
                        className="task-link"
                        disabled={!sessionId}
                        onClick={() => sessionId && onOpenAgent(sessionId)}
                        type="button"
                      >
                        <span>{task.objective}</span>
                        <span className={`ledger-status status-${task.status}`}>
                          {TASK_LABELS[task.status]}
                        </span>
                      </button>
                      {attempts.map((attempt) => (
                        <button
                          className="attempt-link"
                          disabled={!sessionId}
                          key={attempt.attempt_id}
                          onClick={() => sessionId && onOpenAgent(sessionId)}
                          type="button"
                        >
                          {numberedId(attempt.attempt_id)} · {ATTEMPT_LABELS[attempt.outcome]}
                        </button>
                      ))}
                    </div>
                  );
                })}
              </div>
            </section>
          );
        })}
      </div>
    </aside>
  );
}
