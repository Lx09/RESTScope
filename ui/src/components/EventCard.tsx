/** Read-only cards for Agent turns and Tool executions. */

import {
  ApiOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  CodeOutlined,
  DeploymentUnitOutlined,
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
import { Card, Collapse, Flex, Space, Table, Tabs, Tag, Typography } from "antd";
import type { TableColumnsType } from "antd";
import type { ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import {
  KIND_LABELS,
  ROLE_LABELS,
  TOOL_FAMILY_LABELS,
  toolFamily,
  visibleStatusLabel,
} from "../presentation";
import type { TimelineEvent } from "../types";
import { BodyView, CodeView, HeaderTable } from "./ValueViews";

const { Text } = Typography;

type UnknownRecord = Record<string, any>;

interface CanvasToolResult {
  id: string;
  name: string;
  toolCallId: string | null;
  matched: boolean;
  message: UnknownRecord;
}

function compactMessagePreview(value: unknown): string {
  const text = typeof value === "string" ? value : JSON.stringify(value);
  return (text ?? "").replace(/\s+/g, " ").trim().slice(0, 180);
}

const STATUS_ICONS: Record<string, ReactNode> = {
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
  plan: <OrderedListOutlined />,
  openapi: <FileSearchOutlined />,
  test_case: <CodeOutlined />,
  parameter_patch: <DeploymentUnitOutlined />,
  resource: <GlobalOutlined />,
  http: <ApiOutlined />,
  mcp: <ToolOutlined />,
  other: <QuestionCircleOutlined />,
};

function eventIcon(event: TimelineEvent): ReactNode {
  if (event.kind === "agent_turn") return <RobotOutlined />;
  const family = toolFamily(event);
  return family ? TOOL_ICONS[family] ?? <QuestionCircleOutlined /> : <ToolOutlined />;
}

function formatTime(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    fractionalSecondDigits: 3,
    hour12: false,
  }).format(new Date(value));
}

function EventMeta({ event }: { event: TimelineEvent }) {
  return (
    <Flex className="event-meta" gap={6} wrap>
      <Text className="mono">#{event.order}</Text>
      <Text>{formatTime(event.started_at)}</Text>
      {event.duration_ms !== null && <Text>{event.duration_ms.toLocaleString()} ms</Text>}
      {event.operation_key && <Tag>{event.operation_key}</Tag>}
      {event.round_number !== null && <Tag>Round {event.round_number}</Tag>}
      {event.agent && <Tag className="agent-tag">Agent · {event.agent.name}</Tag>}
    </Flex>
  );
}

/** Parse only top-level JSON objects and arrays from a message string.
 *
 * The first non-whitespace character is the deliberate display boundary. A
 * malformed container or a primitive remains ordinary Markdown so presenting
 * the message never changes or hides its original text.
 */
export function parseJsonContainerText(value: string): UnknownRecord | unknown[] | null {
  const trimmed = value.trimStart();
  if (!trimmed.startsWith("{") && !trimmed.startsWith("[")) return null;
  try {
    const parsed: unknown = JSON.parse(trimmed);
    return parsed !== null && typeof parsed === "object" ? parsed as UnknownRecord | unknown[] : null;
  } catch {
    return null;
  }
}

export function MarkdownValue({ value }: { value: unknown }) {
  if (value === null || value === undefined || value === "") {
    return <Text type="secondary">（空消息）</Text>;
  }
  if (typeof value !== "string") return <CodeView value={value ?? null} />;
  const structured = parseJsonContainerText(value);
  if (structured !== null) return <CodeView value={structured} />;
  return (
    <div className="markdown-content">
      <ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml>
        {value}
      </ReactMarkdown>
    </div>
  );
}

