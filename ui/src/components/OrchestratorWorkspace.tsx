/** Render accepted Orchestrator roots as one result-first segmented conversation. */

import { DownOutlined } from "@ant-design/icons";
import { Button, Empty, Typography } from "antd";
import { useEffect, useRef, useState } from "react";

import {
  projectConversation,
  type RootSessionView,
} from "../conversationProjector";
import type { OrchestrationState, TimelineEvent, TimelineFilters } from "../types";
import { ConversationView } from "./ConversationView";
import { MarkdownValue } from "./EventCard";

const { Text } = Typography;

const STATUS_LABELS: Record<RootSessionView["status"], string> = {
  running: "运行中",
  completed: "已完成",
  failed: "失败",
  cancelled: "已取消",
  rollout_budget_exceeded: "输出预算不足",
  context_budget_exceeded: "上下文预算不足",
  context_compaction_failed: "上下文压缩失败",
};
const DECISION_LABELS = {
  replan: "重新规划",
  dispatch_task: "派发任务",
  complete: "完成判定",
} as const;

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function list(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function decisionOutput(events: TimelineEvent[], sessionId: string): Record<string, unknown> | null {
  const finalEvent = [...events].reverse().find((event) => (
    event.agent?.session_id === sessionId && record(event.detail.task_result)?.status === "completed"
  ));
  return record(record(finalEvent?.detail.task_result)?.output);
}

function DecisionSummary({ output }: { output: Record<string, unknown> | null }) {
  if (!output || typeof output.kind !== "string") return null;
  if (output.kind === "replan") {
    return (
      <div className="decision-summary">
        <Text className="decision-label">规划原因</Text>
        <MarkdownValue value={output.reason} />
        {list(output.milestones).map((value, index) => {
          const milestone = record(value);
          if (!milestone) return null;
          const supersedes = milestone.supersedes_milestone_id;
          return (
            <Text key={index}>
              {typeof supersedes === "string" ? `取代 ${supersedes}` : "新增里程碑"}：
              {String(milestone.title ?? "未命名")}
            </Text>
          );
        })}
        {list(output.completed_milestone_ids).map((value, index) => (
          <Text key={`completed:${index}`}>完成里程碑：{String(value)}</Text>
        ))}
      </div>
    );
  }
  if (output.kind === "dispatch_task") {
    const task = record(output.task);
    return (
      <div className="decision-summary">
        <Text className="decision-label">派发任务</Text>
        <MarkdownValue value={task?.objective} />
        {task?.purpose !== undefined && <Text type="secondary">目的：{String(task.purpose)}</Text>}
        {list(task?.success_criteria).map((value, index) => {
          const criterion = record(value);
          return criterion && <Text key={index}>验收：{String(criterion.description ?? "—")}</Text>;
        })}
      </div>
    );
  }
  return (
    <div className="decision-summary">
      <Text className="decision-label">完成总结</Text>
      <MarkdownValue value={output.summary} />
      {list(output.goal_criteria).map((value, index) => {
        const criterion = record(value);
        return criterion && (
          <Text key={index}>
            {String(criterion.criterion_id ?? "Goal")} · {String(criterion.status ?? "unknown")}：
            {String(criterion.explanation ?? "—")}
          </Text>
        );
      })}
      {list(output.unresolved).map((value, index) => (
        <Text key={index}>未解决：{String(value)}</Text>
      ))}
    </div>
  );
}

/** Preserve the existing conversation surface inside each exact root segment. */
export function OrchestratorWorkspace({
  orchestration,
  events,
  filters,
  sessions,
  onOpenAgent,
}: {
  orchestration: OrchestrationState | null;
  events: TimelineEvent[];
  filters: TimelineFilters;
  sessions: RootSessionView[];
  onOpenAgent: (sessionId: string) => void;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const [autoFollow, setAutoFollow] = useState(true);
  const orchestrators = sessions.filter((session) => session.role === "orchestrator");
  useEffect(() => {
    const element = scrollRef.current;
    if (autoFollow && element) element.scrollTop = element.scrollHeight;
  }, [autoFollow, events.length, orchestration?.revision]);
  if (!orchestration || orchestrators.length === 0) {
    return <Empty className="orchestration-empty" description="等待 Orchestrator 会话" />;
  }

  return (
    <div className="orchestrator-timeline-wrap">
      <div
        aria-label="Orchestrator 会话"
        className="orchestrator-timeline"
        onScroll={() => {
          const element = scrollRef.current;
          if (element) {
            setAutoFollow(element.scrollHeight - element.scrollTop - element.clientHeight < 72);
          }
        }}
        ref={scrollRef}
        role="feed"
      >
        {orchestrators.map((session) => {
          const items = projectConversation(events, session.session_id, filters);
          return (
            <section className="orchestrator-segment" key={session.session_id}>
              <header className="orchestrator-segment-header">
                <div>
                  <Text strong>
                    Orchestrator #{session.sequence} · {session.session_id.slice(0, 6)}
                  </Text>
                  <Text className={`session-status status-${session.status}`}>
                    {STATUS_LABELS[session.status]}
                  </Text>
                </div>
                <Text type="secondary">
                  {session.startedAt ? new Date(session.startedAt).toLocaleTimeString() : "等待事件"}
                  {session.decision_kind ? ` · ${DECISION_LABELS[session.decision_kind]}` : " · 等待决策"}
                </Text>
              </header>
              <DecisionSummary output={decisionOutput(events, session.session_id)} />
              <ConversationView
                emptyDescription="此 Orchestrator session 尚无会话内容"
                items={items}
                onOpenSubagent={onOpenAgent}
                onOpenSystemAgent={onOpenAgent}
                virtualize={false}
              />
            </section>
          );
        })}
      </div>
      {!autoFollow && (
        <Button
          className="follow-button"
          icon={<DownOutlined />}
          onClick={() => {
            setAutoFollow(true);
            if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
          }}
        >
          跟随最新消息
        </Button>
      )}
    </div>
  );
}
