/** Own the AntV G6 lifecycle for the read-only Agent session canvas.
 *
 * Semantic events stay in the page reducer exactly as the backend emitted
 * them. This component derives the visual graph, coalesces live revisions on
 * animation frames, expands details inside their owning nodes, and destroys
 * G6 cleanly when React StrictMode remounts it.
 */

import {
  AimOutlined,
  DeploymentUnitOutlined,
  MinusOutlined,
  PlusOutlined,
  VerticalAlignBottomOutlined,
} from "@ant-design/icons";
import { ReactNode } from "@antv/g6-extension-react";
import {
  AntVDagreLayout,
  CanvasEvent,
  ExtensionCategory,
  Graph,
  getExtension,
  register,
  type GraphData,
  type GraphOptions,
} from "@antv/g6";
import { Alert, Button, Empty, Flex, Space, Tag, Typography } from "antd";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  buildCanvasModel,
  eventDetailKey,
  messageDetailKey,
  messagePortPlacement,
  type AgentSessionCanvasNode,
  type CanvasMessage,
  type CanvasModel,
} from "../canvasModel";
import type { ThemeMode } from "../theme";
import type { TimelineEvent, TimelineFilters } from "../types";
import { AgentSessionNodeView, EventCanvasNodeView } from "./CanvasNodes";
import { detailMotionTiming } from "./InlineReveal";

const { Text, Title } = Typography;
const REACT_NODE_TYPE = "restscope-react-node";

if (!getExtension(ExtensionCategory.NODE, REACT_NODE_TYPE)) {
  register(ExtensionCategory.NODE, REACT_NODE_TYPE, ReactNode);
}

export interface EventCanvasProps {
  events: TimelineEvent[];
  filters: TimelineFilters;
  latestCursor: number;
  runId: string | null;
  themeMode: ThemeMode;
}

interface GraphCallbacks {
  openMessage: (message: CanvasMessage, expanded: boolean) => void;
  openEvent: (event: TimelineEvent, expanded: boolean) => void;
  toggleSession: (sessionId: string, collapsed: boolean) => void;
}

const EDGE_COLORS = {
  running: "#4096ff",
  succeeded: "#6c8fbd",
  warning: "#d89614",
  failed: "#ff4d4f",
};

interface PendingDetailMotion {
  expanded: boolean;
  sequence: number;
}

type GraphMotionOptions = Pick<GraphOptions, "animation" | "edge" | "node">;
type CanvasBehaviorOptions = NonNullable<GraphOptions["behaviors"]>;

function isTrackpadPinch(event: unknown): boolean {
  return typeof event === "object"
    && event !== null
    && "ctrlKey" in event
    && (event as { ctrlKey?: boolean }).ctrlKey === true;
}

/** Keep two-finger movement and pinch as distinct canvas gestures.
 *
 * Desktop browsers expose ordinary trackpad scrolling as a wheel event and a
 * trackpad pinch as a wheel event carrying `ctrlKey`. G6's default zoom
 * behavior consumes both, so RESTScope explicitly routes the two forms to
 * scroll and zoom behaviors instead.
 */
export function canvasNavigationBehaviors(): CanvasBehaviorOptions {
  return [
    "drag-canvas",
    {
      type: "scroll-canvas",
      enable: (event: unknown) => !isTrackpadPinch(event),
      range: Infinity,
      sensitivity: 1,
    },
    {
      type: "zoom-canvas",
      animation: false,
      enable: isTrackpadPinch,
      sensitivity: 1,
      trigger: [],
    },
  ];
}

const STABLE_LAYOUT_OPTIONS = {
  align: "UL",
  controlPoints: true,
  nodesep: 46,
  rankdir: "LR",
  ranksep: 120,
} as const;

