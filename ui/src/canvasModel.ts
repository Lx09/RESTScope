/** Build the read-only Agent-session graph shown by the live observer canvas.
 *
 * The backend deliberately emits one semantic event per Agent turn, Tool
 * execution, or Smoke Batch. This module keeps that wire contract unchanged
 * while folding turns from the same runtime Agent session into one visual
 * node. It also resolves each Tool edge back to the Assistant message that
 * requested the call, so the canvas expresses intent and execution separately.
 */

import { eventMatches, toolFamily } from "./presentation";
import type { EventStatus, TimelineEvent, TimelineFilters } from "./types";

type UnknownRecord = Record<string, any>;

export const AGENT_NODE_WIDTH = 440;
export const AGENT_HEADER_HEIGHT = 92;
export const AGENT_MESSAGE_HEIGHT = 104;
export const AGENT_MESSAGE_HEADER_HEIGHT = 32;
export const AGENT_MESSAGE_COLLAPSED_CONTENT_HEIGHT = 38;
export const AGENT_MESSAGE_GAP = 8;
export const AGENT_NODE_PADDING = 12;
export const COLLAPSED_AGENT_HEIGHT = 156;
export const EVENT_NODE_WIDTH = 328;
export const EVENT_NODE_HEIGHT = 164;
export const INLINE_AGENT_DETAIL_HEIGHT = 440;
export const EVENT_COLLAPSED_CONTENT_HEIGHT = 52;
export const INLINE_EVENT_DETAIL_HEIGHT = 520;
export const MESSAGE_PREVIEW_LIMIT = 160;

export interface CanvasMessage {
  id: string;
  portKey: string;
  turnEventId: string;
  turnNumber: number;
  direction: "input" | "output";
  role: string;
  message: UnknownRecord;
  preview: string;
  toolCallId: string | null;
  toolCallIds: string[];
  exactMatch: boolean;
  connectionContext: boolean;
  expanded: boolean;
}

export interface AgentSessionCanvasNode {
  id: string;
  kind: "agent_session";
  sessionId: string;
  name: string;
  path: string[];
  operationKey: string | null;
  roundNumber: number | null;
  status: EventStatus;
  order: number;
  latestOrder: number;
  startedAt: string;
  endedAt: string | null;
  durationMs: number | null;
  turns: TimelineEvent[];
  messages: CanvasMessage[];
  collapsed: boolean;
  hiddenDescendantCount: number;
  contextOnly: boolean;
  width: number;
  height: number;
}

export interface EventCanvasNode {
  id: string;
  kind: "tool_call" | "smoke_batch";
  event: TimelineEvent;
  contextOnly: boolean;
  width: number;
  height: number;
  order: number;
  latestOrder: number;
  expanded: boolean;
  collapsedContentHeight: number;
}

export type CanvasNodeModel = AgentSessionCanvasNode | EventCanvasNode;

export interface CanvasEdgeModel {
  id: string;
  source: string;
  target: string;
  sourcePort: string | null;
  targetPort: string;
  relationship: "tool_call" | "nested_agent" | "smoke_batch";
  status: EventStatus;
  fallback: boolean;
}

export interface CanvasModel {
  nodes: CanvasNodeModel[];
  edges: CanvasEdgeModel[];
  matchCount: number;
  semanticEventCount: number;
  agentSessionCount: number;
  toolNodeCount: number;
  batchNodeCount: number;
  latestNodeId: string | null;
  matchedEventIds: Set<string>;
}

function filtersAreActive(filters: TimelineFilters): boolean {
  return Boolean(
    filters.search.trim()
      || filters.agents.length
      || filters.kinds.length
      || filters.toolFamilies.length
      || filters.statuses.length,
  );
}

function asRecord(value: unknown): UnknownRecord | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as UnknownRecord
    : null;
}

function messageList(value: unknown): UnknownRecord[] {
  return Array.isArray(value)
    ? value.filter((item): item is UnknownRecord => asRecord(item) !== null)
    : [];
}

