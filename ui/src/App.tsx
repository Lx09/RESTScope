/** Assemble run controls, the Main Agent conversation, and auxiliary Drawers. */

import {
  BugOutlined,
  DisconnectOutlined,
  MoonOutlined,
  RobotOutlined,
  SearchOutlined,
  SunOutlined,
} from "@ant-design/icons";
import {
  Alert,
  Badge,
  Breadcrumb,
  Button,
  ConfigProvider,
  Drawer,
  Empty,
  Flex,
  Input,
  Select,
  Switch,
  Typography,
} from "antd";
import { useEffect, useMemo, useReducer, useRef, useState } from "react";

import { ConversationView } from "./components/ConversationView";
import { FloatingTodo } from "./components/FloatingTodo";
import { RunHistoryBar } from "./components/RunHistoryBar";
import { projectConversation, projectMainConversation } from "./conversationProjector";
import {
  EMPTY_FILTERS,
  STATUS_LABELS,
} from "./presentation";
import {
  RunHistoryStore,
  RunHistoryWriter,
  observerStateToSnapshot,
  selectObserverView,
  type HistoryViewMode,
  type RunHistoryListing,
  type RunHistorySaveResult,
  type RunHistoryStorageStatus,
  type RunHistorySummary,
} from "./runHistory";
import { initialObserverState, observerReducer } from "./state";
import { connectLiveRun, type LiveConnection } from "./stream";
import { observerTheme, type ThemeMode } from "./theme";
import type {
  AgentIdentity,
  EventStatus,
  ObserverSnapshot,
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
  const [liveState, dispatch] = useReducer(observerReducer, initialObserverState);
  const [streamStatus, setStreamStatus] = useState<StreamStatus>("connecting");
  const [connectionError, setConnectionError] = useState<string | null>(null);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [historyWarning, setHistoryWarning] = useState<string | null>(null);
  const [historyStatus, setHistoryStatus] = useState<RunHistoryStorageStatus>("loading");
  const [historyReady, setHistoryReady] = useState(false);
  const [historySummaries, setHistorySummaries] = useState<RunHistorySummary[]>([]);
  const [automaticHistory, setAutomaticHistory] = useState<ObserverSnapshot | null>(null);
  const [selectedHistory, setSelectedHistory] = useState<ObserverSnapshot | null>(null);
  const [historyMode, setHistoryMode] = useState<HistoryViewMode>("auto");
  const [filters, setFilters] = useState<TimelineFilters>(EMPTY_FILTERS);
  const [themeMode, setThemeMode] = useState<ThemeMode>(readThemePreference);
  const [selectedSubagentId, setSelectedSubagentId] = useState<string | null>(null);
  const [selectedSystemAgentId, setSelectedSystemAgentId] = useState<string | null>(null);
  const [, refreshElapsed] = useState(0);
  const historyStore = useRef<RunHistoryStore | null>(null);
  const historyWriter = useRef<RunHistoryWriter | null>(null);
  const selectionRequest = useRef(0);

  function acceptHistoryListing(listing: RunHistoryListing): void {
    setHistorySummaries(listing.summaries);
    setHistoryWarning(
      listing.invalidCount > 0
        ? `${listing.invalidCount} 条本地历史格式不兼容，已安全忽略。可使用“清空全部”删除。`
        : null,
    );
  }

  function acceptSavedRun(result: RunHistorySaveResult): void {
    setHistorySummaries((current) => {
      const deleted = new Set(result.deletedRunIds);
      return [
        result.summary,
        ...current.filter((summary) => (
          summary.runId !== result.summary.runId && !deleted.has(summary.runId)
        )),
      ]
        .sort((left, right) => right.savedAt.localeCompare(left.savedAt))
        .slice(0, 5);
    });
  }

  useEffect(() => {
    let cancelled = false;
    if (window.indexedDB === undefined) {
      setHistoryStatus("error");
      setHistoryError("当前浏览器不支持 IndexedDB；实时观察不受影响。");
      return undefined;
    }

    const store = new RunHistoryStore(window.indexedDB);
    const writer = new RunHistoryWriter(store, {
      delayMs: 100,
      onSaving: () => {
        if (!cancelled) setHistoryStatus("saving");
      },
      onSaved: (result) => {
        if (cancelled) return;
        acceptSavedRun(result);
        setHistoryStatus("saved");
        setHistoryError(null);
      },
      onError: (message) => {
        if (cancelled) return;
        setHistoryStatus("error");
        setHistoryError(`浏览器未能保存最新运行：${message}。实时观察仍会继续。`);
      },
    });
    historyStore.current = store;
    historyWriter.current = writer;

    // Startup restoration and the server snapshot run independently. The
    // view selector below gives a real live Run priority unless the user has
    // explicitly chosen a historical record.
    const initialize = store.list().then(async (listing) => {
      if (cancelled) return;
      acceptHistoryListing(listing);
      const newest = listing.summaries[0];
      if (newest !== undefined) {
        const loaded = await store.load(newest.runId);
        if (!cancelled && loaded.record !== null) setAutomaticHistory(loaded.record.snapshot);
      }
      if (!cancelled) {
        setHistoryStatus("ready");
        setHistoryReady(true);
      }
    }).catch((error: unknown) => {
      if (cancelled) return;
      const message = error instanceof Error ? error.message : "IndexedDB could not be opened";
      setHistoryStatus("error");
      setHistoryError(`本地运行历史不可用：${message}。实时观察不受影响。`);
    });

    const flushBeforePageLeaves = () => {
      void writer.flush();
    };
    window.addEventListener("pagehide", flushBeforePageLeaves);
    return () => {
      cancelled = true;
      window.removeEventListener("pagehide", flushBeforePageLeaves);
      historyStore.current = null;
      historyWriter.current = null;
      // React cannot await effect cleanup, but starting the final IndexedDB
      // transaction here gives normal navigation the best chance to retain the
      // latest complete state. StrictMode's first mount closes only its handle.
      void writer.flush().finally(async () => {
        await initialize.catch(() => undefined);
        store.close();
      });
    };
  }, []);

  useEffect(() => {
    let connection: LiveConnection | null = null;
    let cancelled = false;
    const controller = new AbortController();
    void connectLiveRun(dispatch, (status) => {
      setStreamStatus(status);
      if (status === "live") setConnectionError(null);
    }, controller.signal)
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
    if (!historyReady || liveState.run === null) return;
    historyWriter.current?.schedule(observerStateToSnapshot(liveState));
  }, [historyReady, liveState]);

  const selectedView = useMemo(
    () => selectObserverView(liveState, automaticHistory, selectedHistory, historyMode),
    [automaticHistory, historyMode, liveState, selectedHistory],
  );
  const state = selectedView.state;
  const displayedRunId = state.run?.run_id ?? null;
  const displayedHistorySummary = selectedView.source === "history"
    ? historySummaries.find((summary) => summary.runId === displayedRunId) ?? null
    : null;

  useEffect(() => {
    if (selectedView.source === "history" || state.run?.ended_at) return undefined;
    const timer = window.setInterval(() => refreshElapsed((value) => value + 1), 1000);
    return () => window.clearInterval(timer);
  }, [selectedView.source, state.run?.ended_at]);

  const allEvents = useMemo(
    () => state.eventIds.map((eventId) => state.eventById[eventId]),
    [state.eventById, state.eventIds],
  );

  const conversation = useMemo(
    () => projectMainConversation(allEvents, filters),
    [allEvents, filters],
  );
  const selectedSubagent = selectedSubagentId
    ? conversation.sessionAgents[selectedSubagentId] ?? null
    : null;
  const subagentItems = useMemo(
    () => selectedSubagentId
      ? projectConversation(allEvents, selectedSubagentId, filters)
      : [],
    [allEvents, filters, selectedSubagentId],
  );
  const selectedSystemAgent = selectedSystemAgentId
    ? conversation.sessionAgents[selectedSystemAgentId] ?? null
    : null;
  const systemAgentItems = useMemo(
    () => selectedSystemAgentId
      ? projectConversation(allEvents, selectedSystemAgentId, filters)
      : [],
    [allEvents, filters, selectedSystemAgentId],
  );

  useEffect(() => {
    if (selectedSubagentId && !conversation.sessionAgents[selectedSubagentId]) {
      setSelectedSubagentId(null);
    }
  }, [conversation.sessionAgents, selectedSubagentId]);

  useEffect(() => {
    if (
      selectedSystemAgentId
      && conversation.sessionAgents[selectedSystemAgentId]?.lifecycle !== "system"
    ) {
      setSelectedSystemAgentId(null);
    }
  }, [conversation.sessionAgents, selectedSystemAgentId]);

  const subagentBreadcrumb = useMemo(() => {
    if (!selectedSubagent) return [];
    const agents = conversation.sessionAgents;
    const path: AgentIdentity[] = [];
    let current: AgentIdentity | undefined = selectedSubagent;
    for (let depth = 0; current?.lifecycle === "subagent" && depth < 3; depth += 1) {
      path.unshift(current);
      const parentId: string | null | undefined = current.parent_session_id;
      const parent: AgentIdentity | undefined = parentId ? agents[parentId] : undefined;
      current = parent?.lifecycle === "subagent" ? parent : undefined;
    }
    return path;
  }, [conversation.sessionAgents, selectedSubagent]);
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

  async function loadHistory(runId: string): Promise<void> {
    const store = historyStore.current;
    if (store === null) return;
    const requestNumber = selectionRequest.current + 1;
    selectionRequest.current = requestNumber;
    try {
      const loaded = await store.load(runId);
      if (requestNumber !== selectionRequest.current) return;
      if (loaded.record === null) {
        setHistoryWarning(
          loaded.invalid
            ? "所选本地历史格式不兼容，已安全忽略。"
            : "所选本地历史已不存在。",
        );
        setHistoryMode("live");
        setSelectedHistory(null);
        return;
      }
      setSelectedHistory(loaded.record.snapshot);
      setHistoryMode("history");
    } catch (error) {
      const message = error instanceof Error ? error.message : "History record could not be loaded";
      setHistoryError(`无法读取所选本地历史：${message}`);
      setHistoryStatus("error");
    }
  }

  function selectHistory(runId: string | null): void {
    if (runId === null) {
      selectionRequest.current += 1;
      setSelectedHistory(null);
      setHistoryMode("live");
      return;
    }
    void loadHistory(runId);
  }

  async function loadNewestAutomaticHistory(listing: RunHistoryListing): Promise<void> {
    const store = historyStore.current;
    const newest = listing.summaries[0];
    if (store === null || newest === undefined) {
      setAutomaticHistory(null);
      return;
    }
    const loaded = await store.load(newest.runId);
    setAutomaticHistory(loaded.record?.snapshot ?? null);
  }

  async function deleteSelectedHistory(): Promise<void> {
    const runId = selectedView.source === "history" ? displayedRunId : null;
    const store = historyStore.current;
    if (runId === null || store === null) return;
    historyWriter.current?.cancelPending();
    try {
      const listing = await store.delete(runId);
      acceptHistoryListing(listing);
      setSelectedHistory(null);
      setHistoryMode(liveState.run === null ? "auto" : "live");
      await loadNewestAutomaticHistory(listing);
      setHistoryStatus("ready");
    } catch (error) {
      const message = error instanceof Error ? error.message : "History record could not be deleted";
      setHistoryError(`无法删除本地历史：${message}`);
      setHistoryStatus("error");
    }
  }

  async function clearHistory(): Promise<void> {
    const store = historyStore.current;
    if (store === null) return;
    historyWriter.current?.cancelPending();
    try {
      await store.clear();
      setHistorySummaries([]);
      setAutomaticHistory(null);
      setSelectedHistory(null);
      setHistoryWarning(null);
      setHistoryMode(liveState.run === null ? "auto" : "live");
      setHistoryStatus("ready");
      setHistoryError(null);
    } catch (error) {
      const message = error instanceof Error ? error.message : "Browser history could not be cleared";
      setHistoryError(`无法清空本地历史：${message}`);
      setHistoryStatus("error");
    }
  }

  return (
    <ConfigProvider theme={observerTheme(themeMode)}>
      <div className={`observer-shell theme-${themeMode}`}>
        <header className="run-header">
          <div className="brand-block">
            <BugOutlined className="brand-icon" />
            <div>
              <Title level={4}>RESTScope Live Observer</Title>
              <Text type="secondary">只读 · 实时与本地历史 · 浏览器 IndexedDB</Text>
            </div>
          </div>
          <Flex className="run-metrics" align="center" gap={18} wrap>
            <div className="metric"><span>运行状态</span><strong>{selectedView.source === "history" && state.run?.status === "running" ? "历史快照 / 可能已中断" : state.run?.status ?? "等待运行"}</strong></div>
            <div className="metric"><span>耗时</span><strong className="mono">{elapsed(state.run?.started_at, state.run?.ended_at ?? displayedHistorySummary?.savedAt)}</strong></div>
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

        <RunHistoryBar
          summaries={historySummaries}
          selectedRunId={selectedView.source === "history" ? displayedRunId : null}
          source={selectedView.source}
          liveRunId={liveState.run?.run_id ?? null}
          storageStatus={historyStatus}
          historyRunStatus={selectedView.source === "history" ? state.run?.status ?? null : null}
          canClear={historySummaries.length > 0 || historyWarning !== null}
          onSelect={selectHistory}
          onReturnLive={() => selectHistory(null)}
          onDeleteSelected={() => { void deleteSelectedHistory(); }}
          onClear={() => { void clearHistory(); }}
        />

        <section className="filter-bar" aria-label="会话筛选">
          <Input
            allowClear
            prefix={<SearchOutlined />}
            placeholder="搜索 Prompt、Reasoning 或 Response"
            value={filters.search}
            onChange={(event) => updateFilter("search", event.target.value)}
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

        {(historyError || historyWarning) && (
          <Alert
            className="connection-alert"
            title={historyError ? "本地历史暂不可用" : "部分本地历史已忽略"}
            description={historyError ?? historyWarning}
            showIcon
            type="warning"
          />
        )}

        <main className="observer-main">
          <section
            aria-label="Main Agent 会话"
            className="conversation-region"
            data-cursor={state.latestCursor}
            data-run-id={state.run?.run_id ?? ""}
            data-testid="conversation-surface"
          >
            {conversation.mainAgent ? (
              <ConversationView
                items={conversation.items}
                onOpenSubagent={setSelectedSubagentId}
                onOpenSystemAgent={setSelectedSystemAgentId}
              />
            ) : (
              <Empty
                className="main-agent-empty"
                description="此运行未启动 Main Agent"
              >
                <Text type="secondary">
                  旧 Agent 运行不会被冒充为 Main Agent；会话将在 lifecycle=main 出现后展示。
                </Text>
              </Empty>
            )}
          </section>
        </main>

        <FloatingTodo
          historical={selectedView.source === "history"}
          todo={state.todo}
        />

        <Drawer
          className="subagent-drawer"
          focusable={{ trap: true, focusTriggerAfterClose: true }}
          onClose={() => setSelectedSubagentId(null)}
          open={selectedSubagent !== null}
          placement="right"
          title={(
            <Flex align="center" gap={8} wrap>
              {selectedSubagent?.parent_session_id
                && conversation.sessionAgents[selectedSubagent.parent_session_id]?.lifecycle
                  === "subagent" && (
                <Button
                  onClick={() => setSelectedSubagentId(selectedSubagent.parent_session_id ?? null)}
                  size="small"
                  type="text"
                >
                  返回
                </Button>
              )}
              <Breadcrumb
                items={subagentBreadcrumb.map((agent) => ({
                  title: agent.session_id === selectedSubagentId
                    ? agent.profile_name ?? agent.name
                    : (
                      <button
                        className="breadcrumb-button"
                        onClick={() => setSelectedSubagentId(agent.session_id)}
                        type="button"
                      >
                        {agent.profile_name ?? agent.name}
                      </button>
                    ),
                }))}
              />
            </Flex>
          )}
          size={720}
        >
          {selectedSubagent && (
            <ConversationView
              emptyDescription="此 Subagent 尚无会话内容"
              items={subagentItems}
              onOpenSubagent={setSelectedSubagentId}
              onOpenSystemAgent={setSelectedSystemAgentId}
            />
          )}
        </Drawer>

        <Drawer
          className="system-agent-drawer"
          focusable={{ trap: true, focusTriggerAfterClose: true }}
          onClose={() => setSelectedSystemAgentId(null)}
          open={selectedSystemAgent?.lifecycle === "system"}
          placement="right"
          title={(
            <Flex align="center" gap={8}>
              <RobotOutlined aria-hidden />
              <span>{selectedSystemAgent?.profile_name ?? "System Agent"}</span>
            </Flex>
          )}
          size={720}
        >
          {selectedSystemAgent?.lifecycle === "system" && (
            <ConversationView
              emptyDescription="此 System Agent 尚无会话内容"
              items={systemAgentItems}
              onOpenSubagent={setSelectedSubagentId}
              onOpenSystemAgent={setSelectedSystemAgentId}
            />
          )}
        </Drawer>
      </div>
    </ConfigProvider>
  );
}