function toolResultStatus(result: CanvasToolResult): string {
  const explicit = result.message.status;
  if (typeof explicit === "string") return explicit;
  const content = result.message.content;
  if (content !== null && typeof content === "object" && !Array.isArray(content)) {
    const structured = content as UnknownRecord;
    if (typeof structured.status === "string") return structured.status;
  }
  if (typeof content === "string") {
    const structured = parseJsonContainerText(content);
    if (structured && !Array.isArray(structured) && typeof structured.status === "string") {
      return structured.status;
    }
    const match = content.match(/\bstatus\s*:\s*["']?([a-z_]+)/i);
    if (match) return match[1].toLowerCase();
  }
  return "returned";
}

function toolResultStatusTag(result: CanvasToolResult): ReactNode {
  const status = toolResultStatus(result);
  const labels: Record<string, string> = {
    succeeded: "成功",
    failed: "失败",
    warning: "警告",
    running: "运行中",
    returned: "已返回",
  };
  const colors: Record<string, string> = {
    succeeded: "green",
    failed: "red",
    warning: "gold",
    running: "blue",
    returned: "default",
  };
  return (
    <Tag color={colors[status] ?? "default"} icon={STATUS_ICONS[status]}>
      {labels[status] ?? status}
    </Tag>
  );
}

const TOOL_RESULT_COLUMNS: TableColumnsType<CanvasToolResult> = [
  {
    title: "工具",
    dataIndex: "name",
    key: "name",
    width: 190,
    ellipsis: { showTitle: true },
    render: (name: string) => <Text className="mono">{name}</Text>,
  },
  {
    title: "Call ID",
    dataIndex: "toolCallId",
    key: "toolCallId",
    width: 170,
    ellipsis: { showTitle: true },
    render: (toolCallId: string | null) => (
      <Text className="mono" type="secondary">{toolCallId ?? "不可用"}</Text>
    ),
  },
  {
    title: "状态",
    key: "status",
    width: 90,
    render: (_value, record) => toolResultStatusTag(record),
  },
  {
    title: "结果摘要",
    key: "summary",
    ellipsis: { showTitle: true },
    render: (_value, record) => compactMessagePreview(record.message.content),
  },
];

function ToolResultsTable({ results }: { results: CanvasToolResult[] }) {
  return (
    <section className="agent-message-tool-results">
      <Text type="secondary">Tool results ({results.length})</Text>
      <Table<CanvasToolResult>
        className="smoke-table tool-results-table"
        columns={TOOL_RESULT_COLUMNS}
        dataSource={results}
        expandable={{
          expandedRowRender: (record) => (
            <Space orientation="vertical" size="small" style={{ width: "100%" }}>
              {!record.matched && <Tag color="gold">调用来源不可用</Tag>}
              <MarkdownValue value={record.message.content} />
            </Space>
          ),
        }}
        pagination={false}
        rowKey="id"
        scroll={{ x: 700 }}
        size="small"
      />
    </section>
  );
}

/** Render one Agent message plus Tool results owned by that Assistant. */
export function AgentMessageBody({
  message,
  toolResults = [],
}: {
  message: UnknownRecord;
  toolResults?: CanvasToolResult[];
}) {
  return (
    <Space className="agent-message-body" orientation="vertical" size="small" style={{ width: "100%" }}>
      {(message.name || message.tool_call_id) && (
        <Flex align="center" gap={7} wrap>
          {message.name && <Tag icon={<ToolOutlined />}>Tool · {String(message.name)}</Tag>}
          {message.tool_call_id && <Tag className="mono">Call · {String(message.tool_call_id)}</Tag>}
        </Flex>
      )}
      <MarkdownValue value={message.content} />
      {Array.isArray(message.tool_calls) && message.tool_calls.length > 0 && (
        <section className="agent-message-tool-calls">
          <Text type="secondary">Tool calls ({message.tool_calls.length})</Text>
          <CodeView value={message.tool_calls} label="复制消息 Tool calls" />
        </section>
      )}
      {toolResults.length > 0 && <ToolResultsTable results={toolResults} />}
    </Space>
  );
}

function AgentMessage({ message }: { message: UnknownRecord }) {
  const role = typeof message.role === "string" ? message.role : "user";
  return (
    <div className={`agent-message agent-message-${role}`}>
      <Flex align="center" gap={7} className="agent-message-heading">
        <span aria-hidden>{ROLE_ICONS[role] ?? <QuestionCircleOutlined />}</span>
        <Tag>{ROLE_LABELS[role] ?? role}</Tag>
        {message.name && <Text className="mono" type="secondary">{String(message.name)}</Text>}
        {message.tool_call_id && <Text className="mono" type="secondary">{String(message.tool_call_id)}</Text>}
      </Flex>
      <AgentMessageBody message={message} />
    </div>
  );
}

function AgentTurnDetail({
  event,
  defaultTab = "input",
}: {
  event: TimelineEvent;
  defaultTab?: "input" | "output";
}) {
  const input = event.detail.input ?? {};
  const output = event.detail.output ?? {};
  const messages = Array.isArray(input.messages) ? input.messages : [];
  const toolCalls = Array.isArray(output.tool_calls) ? output.tool_calls : [];
  return (
    <Tabs
      defaultActiveKey={defaultTab}
      items={[
        {
          key: "input",
          label: `Prompt (${messages.length})`,
          children: (
            <Space orientation="vertical" size="small" style={{ width: "100%" }}>
              {messages.length
                ? messages.map((message: UnknownRecord, index: number) => (
                    <AgentMessage key={`${message.role ?? "message"}-${index}`} message={message} />
                  ))
                : <Text type="secondary">本轮尚未收到输入。</Text>}
              <Collapse
                ghost
                items={[
                  {
                    key: "raw-input",
                    label: "原始 Prompt JSON",
                    children: <CodeView value={messages} label="复制原始 Prompt" />,
                  },
                ]}
                size="small"
              />
            </Space>
          ),
        },
        {
          key: "output",
          label: "响应",
          children: (
            <Space orientation="vertical" size="middle" style={{ width: "100%" }}>
              {output.finish_reason && <Tag>Finish reason · {String(output.finish_reason)}</Tag>}
              <MarkdownValue value={output.content} />
              {output.structured !== undefined && output.structured !== null && (
                <section>
                  <Text type="secondary">Structured result</Text>
                  <CodeView value={output.structured} label="复制结构化结果" />
                </section>
              )}
              {toolCalls.length > 0 && (
                <section>
                  <Text type="secondary">Tool calls ({toolCalls.length})</Text>
                  <CodeView value={toolCalls} label="复制 Tool calls" />
                </section>
              )}
              <Collapse
                ghost
                items={[
                  {
                    key: "raw-output",
                    label: "原始响应 JSON",
                    children: <CodeView value={output} label="复制原始响应" />,
                  },
                ]}
                size="small"
              />
            </Space>
          ),
        },
      ]}
      size="small"
    />
  );
}

function RequestView({ request }: { request: UnknownRecord | null | undefined }) {
  if (!request) return <Text type="secondary">没有 Request 详情。</Text>;
  return (
    <Space className="http-detail" orientation="vertical" size="middle" style={{ width: "100%" }}>
      <Flex align="center" gap={8} wrap>
        <Tag className={`method-tag method-${String(request.method ?? "http").toLowerCase()}`}>
          {String(request.method ?? "HTTP")}
        </Tag>
        <Text className="http-url mono" copyable>{String(request.url ?? request.path ?? "")}</Text>
      </Flex>
      {(request.path || request.path_template) && (
        <Flex gap={8} wrap>
          {request.path && <Tag>Path · {String(request.path)}</Tag>}
          {request.path_template && <Tag>Template · {String(request.path_template)}</Tag>}
        </Flex>
      )}
      <HeaderTable headers={request.headers ?? null} />
      <section>
        <Text type="secondary">Query parameters</Text>
        <CodeView value={request.query ?? []} label="复制 query" />
      </section>
      <BodyView body={request.body} />
    </Space>
  );
}

function ResponseView({
  response,
  transportError,
}: {
  response: UnknownRecord | null | undefined;
  transportError?: UnknownRecord | null;
}) {
  if (!response) {
    return transportError
      ? <CodeView value={transportError} label="复制 transport error" />
      : <Text type="secondary">没有 Response 详情。</Text>;
  }
  return (
    <Space className="http-detail" orientation="vertical" size="middle" style={{ width: "100%" }}>
      <Flex align="center" gap={8} wrap>
        <Tag className={`http-status status-${Math.floor(Number(response.status_code) / 100)}xx`}>
          HTTP {String(response.status_code)} {String(response.reason_phrase ?? "")}
        </Tag>
        {response.size_bytes !== undefined && response.size_bytes !== null && (
          <Text type="secondary">正文 {String(response.size_bytes)} bytes</Text>
        )}
      </Flex>
      {response.url && <Text className="http-url mono" copyable>{String(response.url)}</Text>}
      <HeaderTable headers={response.headers ?? null} />
      {response.body_truncated && (
        <div className="truncation-note">
          <WarningOutlined /> 正文已按传输边界截断 · 保留 {String(response.retained_size_bytes ?? response.size_bytes)} bytes
        </div>
      )}
      <BodyView body={response.body} />
      {response.processor_result && (
        <section>
          <Text type="secondary">Response processing</Text>
          <CodeView value={response.processor_result} label="复制处理结果" />
        </section>
      )}
    </Space>
  );
}

function ToolDetail({ event }: { event: TimelineEvent }) {
  const input = event.detail.input ?? {};
  const output = event.detail.output ?? {};
  const isHttp = event.name === "restscope.http.request";
  return (
    <Tabs
      items={[
        {
          key: "input",
          label: "Input",
          children: isHttp ? (
            <Space orientation="vertical" size="middle" style={{ width: "100%" }}>
              <section>
                <Text type="secondary">Tool arguments</Text>
                <CodeView value={input.arguments ?? null} label="复制工具参数" />
              </section>
              <RequestView request={input.request} />
            </Space>
          ) : <CodeView value={input} label="复制工具输入" />,
        },
        {
          key: "output",
          label: "Output",
          children: isHttp ? (
            <Space orientation="vertical" size="middle" style={{ width: "100%" }}>
              <section>
                <Text type="secondary">ToolResult</Text>
                <CodeView value={output.tool_result ?? null} label="复制 ToolResult" />
              </section>
              <ResponseView response={output.response} transportError={output.transport_error} />
            </Space>
          ) : <CodeView value={output ?? null} label="复制工具输出" />,
        },
      ]}
      size="small"
    />
  );
}

/** Render complete read-only detail inside an expanded conversation row. */
export function EventDetail({
  event,
  defaultTab,
}: {
  event: TimelineEvent;
  defaultTab?: "input" | "output";
}): ReactNode {
  if (event.kind === "agent_turn") return <AgentTurnDetail event={event} defaultTab={defaultTab} />;
  return <ToolDetail event={event} />;
}

export function EventCard({ event }: { event: TimelineEvent }) {
  const family = toolFamily(event);
  const discriminator = family ?? event.kind;
  const typeLabel = family
    ? TOOL_FAMILY_LABELS[family] ?? family
    : KIND_LABELS[event.kind];
  return (
    <Card
      className={`event-card event-${event.kind} event-${discriminator} event-status-${event.status}`}
      size="small"
      title={(
        <Flex align="center" gap={10}>
          <span className="event-kind-icon" aria-hidden>{eventIcon(event)}</span>
          <div className="event-title-block">
            <Flex align="center" gap={6} wrap>
              <span className="event-title">{event.name}</span>
              <Tag>{typeLabel}</Tag>
            </Flex>
            <span className="event-summary">{event.summary}</span>
          </div>
        </Flex>
      )}
      extra={(
        <Tag className={`status-tag status-${event.status}`} icon={STATUS_ICONS[event.status]}>
          {visibleStatusLabel(event)}
        </Tag>
      )}
    >
      <EventMeta event={event} />
      <Collapse
        ghost
        items={[{ key: "detail", label: "查看完整详情", children: <EventDetail event={event} /> }]}
        size="small"
      />
    </Card>
  );
}
