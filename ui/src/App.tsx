/** Assemble the fixed run header, Agent session canvas, and Worklist sidebar. */

import {
  BugOutlined,
  DisconnectOutlined,
  MoonOutlined,
  SearchOutlined,
  SunOutlined,
} from "@ant-design/icons";
import {
  Alert,
  Badge,
  ConfigProvider,
  Flex,
  Input,
  Select,
  Switch,
  Typography,
} from "antd";
import { useEffect, useMemo, useReducer, useState } from "react";

import { EventCanvas } from "./components/EventCanvas";
import { WorklistPanel } from "./components/WorklistPanel";
import {
  EMPTY_FILTERS,
  KIND_LABELS,
  STATUS_LABELS,
  TOOL_FAMILY_LABELS,
} from "./presentation";
import { initialObserverState, observerReducer } from "./state";
import { connectLiveRun, type LiveConnection } from "./stream";
import { observerTheme, type ThemeMode } from "./theme";
import type {
  EventKind,
  EventStatus,
  StreamStatus,
  TimelineFilters,
} from "./types";

const { Text, Title } = Typography;
const THEME_KEY = "restscope-observer-theme";

export function readThemePreference(): ThemeMode {
  return window.localStorage.getItem(THEME_KEY) === "light" ? "light" : "dark";
}

function elapsed(startedAt?: string | null, endedAt?: string | null): string {
  if (!startedAt) return "00:00:00";
  const end = endedAt ? new Date(endedAt).getTime() : Date.now();
  const totalSeconds = Math.max(0, Math.floor((end - new Date(startedAt).getTime()) / 1000));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  return [hours, minutes, seconds].map((value) => String(value).padStart(2, "0")).join(":");
}

const STREAM_PRESENTATION: Record<StreamStatus, { status: "success" | "processing" | "warning" | "default"; label: string }> = {
  connecting: { status: "processing", label: "SSE 连接中" },
  live: { status: "success", label: "SSE 实时" },
  reconnecting: { status: "warning", label: "SSE 重连中" },
  closed: { status: "default", label: "SSE 已关闭" },
};

