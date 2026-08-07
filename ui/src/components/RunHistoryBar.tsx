/** Display browser-local Run choices without exposing runtime write controls. */

import {
  CloudServerOutlined,
  DatabaseOutlined,
  DeleteOutlined,
  HistoryOutlined,
} from "@ant-design/icons";
import { Button, Flex, Popconfirm, Select, Tag, Tooltip, Typography } from "antd";

import type {
  ObserverViewSource,
  RunHistoryStorageStatus,
  RunHistorySummary,
} from "../runHistory";

const { Text } = Typography;
const LIVE_VALUE = "__restscope_live__";

const STORAGE_LABELS: Record<RunHistoryStorageStatus, { color: string; text: string }> = {
  loading: { color: "processing", text: "载入历史" },
  ready: { color: "default", text: "本地可用" },
  saving: { color: "processing", text: "正在保存" },
  saved: { color: "success", text: "已保存" },
  error: { color: "error", text: "保存失败" },
};

export interface RunHistoryBarProps {
  summaries: RunHistorySummary[];
  selectedRunId: string | null;
  source: ObserverViewSource;
  liveRunId: string | null;
  storageStatus: RunHistoryStorageStatus;
  historyRunStatus: string | null;
  canClear: boolean;
  onSelect: (runId: string | null) => void;
  onReturnLive: () => void;
  onDeleteSelected: () => void;
  onClear: () => void;
}

function formatHistoryLabel(summary: RunHistorySummary): string {
  const timestamp = new Date(summary.startedAt).toLocaleString();
  const operation = summary.operationKey ?? "无 operation";
  return `${timestamp} · ${summary.status} · ${summary.runId} · ${operation} · ${summary.eventCount} 事件`;
}

/**
 * Render the live/history selector and browser-only deletion actions.
 * Deletion is deliberately described as local so it cannot be confused with
 * stopping, retrying, or otherwise changing a RESTScope Run.
 */
export function RunHistoryBar({
  summaries,
  selectedRunId,
  source,
  liveRunId,
  storageStatus,
  historyRunStatus,
  canClear,
  onSelect,
  onReturnLive,
  onDeleteSelected,
  onClear,
}: RunHistoryBarProps) {
  const storage = STORAGE_LABELS[storageStatus];
  const selectedValue = source === "history" && selectedRunId !== null
    ? selectedRunId
    : LIVE_VALUE;
  const options = [
    {
      label: liveRunId === null ? "实时（等待运行）" : `实时 · ${liveRunId}`,
      value: LIVE_VALUE,
    },
    ...summaries.map((summary) => ({
      label: formatHistoryLabel(summary),
      value: summary.runId,
    })),
  ];

  return (
    <section className="history-bar" aria-label="本地运行历史">
      <Flex className="history-controls" align="center" gap={8} wrap>
        <HistoryOutlined />
        <Select
          aria-label="选择实时或历史运行"
          className="history-select"
          options={options}
          value={selectedValue}
          onChange={(value) => onSelect(value === LIVE_VALUE ? null : value)}
        />
        {source === "history" && <Tag color="purple" icon={<DatabaseOutlined />}>本地历史</Tag>}
        {source === "history" && historyRunStatus === "running" && (
          <Tag color="warning">历史快照 / 可能已中断</Tag>
        )}
        <Tooltip title="IndexedDB 仅属于当前浏览器和当前页面地址">
          <Tag color={storage.color}>{storage.text}</Tag>
        </Tooltip>
        {source === "history" && liveRunId !== null && (
          <Button
            aria-label="返回实时"
            icon={<CloudServerOutlined />}
            onClick={onReturnLive}
          >
            返回实时
          </Button>
        )}
        {source === "history" && selectedRunId !== null && (
          <Popconfirm
            title="删除当前本地历史？"
            description="只删除浏览器缓存，不会停止或修改测试。"
            okText="确认删除"
            cancelText="取消"
            okButtonProps={{ danger: true }}
            onConfirm={onDeleteSelected}
          >
            <Button danger icon={<DeleteOutlined />} aria-label="删除当前历史" />
          </Popconfirm>
        )}
        {canClear && (
          <Popconfirm
            title="清空全部本地历史？"
            description="当前实时运行以后有新事件时仍会继续保存。"
            okText="确认清空"
            cancelText="取消"
            okButtonProps={{ danger: true }}
            onConfirm={onClear}
          >
            <Button type="text" danger>清空全部</Button>
          </Popconfirm>
        )}
      </Flex>
      <Text type="secondary" className="history-sensitive-note">
        本地历史包含请求凭据、Prompt、Tool 与 HTTP 详情；不会上传到后端数据库。
      </Text>
    </section>
  );
}
