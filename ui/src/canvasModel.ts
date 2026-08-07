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
  layoutColumn: number;
  width: number;
  height: number;
}

export interface EventCanvasNode {
  id: string;
  kind: "tool_call" | "smoke_batch";
  event: TimelineEvent;
  contextOnly: boolean;
  layoutColumn: number;
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
  callGroupKey: string;
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

function assistantPortForTurn(
  turn: TimelineEvent,
  toolCallId: string | null,
): string | null {
  const candidates = outputMessages(turn)
    .map((message, index) => ({ message, index }))
    .filter(({ message }) => message.role === "assistant");
  const exact = toolCallId
    ? candidates.find(({ message }) => messageToolCallIds(message).includes(toolCallId))
    : undefined;
  const selected = exact ?? candidates.at(-1);
  return selected ? portKey(`${turn.event_id}:output:${selected.index}`) : null;
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

function parentSessionId(event: TimelineEvent | undefined): string | null {
  const value = event?.agent?.parent_session_id;
  return typeof value === "string" && value ? value : null;
}

function sessionLineage(
  event: TimelineEvent,
  sessionParents: ReadonlyMap<string, string>,
): string[] {
  const lineage: string[] = [];
  const visited = new Set<string>();
  let sessionId = event.agent?.session_id ?? null;
  while (sessionId && !visited.has(sessionId)) {
    visited.add(sessionId);
    lineage.push(sessionId);
    sessionId = sessionParents.get(sessionId) ?? null;
  }
  return lineage;
}

interface RelationshipSeed extends CanvasEdgeModel {
  targetOrder: number;
}

function relationshipGroupKey(
  source: string,
  sourcePort: string,
  target: string,
  fallback: boolean,
): string {
  // Missing Assistant messages cannot safely be grouped together. Treat each
  // header fallback as its own call so unrelated inferred hops never share a
  // column merely because they use the same fallback port.
  return fallback && sourcePort === "agent_header"
    ? `${source}:fallback:${target}`
    : `${source}:${sourcePort}`;
}

/** Resolve every causal edge before filters hide any node.
 *
 * Building this complete relationship set is what keeps call-group columns
 * stable while the user searches, filters, or collapses a branch.
 */
function buildRelationshipSeeds(
  orderedEvents: TimelineEvent[],
  eventsById: ReadonlyMap<string, TimelineEvent>,
  turnsBySession: ReadonlyMap<string, TimelineEvent[]>,
): RelationshipSeed[] {
  const seeds = new Map<string, RelationshipSeed>();

  function add(seed: Omit<RelationshipSeed, "callGroupKey">) {
    if (seeds.has(seed.id)) return;
    seeds.set(seed.id, {
      ...seed,
      callGroupKey: relationshipGroupKey(
        seed.source,
        seed.sourcePort ?? "agent_header",
        seed.target,
        seed.fallback,
      ),
    });
  }

  for (const event of orderedEvents) {
    if (event.kind === "agent_turn") continue;
    const target = `event:${event.event_id}`;
    const parent = event.parent_event_id
      ? eventsById.get(event.parent_event_id)
      : undefined;
    if (parent?.kind === "agent_turn" && parent.agent?.session_id) {
      const source = `agent:${parent.agent.session_id}`;
      const sourcePort = assistantPortForTurn(
        parent,
        event.kind === "tool_call" ? eventToolCallId(event) : null,
      );
      add({
        id: `${event.kind}:${parent.event_id}:${target}`,
        source,
        target,
        sourcePort: sourcePort ?? "agent_header",
        targetPort: "input",
        relationship: event.kind === "tool_call" ? "tool_call" : "smoke_batch",
        status: event.status,
        fallback: sourcePort === null,
        targetOrder: event.order,
      });
    } else if (event.kind === "tool_call" && event.agent?.session_id) {
      const source = `agent:${event.agent.session_id}`;
      add({
        id: `tool_call:fallback:${target}`,
        source,
        target,
        sourcePort: "agent_header",
        targetPort: "input",
        relationship: "tool_call",
        status: event.status,
        fallback: true,
        targetOrder: event.order,
      });
    }
  }

  for (const [sessionId, turns] of turnsBySession) {
    const target = `agent:${sessionId}`;
    let hasExactParent = false;
    for (const turn of turns) {
      const parent = turn.parent_event_id
        ? eventsById.get(turn.parent_event_id)
        : undefined;
      if (
        parent?.kind === "agent_turn"
        && parent.agent?.session_id
        && parent.agent.session_id !== sessionId
      ) {
        const source = `agent:${parent.agent.session_id}`;
        const sourcePort = assistantPortForTurn(parent, null);
        add({
          id: `nested_agent:${parent.event_id}:${target}`,
          source,
          target,
          sourcePort: sourcePort ?? "agent_header",
          targetPort: "input",
          relationship: "nested_agent",
          status: aggregateStatus(turns),
          fallback: sourcePort === null,
          targetOrder: turns[0].order,
        });
        hasExactParent = true;
      } else if (parent?.kind === "tool_call") {
        add({
          id: `nested_agent:${parent.event_id}:${target}`,
          source: `event:${parent.event_id}`,
          target,
          sourcePort: "output",
          targetPort: "input",
          relationship: "nested_agent",
          status: aggregateStatus(turns),
          fallback: false,
          targetOrder: turns[0].order,
        });
        hasExactParent = true;
      }
    }
    if (!hasExactParent) {
      const parentId = parentSessionId(turns[0]);
      if (parentId && parentId !== sessionId) {
        add({
          id: `nested_agent:fallback:${parentId}:${target}`,
          source: `agent:${parentId}`,
          target,
          sourcePort: "agent_header",
          targetPort: "input",
          relationship: "nested_agent",
          status: aggregateStatus(turns),
          fallback: true,
          targetOrder: turns[0].order,
        });
      }
    }
  }

  return [...seeds.values()].sort(
    (left, right) => left.targetOrder - right.targetOrder || left.id.localeCompare(right.id),
  );
}

/** Assign stable left-to-right columns to complete, unfiltered relationships. */
function layoutColumns(
  orderedEvents: TimelineEvent[],
  turnsBySession: ReadonlyMap<string, TimelineEvent[]>,
  relationships: RelationshipSeed[],
): Map<string, number> {
  const columns = new Map<string, number>();
  for (const sessionId of turnsBySession.keys()) columns.set(`agent:${sessionId}`, 0);
  for (const event of orderedEvents) {
    if (event.kind !== "agent_turn") columns.set(`event:${event.event_id}`, 0);
  }

  const groupsBySource = new Map<string, Map<string, number>>();
  for (const edge of relationships) {
    const groups = groupsBySource.get(edge.source) ?? new Map<string, number>();
    const currentOrder = groups.get(edge.callGroupKey);
    groups.set(
      edge.callGroupKey,
      currentOrder === undefined ? edge.targetOrder : Math.min(currentOrder, edge.targetOrder),
    );
    groupsBySource.set(edge.source, groups);
  }
  const groupOffsets = new Map<string, number>();
  for (const groups of groupsBySource.values()) {
    [...groups.entries()]
      .sort((left, right) => left[1] - right[1] || left[0].localeCompare(right[0]))
      .forEach(([groupKey], index) => groupOffsets.set(groupKey, index + 1));
  }

  // Event order is already topological in normal runs. Repeating bounded
  // relaxation also covers a revised event whose parent arrives one frame
  // later without allowing a malformed cycle to loop forever.
  for (let pass = 0; pass < Math.max(1, columns.size); pass += 1) {
    let changed = false;
    for (const edge of relationships) {
      const sourceColumn = columns.get(edge.source) ?? 0;
      const targetColumn = columns.get(edge.target) ?? 0;
      const candidate = sourceColumn + (groupOffsets.get(edge.callGroupKey) ?? 1);
      if (candidate > targetColumn) {
        columns.set(edge.target, candidate);
        changed = true;
      }
    }
    if (!changed) break;
  }
  return columns;
}

function descendantCount(
  sessionId: string,
  events: TimelineEvent[],
  eventsById: Map<string, TimelineEvent>,
  sessionParents: ReadonlyMap<string, string>,
): number {
  return events.filter((event) => {
    if (event.kind === "agent_turn" && event.agent?.session_id === sessionId) return false;
    return sessionLineage(event, sessionParents).includes(sessionId)
      || parentChain(event, eventsById).some(
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
  const turnsBySession = new Map<string, TimelineEvent[]>();
  for (const event of orderedEvents) {
    if (event.kind !== "agent_turn" || !event.agent?.session_id) continue;
    const sessionTurns = turnsBySession.get(event.agent.session_id) ?? [];
    sessionTurns.push(event);
    turnsBySession.set(event.agent.session_id, sessionTurns);
  }
  const sessionParents = new Map<string, string>();
  for (const [sessionId, turns] of turnsBySession) {
    const parentId = turns.map(parentSessionId).find((value) => value !== null);
    if (parentId) sessionParents.set(sessionId, parentId);
  }
  const relationshipSeeds = buildRelationshipSeeds(
    orderedEvents,
    eventsById,
    turnsBySession,
  );
  const stableColumns = layoutColumns(orderedEvents, turnsBySession, relationshipSeeds);
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
      // A direct nested Agent can have no visible parent event. Its additive
      // session ancestry still keeps the parent Agent visible as search context.
      for (const sessionId of sessionLineage(event, sessionParents)) {
        forcedSessionIds.add(sessionId);
        for (const turn of turnsBySession.get(sessionId) ?? []) {
          visibleEventIds.add(turn.event_id);
        }
      }
    }
  }

  const collapsedEffective = new Set(
    [...collapsedSessions].filter((sessionId) => !forcedSessionIds.has(sessionId)),
  );
  const hiddenEventIds = new Set<string>();
  for (const event of orderedEvents) {
    if (!visibleEventIds.has(event.event_id)) continue;
    const ownAgentTurnSession = event.kind === "agent_turn"
      ? event.agent?.session_id ?? null
      : null;
    const hiddenByLineage = sessionLineage(event, sessionParents).some(
      (sessionId) => collapsedEffective.has(sessionId) && sessionId !== ownAgentTurnSession,
    );
    const hiddenByVisibleParent = parentChain(event, eventsById).some((ancestor) => {
      const ancestorSession = sessionIdForTurn(ancestor);
      return ancestorSession !== null && collapsedEffective.has(ancestorSession);
    });
    const hiddenBySession = hiddenByLineage || hiddenByVisibleParent;
    if (hiddenBySession) hiddenEventIds.add(event.event_id);
  }

  const nodes: CanvasNodeModel[] = [];
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
      hiddenDescendantCount: collapsed
        ? descendantCount(sessionId, orderedEvents, eventsById, sessionParents)
        : 0,
      contextOnly: !turns.some((turn) => matchedEventIds.has(turn.event_id)),
      layoutColumn: stableColumns.get(`agent:${sessionId}`) ?? 0,
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
      layoutColumn: stableColumns.get(`event:${event.event_id}`) ?? 0,
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
  const edges: CanvasEdgeModel[] = relationshipSeeds
    .filter((edge) => visibleNodeIds.has(edge.source) && visibleNodeIds.has(edge.target))
    .map((edge) => ({
      id: edge.id,
      source: edge.source,
      target: edge.target,
      sourcePort: edge.sourcePort,
      targetPort: edge.targetPort,
      relationship: edge.relationship,
      status: edge.status,
      fallback: edge.fallback,
      callGroupKey: edge.callGroupKey,
    }));

  // Highlight the exact Assistant message that owns an outgoing visible edge.
  // Tool-origin and header-fallback edges intentionally have no message marker.
  for (const edge of edges) {
    if (edge.sourcePort === "agent_header" || edge.sourcePort === "output") continue;
    const sourceNode = nodes.find(
      (node): node is AgentSessionCanvasNode => (
        node.id === edge.source && node.kind === "agent_session"
      ),
    );
    const message = sourceNode?.messages.find(
      (candidate) => candidate.portKey === edge.sourcePort,
    );
    if (message) message.connectionContext = true;
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
