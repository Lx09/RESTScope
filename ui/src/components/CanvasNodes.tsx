/** Render compact Ant Design content inside the read-only G6 canvas nodes. */

import {
  ApiOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  CodeOutlined,
  DeploymentUnitOutlined,
  DownOutlined,
  ExperimentOutlined,
  FileSearchOutlined,
  GlobalOutlined,
  LoadingOutlined,
  OrderedListOutlined,
  QuestionCircleOutlined,
  RobotOutlined,
  SafetyCertificateOutlined,
  ToolOutlined,
  UserOutlined,
  WarningOutlined,
} from "@ant-design/icons";
import { Card, ConfigProvider, Flex, Tag, Typography } from "antd";
import type { ReactNode } from "react";

import type {
  AgentSessionCanvasNode,
  CanvasMessage,
  EventCanvasNode,
} from "../canvasModel";
import {
  AGENT_MESSAGE_COLLAPSED_CONTENT_HEIGHT,
  INLINE_AGENT_DETAIL_HEIGHT,
  INLINE_EVENT_DETAIL_HEIGHT,
} from "../canvasModel";
import {
  ROLE_LABELS,
  STATUS_LABELS,
  TOOL_FAMILY_LABELS,
  toolFamily,
  visibleStatusLabel,
} from "../presentation";
import { observerTheme, type ThemeMode } from "../theme";
import type { EventStatus, TimelineEvent } from "../types";
import { AgentMessageBody, EventDetail } from "./EventCard";
import { InlineReveal } from "./InlineReveal";

const { Text } = Typography;

const STATUS_ICONS: Record<EventStatus, ReactNode> = {
  running: <LoadingOutlined spin />,
  succeeded: <CheckCircleOutlined />,
  warning: <WarningOutlined />,
  failed: <CloseCircleOutlined />,
};

const ROLE_ICONS: Record<string, ReactNode> = {
  system: <SafetyCertificateOutlined />,
  user: <UserOutlined />,
  assistant: <RobotOutlined />,
  tool: <ToolOutlined />,
};

const TOOL_ICONS: Record<string, ReactNode> = {
  worklist: <OrderedListOutlined />,
  openapi: <FileSearchOutlined />,
  test_case: <CodeOutlined />,
  parameter_patch: <DeploymentUnitOutlined />,
  resource: <GlobalOutlined />,
  http: <ApiOutlined />,
  mcp: <ToolOutlined />,
  other: <QuestionCircleOutlined />,
};

function formatTime(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    fractionalSecondDigits: 3,
    hour12: false,
  }).format(new Date(value));
}

function StatusLabel({ status, label }: { status: EventStatus; label?: string }) {
  return (
    <Tag className={`status-tag status-${status}`} icon={STATUS_ICONS[status]}>
      {label ?? status}
    </Tag>
  );
}

function MessageCard({
  message,
  startedAt,
  onToggle,
}: {
  message: CanvasMessage;
  startedAt: string;
  onToggle: (expanded: boolean) => void;
}) {
  const toolCount = message.toolCallIds.length;
  return (
    <article
      className={[
        "canvas-message-card",
        `canvas-message-${message.role}`,
        message.exactMatch ? "is-match" : "",
        message.connectionContext ? "is-connection-context" : "",
        message.expanded ? "is-expanded" : "",
      ].filter(Boolean).join(" ")}
      data-expanded={message.expanded ? "true" : "false"}
      data-message-id={message.id}
    >
      <button
        aria-expanded={message.expanded}
        aria-label={`${message.expanded ? "收起" : "展开"}第 ${message.turnNumber} 轮 ${ROLE_LABELS[message.role] ?? message.role} 消息详情`}
        className="canvas-message-summary"
        data-expanded={message.expanded ? "true" : "false"}
        data-message-id={message.id}
        onClick={(event) => {
          event.stopPropagation();
          onToggle(!message.expanded);
        }}
        type="button"
      >
        <Flex align="center" gap={6} wrap={false}>
          <span className="canvas-message-icon" aria-hidden>
            {ROLE_ICONS[message.role] ?? <QuestionCircleOutlined />}
          </span>
          <Tag>{ROLE_LABELS[message.role] ?? message.role}</Tag>
          <Text className="canvas-message-turn" type="secondary">
            Turn {message.turnNumber}
          </Text>
          <Text className="canvas-message-time mono" type="secondary">
            {formatTime(startedAt)}
          </Text>
          <span className="canvas-expand-hint">
            <DownOutlined />
            {message.expanded ? "收起" : "展开"}
          </span>
        </Flex>
      </button>
      <InlineReveal
        ariaLabel={`第 ${message.turnNumber} 轮 ${ROLE_LABELS[message.role] ?? message.role} 完整消息`}
        collapsedHeight={AGENT_MESSAGE_COLLAPSED_CONTENT_HEIGHT}
        detail={(
          <AgentMessageBody
            message={message.message}
            toolResults={message.toolResults}
          />
        )}
        detailClassName="canvas-message-detail"
        expanded={message.expanded}
        expandedHeight={INLINE_AGENT_DETAIL_HEIGHT}
        preview={<span className="canvas-message-preview">{message.preview}</span>}
      />
      <Flex className="canvas-message-foot" align="center" gap={6}>
        <span aria-hidden className="canvas-message-foot-spacer" />
        {toolCount > 0 && <Tag color="gold">{toolCount} 个 Tool call</Tag>}
        {message.toolResults.length > 0 && (
          <Tag color="cyan">{message.toolResults.length} 个 Tool result</Tag>
        )}
        {message.toolCallId && <Tag className="mono">Call · {message.toolCallId}</Tag>}
        {message.connectionContext && <Tag color="blue">连线来源</Tag>}
      </Flex>
    </article>
  );
}