function outputMessages(turn: TimelineEvent): UnknownRecord[] {
  const output = asRecord(turn.detail.output);
  if (!output) return [];
  const explicit = messageList(output.messages);
  if (explicit.length) return explicit;

  // Early schema-v2 snapshots may finish the output fields before the exact
  // provider message array arrives. A temporary Assistant card keeps the turn
  // visible and is replaced in place once the event revision contains it.
  if (output.content !== undefined || Array.isArray(output.tool_calls)) {
    return [{
      role: "assistant",
      content: output.content ?? null,
      tool_calls: Array.isArray(output.tool_calls) ? output.tool_calls : [],
    }];
  }
  return [];
}

/** Build the single-line, Unicode-safe text shown while a message is collapsed. */
export function compactMessagePreview(value: unknown): string {
  let preview: string;
  if (typeof value === "string") {
    preview = value.replace(/\s+/g, " ").trim();
  } else if (value === null || value === undefined) {
    preview = "";
  } else {
    try {
      const serialized = JSON.stringify(value);
      preview = typeof serialized === "string"
        ? serialized.replace(/\s+/g, " ")
        : String(value);
    } catch {
      preview = String(value);
    }
  }
  if (!preview) return "（空消息）";

  // Array.from counts Unicode code points instead of UTF-16 code units, so
  // emoji and other supplementary characters are never cut in half.
  const characters = Array.from(preview);
  return characters.length > MESSAGE_PREVIEW_LIMIT
    ? `${characters.slice(0, MESSAGE_PREVIEW_LIMIT).join("")}…`
    : preview;
}

function messageToolCallIds(message: UnknownRecord): string[] {
  if (!Array.isArray(message.tool_calls)) return [];
  return message.tool_calls
    .map((call) => asRecord(call)?.id)
    .filter((value): value is string => typeof value === "string" && Boolean(value));
}

function portKey(messageId: string): string {
  return `message_${messageId.replace(/[^a-zA-Z0-9_-]/g, "_")}`;
}

function messageMatchesSearch(message: UnknownRecord, search: string): boolean {
  if (!search) return true;
  try {
    return JSON.stringify(message).toLocaleLowerCase().includes(search);
  } catch {
    return String(message).toLocaleLowerCase().includes(search);
  }
}

function buildMessages(
  turns: TimelineEvent[],
  matchedEventIds: Set<string>,
  filters: TimelineFilters,
  expandedDetailIds: ReadonlySet<string>,
): CanvasMessage[] {
  const search = filters.search.trim().toLocaleLowerCase();
  const messages: CanvasMessage[] = [];
  turns.forEach((turn, turnIndex) => {
    const input = asRecord(turn.detail.input);
    const groups: Array<{ direction: "input" | "output"; values: UnknownRecord[] }> = [
      { direction: "input", values: messageList(input?.messages) },
      { direction: "output", values: outputMessages(turn) },
    ];
    groups.forEach(({ direction, values }) => {
      values.forEach((message, index) => {
        const id = `${turn.event_id}:${direction}:${index}`;
        const role = typeof message.role === "string"
          ? message.role
          : direction === "output" ? "assistant" : "user";
        messages.push({
          id,
          portKey: portKey(id),
          turnEventId: turn.event_id,
          turnNumber: turnIndex + 1,
          direction,
          role,
          message,
          preview: compactMessagePreview(message.content),
          toolCallId: typeof message.tool_call_id === "string" ? message.tool_call_id : null,
          toolCallIds: messageToolCallIds(message),
          exactMatch: Boolean(search)
            && matchedEventIds.has(turn.event_id)
            && messageMatchesSearch(message, search),
          connectionContext: false,
          expanded: expandedDetailIds.has(messageDetailKey(id)),
        });
      });
    });
  });
  return messages;
}

/** Build the viewer-only key used to expand one Agent message in place. */
export function messageDetailKey(messageId: string): string {
  return `message:${messageId}`;
}

/** Build the viewer-only key used to expand one Tool or Batch in place. */
export function eventDetailKey(eventId: string): string {
  return `event:${eventId}`;
}

/** Reserve compact content only when it carries information beyond Tool I/O.
 *
 * Ordinary Tool arguments and results belong exclusively to the expanded
 * detail. HTTP method/status/URL and Smoke Batch success counts remain useful
 * at a glance, so those two node shapes keep their existing summary height.
 */
export function eventCollapsedContentHeight(event: TimelineEvent): number {
  return event.kind === "smoke_batch" || toolFamily(event) === "http"
    ? EVENT_COLLAPSED_CONTENT_HEIGHT
    : 0;
}