/** Build the G6 motion that keeps node geometry and connected edges together. */
export function detailGraphMotionOptions(
  expanded: boolean,
  portKeys: readonly string[] = [],
): GraphMotionOptions {
  const timing = detailMotionTiming(expanded);
  const timed = { duration: timing.duration, easing: timing.easing };
  return {
    animation: timing,
    node: {
      type: REACT_NODE_TYPE,
      animation: { update: [
        { fields: ["x", "y"], ...timed },
        // React nodes live inside G6's HTML key shape. Animating the abstract
        // `size` attribute does not resize that DOM surface, so interpolate
        // its measured rectangle and invisible bounds explicitly.
        { shape: "key", fields: ["x", "y", "width", "height"], ...timed },
        { shape: "key-container", fields: ["x", "y", "width", "height"], ...timed },
        ...portKeys.map((key) => ({
          shape: `port-${key}`,
          fields: ["transform"],
          ...timed,
        })),
      ] },
    },
    edge: {
      type: "polyline",
      animation: {
        update: [{
          fields: ["sourceNode", "targetNode"],
          duration: timing.duration,
          easing: timing.easing,
        }],
      },
    },
  };
}

interface DetailGraphMotionRequest {
  expanded: boolean;
  portKeys: readonly string[];
}

/** Apply one structural render without letting option refreshes erase its diff.
 *
 * G6 refreshes every datum when node animation options change. Motion must
 * therefore be installed before the new sizes enter the model. Restoring the
 * normal non-animated mode is followed by one static draw so the refresh does
 * not leak into the next SSE or detail update.
 */
export async function renderStructuralGraphUpdate(
  graph: Graph,
  graphData: GraphData,
  layout: Parameters<Graph["setLayout"]>[0] | null,
  motion: DetailGraphMotionRequest | null,
): Promise<void> {
  if (motion) graph.setOptions(detailGraphMotionOptions(motion.expanded, motion.portKeys));
  // Pre-positioned data must bypass G6's layout adapter. The adapter copies
  // geometry but drops custom node data, including the stable `layer` that
  // distinguishes consecutive call groups.
  if (layout) graph.setLayout(layout);
  else graph.setOptions({ layout: undefined });
  graph.setData(graphData);
  try {
    await graph.render();
  } finally {
    if (motion && !graph.destroyed) {
      graph.setOptions(staticGraphMotionOptions());
      await graph.draw();
    }
  }
}

/** Position G6 data with AntV Dagre while preserving RESTScope's stable layer.
 *
 * G6 normally adapts its node data before invoking Dagre. That adapter omits
 * custom fields, so calling the exported layout directly is the only way to
 * let `data.layer` constrain a node to its assigned call-group column. The
 * returned data keeps the original React components and ports, adding only
 * the calculated x/y positions used by G6's normal renderer.
 */
export async function positionGraphDataByStableColumns(
  graphData: GraphData,
): Promise<GraphData> {
  const nodes = graphData.nodes ?? [];
  const edges = graphData.edges ?? [];
  if (!nodes.length) return graphData;

  const layout = new AntVDagreLayout({
    ...STABLE_LAYOUT_OPTIONS,
    nodeOrder: [...nodes]
      .sort((left, right) => Number(left.data?.order ?? 0) - Number(right.data?.order ?? 0))
      .map((node) => node.id),
    node: (node) => ({ id: node.id, data: node.data }),
    edge: (edge) => ({ id: edge.id, source: edge.source, target: edge.target }),
    nodeSize: (node) => {
      const size = node.style?.size;
      return Array.isArray(size) && size.length >= 2
        ? [Number(size[0]), Number(size[1])]
        : [10, 10];
    },
  });
  const positions = new Map<string, { x: number; y: number }>();
  try {
    await layout.execute({ nodes, edges });
    layout.forEachNode((node) => positions.set(String(node.id), { x: node.x, y: node.y }));
  } finally {
    layout.destroy();
  }

  return {
    ...graphData,
    nodes: nodes.map((node) => {
      const position = positions.get(String(node.id));
      return position
        ? { ...node, style: { ...node.style, x: position.x, y: position.y } }
        : node;
    }),
  };
}

function staticGraphMotionOptions(): GraphMotionOptions {
  return {
    animation: false,
    node: { type: REACT_NODE_TYPE, animation: false },
    edge: { type: "polyline", animation: false },
  };
}