export interface AgentSessionNodeViewProps {
  node: AgentSessionCanvasNode;
  themeMode: ThemeMode;
  onOpenMessage: (message: CanvasMessage, expanded: boolean) => void;
  onToggleSession: (sessionId: string, collapsed: boolean) => void;
}

/** Display a whole Agent session and expose one visual port per message card. */
export function AgentSessionNodeView({
  node,
  themeMode,
  onOpenMessage,
  onToggleSession,
}: AgentSessionNodeViewProps) {
  const turnById = new Map(node.turns.map((turn) => [turn.event_id, turn]));
  const pathText = node.path.join(" / ");
  return (
    <ConfigProvider theme={observerTheme(themeMode)}>
      <Card
        className={[
          "canvas-node",
          "canvas-agent-node",
          `canvas-status-${node.status}`,
          node.contextOnly ? "is-context-only" : "",
        ].filter(Boolean).join(" ")}
        styles={{ body: { padding: 12 } }}
      >
        <button
          aria-label={node.collapsed ? "展开 Agent 会话" : "折叠 Agent 会话"}
          className="canvas-agent-header"
          data-collapsed={node.collapsed ? "true" : "false"}
          data-session-id={node.sessionId}
          onClick={(event) => {
            event.stopPropagation();
            onToggleSession(node.sessionId, !node.collapsed);
          }}
          title={node.collapsed ? "展开会话消息和工具分支" : "折叠会话消息和工具分支"}
          type="button"
        >
          <Flex align="flex-start" justify="space-between" gap={8}>
            <div className="canvas-node-title-block">
              <Tag className="agent-tag" icon={<RobotOutlined />}>Agent · {node.name}</Tag>
              {pathText !== node.name && (
                <Text className="canvas-node-path mono" type="secondary">{pathText}</Text>
              )}
            </div>
            <span className="canvas-collapse-hint">{node.collapsed ? "展开" : "折叠"}</span>
          </Flex>
          <Flex className="canvas-node-meta" align="center" gap={6} wrap>
            <StatusLabel status={node.status} label={STATUS_LABELS[node.status]} />
            <Tag>{node.turns.length} Turns</Tag>
            <Tag>{node.messages.length} Messages</Tag>
            {node.operationKey && <Tag>{node.operationKey}</Tag>}
            {node.roundNumber !== null && <Tag>Round {node.roundNumber}</Tag>}
          </Flex>
        </button>
        {node.collapsed ? (
          <div className="canvas-collapsed-note">
            已隐藏 {node.messages.length} 条消息与 {node.hiddenDescendantCount} 个下游节点
          </div>
        ) : (
          <div className="canvas-message-list">
            {node.messages.map((message) => (
              <MessageCard
                key={message.id}
                message={message}
                onToggle={(expanded) => onOpenMessage(message, expanded)}
                startedAt={turnById.get(message.turnEventId)?.started_at ?? node.startedAt}
              />
            ))}
          </div>
        )}
      </Card>
    </ConfigProvider>
  );
}