function aggregateStatus(turns: TimelineEvent[]): EventStatus {
  if (turns.some((turn) => turn.status === "running")) return "running";
  if (turns.some((turn) => turn.status === "failed")) return "failed";
  if (turns.some((turn) => turn.status === "warning")) return "warning";
  return "succeeded";
}

function eventToolCallId(event: TimelineEvent): string | null {
  const output = asRecord(event.detail.output);
  const toolResult = asRecord(output?.tool_result);
  for (const value of [output?.tool_call_id, toolResult?.tool_call_id]) {
    if (typeof value === "string" && value) return value;
  }
  return null;
}

function assistantForTurn(
  node: AgentSessionCanvasNode,
  turnEventId: string,
  toolCallId: string | null,
): CanvasMessage | null {
  const candidates = node.messages.filter(
    (message) => message.turnEventId === turnEventId
      && message.direction === "output"
      && message.role === "assistant",
  );
  if (toolCallId) {
    const exact = candidates.find((message) => message.toolCallIds.includes(toolCallId));
    if (exact) return exact;
  }
  return candidates.at(-1) ?? null;
}

function parentChain(
  event: TimelineEvent,
  eventsById: Map<string, TimelineEvent>,
): TimelineEvent[] {
  const chain: TimelineEvent[] = [];
  const visited = new Set<string>([event.event_id]);
  let parentId = event.parent_event_id;
  while (parentId && !visited.has(parentId)) {
    visited.add(parentId);
    const parent = eventsById.get(parentId);
    if (!parent) break;
    chain.push(parent);
    parentId = parent.parent_event_id;
  }
  return chain;
}

function sessionIdForTurn(event: TimelineEvent | undefined): string | null {
  return event?.kind === "agent_turn" && event.agent?.session_id
    ? event.agent.session_id
    : null;
}

function descendantCount(
  sessionId: string,
  events: TimelineEvent[],
  eventsById: Map<string, TimelineEvent>,
): number {
  return events.filter((event) => {
    if (event.kind === "agent_turn" && event.agent?.session_id === sessionId) return false;
    return parentChain(event, eventsById).some(
      (ancestor) => ancestor.kind === "agent_turn" && ancestor.agent?.session_id === sessionId,
    );
  }).length;
}

/** Convert schema-v2 semantic events into the visual session graph.
 *
 * `collapsedSessions` contains viewer-only session IDs. Filters temporarily
 * override that state when a hidden descendant is an exact match, ensuring a
 * search result can never disappear inside a collapsed Agent branch.
 */