/** Translate the view model into G6 data with a real port beside every message. */
export function graphDataForModel(
  model: CanvasModel,
  themeMode: ThemeMode,
  callbacks: GraphCallbacks,
): GraphData {
  return {
    nodes: model.nodes.map((node) => {
      if (node.kind === "agent_session") {
        const ports = [
          {
            key: "input",
            placement: [0, 0.15] as [number, number],
            r: 4,
            fill: "#4f8cff",
            stroke: "#d6e4ff",
            lineWidth: 1,
          },
          {
            key: "agent_header",
            placement: [1, 0.15] as [number, number],
            r: 4,
            fill: "#4f8cff",
            stroke: "#d6e4ff",
            lineWidth: 1,
          },
          ...(!node.collapsed
            ? node.messages.map((message, index) => ({
                key: message.portKey,
                placement: messagePortPlacement(node, index),
                r: message.connectionContext ? 5 : 3,
                fill: message.connectionContext ? "#faad14" : "#6c8fbd",
                stroke: "#fff7e6",
                lineWidth: 1,
              }))
            : []),
        ];
        return {
          id: node.id,
          type: REACT_NODE_TYPE,
          data: { order: node.order, layer: node.layoutColumn },
          style: {
            size: [node.width, node.height],
            dx: -node.width / 2,
            dy: -node.height / 2,
            ports,
            component: (
              <AgentSessionNodeView
                node={node}
                onOpenMessage={callbacks.openMessage}
                onToggleSession={callbacks.toggleSession}
                themeMode={themeMode}
              />
            ) as any,
          },
        };
      }
      return {
        id: node.id,
        type: REACT_NODE_TYPE,
        data: { order: node.order, layer: node.layoutColumn },
        style: {
          size: [node.width, node.height],
          dx: -node.width / 2,
          dy: -node.height / 2,
          ports: [
            {
              key: "input",
              placement: [0, 0.5] as [number, number],
              r: 4,
              fill: "#4f8cff",
              stroke: "#d6e4ff",
              lineWidth: 1,
            },
            ...(node.kind === "tool_call" ? [{
              key: "output",
              placement: [1, 0.5] as [number, number],
              r: 4,
              fill: "#9254de",
              stroke: "#efdbff",
              lineWidth: 1,
            }] : []),
          ],
          component: (
            <EventCanvasNodeView
              node={node}
              onOpen={callbacks.openEvent}
              themeMode={themeMode}
            />
          ) as any,
        },
      };
    }),
    edges: model.edges.map((edge) => ({
      id: edge.id,
      source: edge.source,
      target: edge.target,
      type: "polyline",
      data: { relationship: edge.relationship, fallback: edge.fallback },
      style: {
        sourcePort: edge.sourcePort ?? undefined,
        targetPort: edge.targetPort,
        stroke: EDGE_COLORS[edge.status],
        lineWidth: edge.status === "failed" ? 2.5 : 2,
        lineDash: edge.status === "running"
          ? [5, 4]
          : edge.status === "warning" ? [9, 5] : undefined,
        endArrow: true,
        radius: 10,
        router: { type: "orth" },
        labelText: edge.relationship === "nested_agent"
          ? edge.fallback ? "启动 Agent · 调用消息不可用" : "启动 Agent"
          : edge.fallback ? "调用消息不可用" : undefined,
        labelFill: EDGE_COLORS[edge.status],
        labelBackground: true,
        labelBackgroundFill: themeMode === "dark" ? "#131c2b" : "#ffffff",
        labelPadding: [3, 5],
      },
    })),
  };
}

function structuralSignature(model: CanvasModel): string {
  return JSON.stringify({
    nodes: model.nodes.map((node) => [
      node.id,
      node.width,
      node.height,
      node.order,
      node.layoutColumn,
    ]),
    edges: model.edges.map((edge) => [
      edge.id,
      edge.source,
      edge.target,
      edge.sourcePort,
      edge.targetPort,
    ]),
  });
}

/** Focus the newest node together with the message-owning parent when present. */
export function liveFocusIds(model: CanvasModel): string[] {
  if (!model.latestNodeId) return [];
  const sources = model.edges
    .filter((edge) => edge.target === model.latestNodeId)
    .map((edge) => edge.source);
  return [...new Set([...sources, model.latestNodeId])];
}