function asRecord(value: unknown): Record<string, any> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, any>
    : null;
}

function httpSummary(event: TimelineEvent): { method: string; url: string; status: string | null } | null {
  if (toolFamily(event) !== "http") return null;
  const input = asRecord(event.detail.input);
  const output = asRecord(event.detail.output);
  const request = asRecord(input?.request) ?? asRecord(input?.arguments);
  const response = asRecord(output?.response);
  return {
    method: String(request?.method ?? "HTTP"),
    url: String(request?.url ?? request?.path ?? "Request 尚未准备"),
    status: response?.status_code !== undefined ? String(response.status_code) : null,
  };
}

function smokePreview(event: TimelineEvent): string {
  const count = event.detail.case_count ?? (Array.isArray(event.detail.cases) ? event.detail.cases.length : 0);
  const success = event.detail.success_count ?? 0;
  return `${success} / ${count} 个测试用例成功`;
}

export interface EventCanvasNodeViewProps {
  node: EventCanvasNode;
  themeMode: ThemeMode;
  onOpen: (event: TimelineEvent, expanded: boolean) => void;
}

/** Display one Tool execution or Smoke Batch with inline expandable detail. */
export function EventCanvasNodeView({ node, themeMode, onOpen }: EventCanvasNodeViewProps) {
  const { event } = node;
  const family = toolFamily(event);
  const http = httpSummary(event);
  const icon = event.kind === "smoke_batch"
    ? <ExperimentOutlined />
    : TOOL_ICONS[family ?? "other"] ?? <ToolOutlined />;
  const typeLabel = event.kind === "smoke_batch"
    ? "Smoke Batch"
    : TOOL_FAMILY_LABELS[family ?? "other"] ?? "Tool";
  const compactPreview = http ? (
    <div className="canvas-http-summary">
      <Flex align="center" gap={6}>
        <Tag className={`method-tag method-${http.method.toLocaleLowerCase()}`}>{http.method}</Tag>
        {http.status && <Tag>HTTP {http.status}</Tag>}
      </Flex>
      <Text className="canvas-http-url mono">{http.url}</Text>
    </div>
  ) : event.kind === "smoke_batch" ? (
    <span className="canvas-event-preview">{smokePreview(event)}</span>
  ) : null;
  return (
    <ConfigProvider theme={observerTheme(themeMode)}>
      <Card
        className={[
          "canvas-node",
          "canvas-event-node",
          `canvas-event-${event.kind}`,
          `canvas-status-${event.status}`,
          node.contextOnly ? "is-context-only" : "",
        ].filter(Boolean).join(" ")}
        styles={{ body: { padding: 12 } }}
      >
        <button
          aria-expanded={node.expanded}
          aria-label={`${node.expanded ? "收起" : "展开"} ${event.kind === "smoke_batch" ? "Smoke Batch" : event.name} 详情`}
          className="canvas-event-summary-button"
          data-event-id={event.event_id}
          data-expanded={node.expanded ? "true" : "false"}
          onClick={(mouseEvent) => {
            mouseEvent.stopPropagation();
            onOpen(event, !node.expanded);
          }}
          type="button"
        >
          <Flex align="center" gap={7}>
            <span className="canvas-event-icon" aria-hidden>{icon}</span>
            <Tag>{typeLabel}</Tag>
            <StatusLabel status={event.status} label={visibleStatusLabel(event)} />
            <span className="canvas-expand-hint">
              <DownOutlined />
              {node.expanded ? "收起" : "展开"}
            </span>
          </Flex>
          <Text className="canvas-event-name mono" title={event.name}>{event.name}</Text>
        </button>
        <InlineReveal
          ariaLabel={`${event.name} 完整详情`}
          collapsedHeight={node.collapsedContentHeight}
          detail={<EventDetail event={event} />}
          detailClassName="canvas-event-detail"
          expanded={node.expanded}
          expandedHeight={INLINE_EVENT_DETAIL_HEIGHT}
          preview={compactPreview}
        />
        <Flex className="canvas-node-meta" align="center" gap={6} wrap>
          <Text className="mono" type="secondary">#{event.order}</Text>
          {event.duration_ms !== null && <Text type="secondary">{event.duration_ms.toLocaleString()} ms</Text>}
          {event.operation_key && <Tag>{event.operation_key}</Tag>}
        </Flex>
      </Card>
    </ConfigProvider>
  );
}
