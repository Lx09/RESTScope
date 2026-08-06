/** Fixed latest-Worklist sidebar; revisions remain visible through Tool nodes. */

import { CheckCircleOutlined, ClockCircleOutlined, OrderedListOutlined } from "@ant-design/icons";
import { Card, Empty, Flex, Progress, Space, Tag, Tooltip, Typography } from "antd";

import type { WorklistItem, WorklistState } from "../types";
import { CodeView } from "./ValueViews";

const { Paragraph, Text, Title } = Typography;

const REFERENCE_HELP = {
  failure: "E = this Failure Resolution session's exact Failure evidence",
  testCase: "TC = test case that produced or reproduced the evidence",
  candidate: "P = parameter patch candidate considered for this diagnosis",
};

function ReferenceTag({ reference, help }: { reference: string; help: string }) {
  return (
    <Tooltip title={help}>
      <Tag className="worklist-reference-tag">{reference}</Tag>
    </Tooltip>
  );
}

function FailureEvidence({
  item,
  failureMessages,
}: {
  item: WorklistItem;
  failureMessages: Record<string, string>;
}) {
  return (
    <section className="worklist-section" aria-label="Failure">
      <Text className="worklist-section-title" type="secondary">Failure</Text>
      <Space className="failure-list" orientation="vertical" size={6}>
        {item.source_failure_refs.map((failureRef) => (
          <div className="failure-entry" key={failureRef}>
            <span className={`failure-message ${failureMessages[failureRef] ? "" : "muted"}`}>
              {failureMessages[failureRef] ?? "Failure detail unavailable"}
            </span>{" "}
            <ReferenceTag reference={failureRef} help={REFERENCE_HELP.failure} />
          </div>
        ))}
      </Space>
    </section>
  );
}

function ReferenceSection({
  label,
  references,
  help,
}: {
  label: string;
  references: string[];
  help: string;
}) {
  return (
    <section className="worklist-section" aria-label={label}>
      <Text className="worklist-section-title" type="secondary">{label}</Text>
      <Flex className="worklist-tag-group" gap={4} wrap>
        {references.length
          ? references.map((reference) => (
            <ReferenceTag help={help} key={reference} reference={reference} />
          ))
          : <Text type="secondary">—</Text>}
      </Flex>
    </section>
  );
}

function SuspectedParameters({ parameters }: { parameters: string[] }) {
  return (
    <section className="worklist-section" aria-label="Suspected parameters">
      <Text className="worklist-section-title" type="secondary">Suspected parameters</Text>
      <Flex className="worklist-tag-group" gap={4} wrap>
        {parameters.length
          ? parameters.map((parameter) => (
            <Tag className="worklist-parameter-tag" color="purple" key={parameter}>
              {parameter}
            </Tag>
          ))
          : <Text type="secondary">—</Text>}
      </Flex>
    </section>
  );
}

export function WorklistPanel({ worklist }: { worklist: WorklistState | null }) {
  if (!worklist) {
    return (
      <aside className="worklist-panel" aria-label="最新 Worklist">
        <Card className="sidebar-card" size="small" title={<><OrderedListOutlined /> 最新 Worklist</>}>
          <Empty description="尚无成功写入的 Worklist" image={Empty.PRESENTED_IMAGE_SIMPLE} />
        </Card>
      </aside>
    );
  }

  const { snapshot } = worklist;
  return (
    <aside className="worklist-panel" aria-label="最新 Worklist">
      <Card
        className="sidebar-card"
        size="small"
        title={<><OrderedListOutlined /> 最新 Worklist</>}
        extra={<Tag>Revision {snapshot.revision}</Tag>}
      >
        <Space className="worklist-stack" orientation="vertical" size="middle">
          <div>
            <Flex justify="space-between">
              <Text>已有 decision / 总项</Text>
              <Text strong>{worklist.decided_count} / {worklist.total_count}</Text>
            </Flex>
            <Progress percent={worklist.percent} status="active" />
          </div>
          <div className="worklist-operation-note">
            <Text type="secondary">Operation</Text>
            <Tag className="worklist-operation-tag" color="geekblue">
              {worklist.operation_key ?? "未知"}
            </Tag>
          </div>
          <div className="active-worklist-note">
            <ClockCircleOutlined /> Active item · <strong>{snapshot.active_item_id ?? "无"}</strong>
          </div>
          <div className="worklist-items">
            {snapshot.items.map((item) => {
              const active = item.item_id === snapshot.active_item_id;
              const decided = item.decision !== null && item.decision !== undefined;
              return (
                <Card
                  className={`worklist-item ${active ? "is-active" : ""}`}
                  key={item.item_id}
                  size="small"
                  title={(
                    <Flex className="worklist-item-title" align="center" gap={6} wrap>
                      {decided ? <CheckCircleOutlined className="success-icon" /> : <ClockCircleOutlined />}
                      <span>{item.item_id}</span>
                      {active && <Tag color="blue">ACTIVE</Tag>}
                    </Flex>
                  )}
                >
                  <FailureEvidence item={item} failureMessages={worklist.failure_messages} />
                  <ReferenceSection
                    help={REFERENCE_HELP.testCase}
                    label="Test cases"
                    references={item.test_case_refs}
                  />
                  <SuspectedParameters parameters={item.suspected_parameters} />
                  <ReferenceSection
                    help={REFERENCE_HELP.candidate}
                    label="Patch candidates"
                    references={item.candidate_refs ?? []}
                  />
                  {item.progress && <Paragraph><Text type="secondary">Progress</Text><br />{item.progress}</Paragraph>}
                  {item.root_cause && <Paragraph><Text type="secondary">Root cause</Text><br />{item.root_cause}</Paragraph>}
                  {decided && (
                    <div>
                      <Title level={5}>Decision</Title>
                      <CodeView value={item.decision} label="复制 decision" />
                    </div>
                  )}
                </Card>
              );
            })}
          </div>
        </Space>
      </Card>
    </aside>
  );
}