function sourceMessageY(graph: Graph, model: CanvasModel): number | null {
  if (!model.latestNodeId) return null;
  const edge = model.edges.find((candidate) => candidate.target === model.latestNodeId);
  if (!edge?.sourcePort || edge.sourcePort === "agent_header") return null;
  const source = model.nodes.find(
    (node): node is AgentSessionCanvasNode => node.id === edge.source && node.kind === "agent_session",
  );
  if (!source) return null;
  const messageIndex = source.messages.findIndex((message) => message.portKey === edge.sourcePort);
  if (messageIndex < 0) return null;
  const [, relativeY] = messagePortPlacement(source, messageIndex);
  const [, centerY] = graph.getElementPosition(source.id);
  return centerY - source.height / 2 + source.height * relativeY;
}

/** Keep the newest node and its calling message legible in the current viewport. */
async function focusLiveContext(graph: Graph, model: CanvasModel): Promise<void> {
  const focusIds = liveFocusIds(model);
  if (!focusIds.length) return;
  const bounds = focusIds.map((id) => graph.getElementRenderBounds(id));
  const minX = Math.min(...bounds.map((bound) => bound.min[0]));
  const maxX = Math.max(...bounds.map((bound) => bound.max[0]));
  const [canvasWidth, canvasHeight] = graph.getSize();
  const desiredZoom = Math.min(1, Math.max(0.35, (canvasWidth - 72) / Math.max(1, maxX - minX)));
  // Viewport animation promises can remain pending while G6 receives another
  // live layout. Immediate transforms keep the serialized render queue free
  // for SSE revisions, collapse changes, and run resets.
  const animation = false;
  if (Math.abs(graph.getZoom() - desiredZoom) > 0.02) {
    await graph.zoomTo(desiredZoom, animation);
  }
  await graph.focusElement(focusIds, animation);

  // G6 centers the full, potentially very tall Agent node. Re-center on the
  // actual calling message and newest target so a long session follows its
  // newest interaction instead of remaining anchored at the session midpoint.
  const latestPosition = model.latestNodeId
    ? graph.getElementPosition(model.latestNodeId)
    : null;
  const messageY = sourceMessageY(graph, model);
  const focusY = latestPosition
    ? messageY === null ? latestPosition[1] : (messageY + latestPosition[1]) / 2
    : messageY;
  if (focusY !== null) {
    const [, viewportY] = graph.getViewportByCanvas([0, focusY]);
    await graph.translateBy([0, canvasHeight / 2 - viewportY], animation);
  }
}

function reducedMotion(): boolean {
  return window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
}

function setExpandedKey(
  current: Set<string>,
  key: string,
  expanded: boolean,
): Set<string> {
  if (current.has(key) === expanded) return current;
  const next = new Set(current);
  if (expanded) next.add(key);
  else next.delete(key);
  return next;
}

