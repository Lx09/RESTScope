/** Render one Agent session as a virtualized, Codex-style linear conversation. */

import {
  ApartmentOutlined,
  DownOutlined,
  LoadingOutlined,
  RobotOutlined,
  ToolOutlined,
} from "@ant-design/icons";
import { useVirtualizer } from "@tanstack/react-virtual";
import { Badge, Button, Collapse, Empty, Flex, Typography } from "antd";
import { useEffect, useRef, useState, type ReactNode } from "react";

import type { ConversationItem } from "../conversationProjector";
import { EventDetail, MarkdownValue } from "./EventCard";

const { Text } = Typography;

function outputContent(item: ConversationItem): unknown {
  const output = item.event?.detail.output;
  if (output && typeof output === "object" && "content" in output) {
    return output.content;
  }
  return null;
}

function finalSummary(item: ConversationItem): unknown {
  const result = item.event?.detail.task_result;
  if (!result || typeof result !== "object" || !("completion" in result)) {
    return outputContent(item);
  }
  const completion = result.completion;
  if (completion && typeof completion === "object" && "summary" in completion) {
    return completion.summary;
  }
  return outputContent(item);
}

function promptContent(item: ConversationItem): unknown {
  return item.message?.content ?? item.objective ?? null;
}

function ReasoningItemView({ item }: { item: ConversationItem }) {
  const [expanded, setExpanded] = useState(true);
  const reasoning = item.event?.detail.reasoning;
  const running = item.event?.status === "running" && typeof reasoning !== "string";

  if (running) {
    return (
      <article className="conversation-item reasoning-item">
        <Flex className="reasoning-running" align="center" gap={10}>
          <LoadingOutlined spin />
          <Text>正在推理</Text>
        </Flex>
      </article>
    );
  }

  function toggleFromKeyboard(event: React.KeyboardEvent<HTMLDivElement>) {
    if (event.key !== "Enter" && event.key !== " ") return;
    event.preventDefault();
    setExpanded((current) => !current);
  }

  return (
    <article className="conversation-item reasoning-item">
      <div className="conversation-content">
        {expanded ? (
          <div
            aria-expanded="true"
            aria-label="折叠推理内容"
            className="reasoning-content-toggle reasoning-prose"
            onClick={() => setExpanded(false)}
            onKeyDown={toggleFromKeyboard}
            role="button"
            tabIndex={0}
          >
            <MarkdownValue value={reasoning} />
          </div>
        ) : (
          <div
            aria-expanded="false"
            aria-label="展开推理内容"
            className="reasoning-content-toggle reasoning-collapsed"
            onClick={() => setExpanded(true)}
            onKeyDown={toggleFromKeyboard}
            role="button"
            tabIndex={0}
          >
            <span aria-hidden="true">…</span>
          </div>
        )}
      </div>
    </article>
  );
}

function ConversationItemView({
  item,
  onOpenSubagent,
  onOpenSystemAgent,
}: {
  item: ConversationItem;
  onOpenSubagent?: (sessionId: string) => void;
  onOpenSystemAgent?: (sessionId: string) => void;
}) {
  if (item.kind === "prompt") {
    return (
      <article className="conversation-item prompt-item">
        <div className="conversation-content">
          <MarkdownValue value={promptContent(item)} />
        </div>
      </article>
    );
  }

  if (item.kind === "reasoning") {
    return <ReasoningItemView item={item} />;
  }

  if (item.kind === "subagent") {
    const childName = item.childProfileName ?? item.childSessionId ?? "Subagent";
    return (
      <article className="conversation-item subagent-item">
        <button
          aria-label={`打开 ${childName} 子会话`}
          className="subagent-activity"
          disabled={!item.childSessionId || !onOpenSubagent}
          onClick={() => item.childSessionId && onOpenSubagent?.(item.childSessionId)}
          type="button"
        >
          <ApartmentOutlined />
          <span>{childName}</span>
        </button>
      </article>
    );
  }

  if (item.kind === "tool") {
    const event = item.event;
    if (!event) return null;
    const systemAgents = item.systemAgents ?? [];
    return (
      <article className="conversation-item tool-item">
        <div className="conversation-content">
          <Collapse
            ghost
            items={[{
              key: "tool",
              showArrow: false,
              label: (
                <Flex align="center" gap={8} justify="space-between" style={{ width: "100%" }}>
                  <Flex align="center" gap={8}>
                    <ToolOutlined />
                    <Text type="secondary">{event.name}</Text>
                  </Flex>
                  {systemAgents.length > 0 && (
                    <Badge
                      count={systemAgents.length}
                      overflowCount={99}
                      title={`${systemAgents.length} 个 System Agent 会话`}
                    />
                  )}
                </Flex>
              ),
              children: (
                <div>
                  <EventDetail event={event} />
                  {systemAgents.length > 0 && (
                    <section className="system-agent-list" aria-label="System Agent 会话">
                      <Text type="secondary">System Agent ({systemAgents.length})</Text>
                      {systemAgents.map((agent) => (
                        <button
                          aria-label={`打开 ${agent.profileName} System Agent 会话`}
                          className="system-agent-activity"
                          disabled={!onOpenSystemAgent}
                          key={agent.sessionId}
                          onClick={() => onOpenSystemAgent?.(agent.sessionId)}
                          type="button"
                        >
                          <RobotOutlined aria-hidden />
                          <span className="system-agent-name">{agent.profileName}</span>
                          <Badge
                            status={agent.status === "succeeded"
                              ? "success"
                              : agent.status === "running"
                                ? "processing"
                                : agent.status === "warning"
                                  ? "warning"
                                  : "error"}
                            text={agent.status === "succeeded"
                              ? "成功"
                              : agent.status === "running"
                                ? "运行中"
                                : agent.status === "warning"
                                  ? "警告"
                                  : "失败"}
                          />
                        </button>
                      ))}
                    </section>
                  )}
                </div>
              ),
            }]}
            size="small"
          />
        </div>
      </article>
    );
  }

  const isFinal = item.kind === "final_answer";
  return (
    <article className={`conversation-item assistant-item ${isFinal ? "final-answer-item" : ""}`}>
      <div className="conversation-content">
        <div className="assistant-markdown">
          <MarkdownValue value={isFinal ? finalSummary(item) : outputContent(item)} />
        </div>
      </div>
    </article>
  );
}