export function buildCanvasModel(
  events: TimelineEvent[],
  filters: TimelineFilters,
  collapsedSessions: ReadonlySet<string>,
  expandedDetailIds: ReadonlySet<string> = new Set(),
): CanvasModel {
  const orderedEvents = [...events].sort(
    (left, right) => left.order - right.order || left.started_at.localeCompare(right.started_at),
  );
  const eventsById = new Map(orderedEvents.map((event) => [event.event_id, event]));
  const activeFilters = filtersAreActive(filters);
  const matchedEventIds = new Set(
    orderedEvents
      .filter((event) => !activeFilters || eventMatches(event, filters))
      .map((event) => event.event_id),
  );
  const visibleEventIds = new Set(matchedEventIds);
  const forcedSessionIds = new Set<string>();

  // Only an active search/filter may temporarily override a manual collapse.
  // With the default all-events view, treating every event as a "match" would
  // otherwise make Agent sessions impossible to collapse.
  if (activeFilters) {
    for (const eventId of matchedEventIds) {
      const event = eventsById.get(eventId);
      if (!event) continue;
      for (const candidate of [event, ...parentChain(event, eventsById)]) {
        visibleEventIds.add(candidate.event_id);
        const sessionId = sessionIdForTurn(candidate);
        if (sessionId) forcedSessionIds.add(sessionId);
      }
    }
  }

  const collapsedEffective = new Set(
    [...collapsedSessions].filter((sessionId) => !forcedSessionIds.has(sessionId)),
  );
  const hiddenEventIds = new Set<string>();
  for (const event of orderedEvents) {
    if (!visibleEventIds.has(event.event_id)) continue;
    const hiddenBySession = parentChain(event, eventsById).some((ancestor) => {
      const ancestorSession = sessionIdForTurn(ancestor);
      return ancestorSession !== null
        && collapsedEffective.has(ancestorSession)
        && !(event.kind === "agent_turn" && event.agent?.session_id === ancestorSession);
    });
    if (hiddenBySession) hiddenEventIds.add(event.event_id);
  }

  const turnsBySession = new Map<string, TimelineEvent[]>();
  for (const event of orderedEvents) {
    if (event.kind !== "agent_turn" || !event.agent?.session_id) continue;
    const sessionTurns = turnsBySession.get(event.agent.session_id) ?? [];
    sessionTurns.push(event);
    turnsBySession.set(event.agent.session_id, sessionTurns);
  }

  const nodes: CanvasNodeModel[] = [];
  const sessionNodes = new Map<string, AgentSessionCanvasNode>();
  for (const [sessionId, turns] of turnsBySession) {
    const visibleTurns = turns.filter(
      (turn) => visibleEventIds.has(turn.event_id) && !hiddenEventIds.has(turn.event_id),
    );
    if (!visibleTurns.length) continue;
    const messages = buildMessages(turns, matchedEventIds, filters, expandedDetailIds);
    const collapsed = collapsedEffective.has(sessionId);
    const first = turns[0];
    const last = turns.at(-1) ?? first;
    const startedAt = first.started_at;
    const endedAt = turns.some((turn) => turn.ended_at === null) ? null : last.ended_at;
    const durationMs = endedAt
      ? Math.max(0, new Date(endedAt).getTime() - new Date(startedAt).getTime())
      : null;
    const node: AgentSessionCanvasNode = {
      id: `agent:${sessionId}`,
      kind: "agent_session",
      sessionId,
      name: first.agent?.name ?? first.name,
      path: first.agent?.path ?? [first.name],
      operationKey: first.operation_key,
      roundNumber: first.round_number,
      status: aggregateStatus(turns),
      order: first.order,
      latestOrder: last.order,
      startedAt,
      endedAt,
      durationMs,
      turns,
      messages,
      collapsed,
      hiddenDescendantCount: collapsed ? descendantCount(sessionId, orderedEvents, eventsById) : 0,
      contextOnly: !turns.some((turn) => matchedEventIds.has(turn.event_id)),
      width: AGENT_NODE_WIDTH,
      height: collapsed
        ? COLLAPSED_AGENT_HEIGHT
        : AGENT_HEADER_HEIGHT
          + AGENT_NODE_PADDING * 2
          + messages.reduce(
            (height, message) => height
              + AGENT_MESSAGE_HEIGHT
              + (message.expanded
                ? INLINE_AGENT_DETAIL_HEIGHT - AGENT_MESSAGE_COLLAPSED_CONTENT_HEIGHT
                : 0),
            0,
          )
          + Math.max(0, messages.length - 1) * AGENT_MESSAGE_GAP
    };
    nodes.push(node);
    sessionNodes.set(sessionId, node);
  }

  for (const event of orderedEvents) {
    if (event.kind === "agent_turn") continue;
    if (!visibleEventIds.has(event.event_id) || hiddenEventIds.has(event.event_id)) continue;
    const collapsedContentHeight = eventCollapsedContentHeight(event);
    nodes.push({
      id: `event:${event.event_id}`,
      kind: event.kind,
      event,
      contextOnly: !matchedEventIds.has(event.event_id),
      width: EVENT_NODE_WIDTH,
      order: event.order,
      latestOrder: event.order,
      expanded: expandedDetailIds.has(eventDetailKey(event.event_id)),
      collapsedContentHeight,
      height: EVENT_NODE_HEIGHT - EVENT_COLLAPSED_CONTENT_HEIGHT + collapsedContentHeight
        + (expandedDetailIds.has(eventDetailKey(event.event_id))
          ? INLINE_EVENT_DETAIL_HEIGHT - collapsedContentHeight
          : 0),
    });
  }

  nodes.sort((left, right) => left.order - right.order || left.id.localeCompare(right.id));
  const visibleNodeIds = new Set(nodes.map((node) => node.id));
  const edges: CanvasEdgeModel[] = [];
  const edgeIds = new Set<string>();

  function addAgentEdge(
    sourceTurn: TimelineEvent,
    target: string,
    relationship: CanvasEdgeModel["relationship"],
    status: EventStatus,
    toolCallId: string | null = null,
  ) {
    const sessionId = sessionIdForTurn(sourceTurn);
    const sourceNode = sessionId ? sessionNodes.get(sessionId) : undefined;
    if (!sourceNode || !visibleNodeIds.has(target)) return;
    const message = assistantForTurn(sourceNode, sourceTurn.event_id, toolCallId);
    if (message) message.connectionContext = true;
    const id = `${relationship}:${sourceTurn.event_id}:${target}`;
    if (edgeIds.has(id)) return;
    edgeIds.add(id);
    edges.push({
      id,
      source: sourceNode.id,
      target,
      sourcePort: message?.portKey ?? "agent_header",
      targetPort: "input",
      relationship,
      status,
      fallback: message === null,
    });
  }

  for (const node of nodes) {
    if (node.kind === "tool_call") {
      const parent = node.event.parent_event_id
        ? eventsById.get(node.event.parent_event_id)
        : undefined;
      if (parent?.kind === "agent_turn") {
        addAgentEdge(
          parent,
          node.id,
          "tool_call",
          node.event.status,
          eventToolCallId(node.event),
        );
      } else if (node.event.agent?.session_id) {
        const sourceNode = sessionNodes.get(node.event.agent.session_id);
        if (sourceNode) {
          const id = `tool_call:fallback:${node.id}`;
          edgeIds.add(id);
          edges.push({
            id,
            source: sourceNode.id,
            target: node.id,
            sourcePort: "agent_header",
            targetPort: "input",
            relationship: "tool_call",
            status: node.event.status,
            fallback: true,
          });
        }
      }
      continue;
    }
    if (node.kind === "smoke_batch" && node.event.parent_event_id) {
      const parent = eventsById.get(node.event.parent_event_id);
      if (parent?.kind === "agent_turn") {
        addAgentEdge(parent, node.id, "smoke_batch", node.event.status);
      }
    }
  }

  // A nested Agent's turn can point at a turn from its owning Agent. Aggregate
  // the child turns first, then retain one edge per real external parent.
  for (const node of nodes) {
    if (node.kind !== "agent_session") continue;
    for (const turn of node.turns) {
      if (!turn.parent_event_id) continue;
      const parent = eventsById.get(turn.parent_event_id);
      if (
        parent?.kind === "agent_turn"
        && parent.agent?.session_id !== node.sessionId
      ) {
        addAgentEdge(parent, node.id, "nested_agent", node.status);
      }
    }
  }

  edges.sort((left, right) => {
    const leftOrder = nodes.find((node) => node.id === left.target)?.order ?? 0;
    const rightOrder = nodes.find((node) => node.id === right.target)?.order ?? 0;
    return leftOrder - rightOrder || left.id.localeCompare(right.id);
  });
  const latestNode = [...nodes].sort(
    (left, right) => right.latestOrder - left.latestOrder,
  )[0];

  return {
    nodes,
    edges,
    matchCount: matchedEventIds.size,
    semanticEventCount: orderedEvents.length,
    agentSessionCount: nodes.filter((node) => node.kind === "agent_session").length,
    toolNodeCount: nodes.filter((node) => node.kind === "tool_call").length,
    batchNodeCount: nodes.filter((node) => node.kind === "smoke_batch").length,
    latestNodeId: latestNode?.id ?? null,
    matchedEventIds,
  };
}

/** Return the vertical port position for one message inside an expanded Agent. */
export function messagePortPlacement(
  node: AgentSessionCanvasNode,
  messageIndex: number,
): [number, number] {
  const expandedBefore = node.messages
    .slice(0, messageIndex)
    .filter((message) => message.expanded).length;
  const messageCenter = AGENT_HEADER_HEIGHT
    + AGENT_NODE_PADDING
    + messageIndex * (AGENT_MESSAGE_HEIGHT + AGENT_MESSAGE_GAP)
    + expandedBefore
      * (INLINE_AGENT_DETAIL_HEIGHT - AGENT_MESSAGE_COLLAPSED_CONTENT_HEIGHT)
    + AGENT_MESSAGE_HEADER_HEIGHT / 2;
  return [1, Math.min(1, Math.max(0, messageCenter / node.height))];
}