/** Render and incrementally update the G6 graph without exposing write actions. */
export function EventCanvas({
  events,
  filters,
  latestCursor,
  runId,
  themeMode,
}: EventCanvasProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const graphRef = useRef<Graph | null>(null);
  const renderQueueRef = useRef<Promise<void>>(Promise.resolve());
  const generationRef = useRef(0);
  const detailMotionSequenceRef = useRef(0);
  const pendingDetailMotionRef = useRef<PendingDetailMotion | null>(null);
  const structureRef = useRef("");
  const followingRef = useRef(true);
  const [following, setFollowing] = useState(true);
  const [collapsedSessions, setCollapsedSessions] = useState<Set<string>>(() => new Set());
  const [expandedDetailIds, setExpandedDetailIds] = useState<Set<string>>(() => new Set());
  const [graphError, setGraphError] = useState<string | null>(null);

  const pauseFollowing = useCallback(() => {
    followingRef.current = false;
    setFollowing(false);
  }, []);

  const toggleMessageDetail = useCallback((messageId: string, expanded: boolean) => {
    pauseFollowing();
    setExpandedDetailIds((current) => {
      const key = messageDetailKey(messageId);
      if (current.has(key) === expanded) return current;
      pendingDetailMotionRef.current = {
        expanded,
        sequence: ++detailMotionSequenceRef.current,
      };
      return setExpandedKey(current, key, expanded);
    });
  }, [pauseFollowing]);

  const openMessage = useCallback((message: CanvasMessage, expanded: boolean) => {
    toggleMessageDetail(message.id, expanded);
  }, [toggleMessageDetail]);

  const toggleEventDetail = useCallback((eventId: string, expanded: boolean) => {
    pauseFollowing();
    setExpandedDetailIds((current) => {
      const key = eventDetailKey(eventId);
      if (current.has(key) === expanded) return current;
      pendingDetailMotionRef.current = {
        expanded,
        sequence: ++detailMotionSequenceRef.current,
      };
      return setExpandedKey(current, key, expanded);
    });
  }, [pauseFollowing]);

  const openEvent = useCallback((event: TimelineEvent, expanded: boolean) => {
    toggleEventDetail(event.event_id, expanded);
  }, [toggleEventDetail]);

  const toggleSession = useCallback((sessionId: string, collapsed: boolean) => {
    pauseFollowing();
    setCollapsedSessions((current) => {
      const next = new Set(current);
      if (collapsed) next.add(sessionId);
      else next.delete(sessionId);
      return next;
    });
  }, [pauseFollowing]);

  const callbacks = useMemo(
    () => ({ openMessage, openEvent, toggleSession }),
    [openEvent, openMessage, toggleSession],
  );
  const model = useMemo(
    () => buildCanvasModel(events, filters, collapsedSessions, expandedDetailIds),
    [collapsedSessions, events, expandedDetailIds, filters],
  );
  const graphData = useMemo(
    () => graphDataForModel(model, themeMode, callbacks),
    [callbacks, model, themeMode],
  );
  const signature = useMemo(() => structuralSignature(model), [model]);

  useEffect(() => {
    followingRef.current = true;
    setFollowing(true);
    setCollapsedSessions(new Set());
    setExpandedDetailIds(new Set());
    pendingDetailMotionRef.current = null;
    detailMotionSequenceRef.current += 1;
    structureRef.current = "";
  }, [runId]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return undefined;
    const graph = new Graph({
      animation: false,
      autoResize: true,
      behaviors: canvasNavigationBehaviors(),
      container,
      data: { nodes: [], edges: [] },
      edge: { type: "polyline", animation: false },
      node: { type: REACT_NODE_TYPE, animation: false },
      padding: 64,
      zoomRange: [0.1, 2],
    });
    graphRef.current = graph;
    const pause = () => pauseFollowing();
    const toggleFromHtmlNode = (event: MouseEvent) => {
      const nativeTarget = event.target instanceof Element
        ? event.target
        : event.target instanceof Node ? event.target.parentElement : null;
      if (!nativeTarget) return;
      const messageButton = nativeTarget.closest<HTMLElement>(".canvas-message-summary");
      if (messageButton?.dataset.messageId) {
        toggleMessageDetail(
          messageButton.dataset.messageId,
          messageButton.dataset.expanded !== "true",
        );
        return;
      }
      const eventButton = nativeTarget.closest<HTMLElement>(".canvas-event-summary-button");
      if (eventButton?.dataset.eventId) {
        toggleEventDetail(
          eventButton.dataset.eventId,
          eventButton.dataset.expanded !== "true",
        );
        return;
      }
      const header = nativeTarget.closest<HTMLElement>(".canvas-agent-header");
      if (header?.dataset.sessionId) {
        toggleSession(header.dataset.sessionId, header.dataset.collapsed !== "true");
      }
    };
    graph.on(CanvasEvent.DRAG_START, pause);
    graph.on(CanvasEvent.WHEEL, pause);
    // G6 React nodes are rendered as HTML descendants of the canvas host.
    // Native delegation keeps their read-only controls reliable at every zoom
    // level; the React handlers remain necessary for keyboard activation.
    container.addEventListener("click", toggleFromHtmlNode);
    return () => {
      generationRef.current += 1;
      graph.off(CanvasEvent.DRAG_START, pause);
      graph.off(CanvasEvent.WHEEL, pause);
      container.removeEventListener("click", toggleFromHtmlNode);
      graph.destroy();
      if (graphRef.current === graph) graphRef.current = null;
    };
  }, [pauseFollowing, toggleEventDetail, toggleMessageDetail, toggleSession]);

  useEffect(() => {
    const graph = graphRef.current;
    if (!graph || graph.destroyed) return undefined;
    const generation = ++generationRef.current;
    const structureChanged = structureRef.current !== signature;
    const requestedMotion = pendingDetailMotionRef.current;
    const frame = window.requestAnimationFrame(() => {
      renderQueueRef.current = renderQueueRef.current
        .catch(() => undefined)
        .then(async () => {
          if (graph.destroyed || generation !== generationRef.current) return;
          setGraphError(null);
          if (structureChanged) {
            const animateDetail = requestedMotion !== null && !reducedMotion();
            const positionedGraphData = await positionGraphDataByStableColumns(graphData);
            if (graph.destroyed || generation !== generationRef.current) return;
            const portKeys = model.nodes.flatMap((node) => node.kind === "agent_session"
              ? ["input", "agent_header", ...node.messages.map((message) => message.portKey)]
              : node.kind === "tool_call" ? ["input", "output"] : ["input"]);
            await renderStructuralGraphUpdate(
              graph,
              positionedGraphData,
              null,
              animateDetail && requestedMotion
                ? { expanded: requestedMotion.expanded, portKeys }
                : null,
            );
            if (graph.destroyed || generation !== generationRef.current) return;
            structureRef.current = signature;
            if (pendingDetailMotionRef.current?.sequence === requestedMotion?.sequence) {
              pendingDetailMotionRef.current = null;
            }
          } else {
            if (pendingDetailMotionRef.current?.sequence === requestedMotion?.sequence) {
              pendingDetailMotionRef.current = null;
            }
            graph.updateData(graphData);
            await graph.draw();
            if (graph.destroyed || generation !== generationRef.current) return;
          }
          if (followingRef.current && model.latestNodeId) {
            await focusLiveContext(graph, model);
          }
        })
        .catch((error: unknown) => {
          if (generation === generationRef.current) {
            setGraphError(error instanceof Error ? error.message : "画布渲染失败");
          }
        });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [graphData, latestCursor, model.latestNodeId, model.nodes, signature]);

  const zoom = useCallback((ratio: number) => {
    pauseFollowing();
    const graph = graphRef.current;
    if (graph && !graph.destroyed) void graph.zoomBy(ratio, reducedMotion() ? false : { duration: 160 });
  }, [pauseFollowing]);

  const fitAll = useCallback(() => {
    pauseFollowing();
    const graph = graphRef.current;
    if (graph && !graph.destroyed) {
      void graph.fitView(
        { when: "always", direction: "both" },
        reducedMotion() ? false : { duration: 220 },
      );
    }
  }, [pauseFollowing]);

  const returnToLive = useCallback(() => {
    followingRef.current = true;
    setFollowing(true);
    const graph = graphRef.current;
    if (graph && !graph.destroyed && model.latestNodeId) {
      void focusLiveContext(graph, model);
    }
  }, [model.latestNodeId]);

  return (
    <section
      className="event-canvas-region"
      aria-label="Agent 会话事件画布"
    >
      <div className="canvas-heading">
        <Flex align="center" gap={8} wrap>
          <DeploymentUnitOutlined />
          <Title level={5}>Agent 会话画布</Title>
          <Tag>{model.agentSessionCount} Agent 会话</Tag>
          <Tag>{model.toolNodeCount} Tool</Tag>
          <Tag>{model.batchNodeCount} Batch</Tag>
          <Tag>{model.matchCount} / {model.semanticEventCount} 匹配事件</Tag>
        </Flex>
        <Text type="secondary">拖动画布 · 双指滚动 · 捏合缩放 · 节点只读</Text>
      </div>
      <div className="canvas-stage">
        <div className="g6-container" ref={containerRef} />
        {!model.nodes.length && (
          <Empty
            className="canvas-empty"
            description={events.length ? "没有符合筛选条件的事件" : "等待 RESTScope 运行事件"}
          />
        )}
        {graphError && (
          <Alert
            className="canvas-error"
            description={graphError}
            showIcon
            title="画布暂时无法渲染"
            type="warning"
          />
        )}
        <Space.Compact className="canvas-controls">
          <Button aria-label="放大画布" icon={<PlusOutlined />} onClick={() => zoom(1.2)} />
          <Button aria-label="缩小画布" icon={<MinusOutlined />} onClick={() => zoom(0.8)} />
          <Button icon={<AimOutlined />} onClick={fitAll}>适应全部</Button>
        </Space.Compact>
        {!following && (
          <Button
            className="return-live"
            icon={<VerticalAlignBottomOutlined />}
            onClick={returnToLive}
            type="primary"
          >
            回到实时
          </Button>
        )}
      </div>
    </section>
  );
}