export default function ObserverApp() {
  const [state, dispatch] = useReducer(observerReducer, initialObserverState);
  const [streamStatus, setStreamStatus] = useState<StreamStatus>("connecting");
  const [connectionError, setConnectionError] = useState<string | null>(null);
  const [filters, setFilters] = useState<TimelineFilters>(EMPTY_FILTERS);
  const [themeMode, setThemeMode] = useState<ThemeMode>(readThemePreference);
  const [, refreshElapsed] = useState(0);

  useEffect(() => {
    let connection: LiveConnection | null = null;
    let cancelled = false;
    const controller = new AbortController();
    void connectLiveRun(dispatch, setStreamStatus, controller.signal)
      .then((opened) => {
        if (cancelled) opened.close();
        else connection = opened;
      })
      .catch((error: unknown) => {
        if (!cancelled) {
          setConnectionError(error instanceof Error ? error.message : "Observer connection failed");
          setStreamStatus("reconnecting");
        }
      });
    return () => {
      cancelled = true;
      controller.abort();
      connection?.close();
    };
  }, []);

  useEffect(() => {
    if (state.run?.ended_at) return undefined;
    const timer = window.setInterval(() => refreshElapsed((value) => value + 1), 1000);
    return () => window.clearInterval(timer);
  }, [state.run?.ended_at]);

  const allEvents = useMemo(
    () => state.eventIds.map((eventId) => state.eventById[eventId]),
    [state.eventById, state.eventIds],
  );

  const agentOptions = useMemo(
    () => [...new Set(allEvents.map((event) => event.agent?.name).filter(Boolean) as string[])]
      .sort()
      .map((value) => ({ label: value, value })),
    [allEvents],
  );
  const runningEvents = allEvents.filter((event) => event.status === "running");
  const currentScopedEvent = [...runningEvents, ...allEvents].reverse().find((event) => event.operation_key);
  const failedCount = allEvents.filter((event) => event.status === "failed").length;
  const streamView = STREAM_PRESENTATION[streamStatus];

  function updateFilter<Key extends keyof TimelineFilters>(key: Key, value: TimelineFilters[Key]) {
    setFilters((current) => ({ ...current, [key]: value }));
  }

  function toggleTheme(checked: boolean) {
    const next = checked ? "light" : "dark";
    setThemeMode(next);
    window.localStorage.setItem(THEME_KEY, next);
  }

  return (
    <ConfigProvider theme={observerTheme(themeMode)}>
      <div className={`observer-shell theme-${themeMode}`}>
        <header className="run-header">
          <div className="brand-block">
            <BugOutlined className="brand-icon" />
            <div>
              <Title level={4}>RESTScope Live Observer</Title>
              <Text type="secondary">只读 · 当前运行 · 本机内存</Text>
            </div>
          </div>
          <Flex className="run-metrics" align="center" gap={18} wrap>
            <div className="metric"><span>运行状态</span><strong>{state.run?.status ?? "等待运行"}</strong></div>
            <div className="metric"><span>耗时</span><strong className="mono">{elapsed(state.run?.started_at, state.run?.ended_at)}</strong></div>
            <div className="metric metric-wide"><span>当前 operation / round</span><strong>{currentScopedEvent?.operation_key ?? "—"} {currentScopedEvent?.round_number !== null && currentScopedEvent?.round_number !== undefined ? `/ ${currentScopedEvent.round_number}` : ""}</strong></div>
            <div className="metric"><span>事件</span><strong>{allEvents.length.toLocaleString()}</strong></div>
            <div className="metric"><span>失败</span><strong className={failedCount ? "danger-text" : ""}>{failedCount}</strong></div>
            <Badge status={streamView.status} text={streamView.label} />
            <Switch
              checked={themeMode === "light"}
              checkedChildren={<SunOutlined />}
              unCheckedChildren={<MoonOutlined />}
              onChange={toggleTheme}
              aria-label="切换深浅主题"
            />
          </Flex>
        </header>

        <section className="filter-bar" aria-label="画布筛选">
          <Input
            allowClear
            prefix={<SearchOutlined />}
            placeholder="搜索 Agent、operation、工具输入输出或测试用例"
            value={filters.search}
            onChange={(event) => updateFilter("search", event.target.value)}
          />
          <Select
            aria-label="按 Agent 筛选"
            allowClear
            mode="multiple"
            maxTagCount="responsive"
            options={agentOptions}
            placeholder="Agent"
            value={filters.agents}
            onChange={(value) => updateFilter("agents", value)}
          />
          <Select
            aria-label="按事件类型筛选"
            allowClear
            mode="multiple"
            maxTagCount="responsive"
            options={Object.entries(KIND_LABELS).map(([value, label]) => ({ value, label }))}
            placeholder="事件类型"
            value={filters.kinds}
            onChange={(value) => updateFilter("kinds", value as EventKind[])}
          />
          <Select
            aria-label="按工具家族筛选"
            allowClear
            mode="multiple"
            maxTagCount="responsive"
            options={Object.entries(TOOL_FAMILY_LABELS).map(([value, label]) => ({ value, label }))}
            placeholder="工具家族"
            value={filters.toolFamilies}
            onChange={(value) => updateFilter("toolFamilies", value)}
          />
          <Select
            aria-label="按状态筛选"
            allowClear
            mode="multiple"
            maxTagCount="responsive"
            options={Object.entries(STATUS_LABELS).map(([value, label]) => ({ value, label }))}
            placeholder="状态"
            value={filters.statuses}
            onChange={(value) => updateFilter("statuses", value as EventStatus[])}
          />
        </section>

        {connectionError && (
          <Alert
            className="connection-alert"
            icon={<DisconnectOutlined />}
            title="实时连接暂不可用"
            description={connectionError}
            showIcon
            type="warning"
          />
        )}

        <main className="observer-main">
          <EventCanvas
            events={allEvents}
            filters={filters}
            latestCursor={state.latestCursor}
            runId={state.run?.run_id ?? null}
            themeMode={themeMode}
          />
          <WorklistPanel worklist={state.worklist} />
        </main>
      </div>
    </ConfigProvider>
  );
}