export function ConversationView({
  items,
  emptyDescription = "此会话暂无可展示内容",
  onOpenSubagent,
  onOpenSystemAgent,
  virtualize = true,
}: {
  items: ConversationItem[];
  emptyDescription?: ReactNode;
  onOpenSubagent?: (sessionId: string) => void;
  onOpenSystemAgent?: (sessionId: string) => void;
  virtualize?: boolean;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const [autoFollow, setAutoFollow] = useState(true);
  const virtualizer = useVirtualizer({
    count: items.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => 152,
    getItemKey: (index) => items[index]?.id ?? index,
    measureElement: (element) => element.getBoundingClientRect().height,
    overscan: 8,
    initialRect: { width: 800, height: 800 },
    useFlushSync: false,
  });

  useEffect(() => {
    if (autoFollow && items.length > 0) {
      virtualizer.scrollToIndex(items.length - 1, { align: "end" });
    }
  }, [autoFollow, items.length, virtualizer]);

  function handleScroll() {
    const element = scrollRef.current;
    if (!element) return;
    const distance = element.scrollHeight - element.scrollTop - element.clientHeight;
    setAutoFollow(distance < 72);
  }

  if (items.length === 0) {
    return <Empty className="conversation-empty" description={emptyDescription} />;
  }

  if (!virtualize) {
    return (
      <div aria-label="LLM 会话" className="conversation-test-list" role="feed">
        {items.map((item) => (
          <ConversationItemView
            item={item}
            key={item.id}
            onOpenSubagent={onOpenSubagent}
            onOpenSystemAgent={onOpenSystemAgent}
          />
        ))}
      </div>
    );
  }

  return (
    <div className="conversation-viewport-wrap">
      <div
        aria-label="LLM 会话"
        className="conversation-viewport"
        onScroll={handleScroll}
        ref={scrollRef}
        role="feed"
      >
        <div
          className="conversation-virtual-space"
          style={{ height: `${virtualizer.getTotalSize()}px` }}
        >
          {virtualizer.getVirtualItems().map((virtualItem) => {
            const item = items[virtualItem.index];
            return (
              <div
                data-index={virtualItem.index}
                key={item.id}
                ref={virtualizer.measureElement}
                style={{ transform: `translateY(${virtualItem.start}px)` }}
                className="conversation-virtual-item"
              >
                <ConversationItemView
                  item={item}
                  onOpenSubagent={onOpenSubagent}
                  onOpenSystemAgent={onOpenSystemAgent}
                />
              </div>
            );
          })}
        </div>
      </div>
      {!autoFollow && (
        <Button
          className="follow-button"
          icon={<DownOutlined />}
          onClick={() => {
            setAutoFollow(true);
            virtualizer.scrollToIndex(items.length - 1, { align: "end" });
          }}
        >
          跟随最新消息
        </Button>
      )}
    </div>
  );
}
