/** Place observer cards without an asynchronous graph-layout engine.
 *
 * The canvas model supplies a complete, collapsed-detail graph plus the
 * currently visible card sizes. This module first assigns permanent columns
 * and top positions from the complete graph. It then overlays expanded sizes
 * and moves only later groups downward when they would overlap. The result is
 * synchronous and deterministic, so an older SSE render can never replace a
 * newer layout calculation.
 */

import {
  AGENT_HEADER_HEIGHT,
  AGENT_MESSAGE_GAP,
  AGENT_MESSAGE_HEIGHT,
  AGENT_NODE_PADDING,
  INLINE_AGENT_DETAIL_HEIGHT,
  AGENT_MESSAGE_COLLAPSED_CONTENT_HEIGHT,
  type AgentSessionCanvasNode,
  type CanvasEdgeModel,
  type CanvasModel,
  type CanvasNodeModel,
} from "./canvasModel";

export const CALL_GROUP_GAP = 16;
export const ROOT_GAP = 48;
export const COLUMN_GAP = 64;
export const CONNECTION_OFFSET_Y = 16;
export const BRANCH_OFFSET_X = 20;
export const BRANCH_RADIUS = 10;

export interface CanvasNodePosition {
  id: string;
  column: number;
  left: number;
  top: number;
  width: number;
  height: number;
  x: number;
  y: number;
}

export interface CanvasConnectionGroup {
  id: string;
  source: string;
  sourcePort: string | null;
  sourceTop: number;
  sourceY: number;
  targets: string[];
  edges: CanvasEdgeModel[];
}

export interface CanvasLayoutResult {
  positions: Map<string, CanvasNodePosition>;
  connectionGroups: CanvasConnectionGroup[];
}

interface CallGroup {
  id: string;
  source: string;
  sourcePort: string | null;
  edges: CanvasEdgeModel[];
  order: number;
}

interface BasePlacement {
  column: number;
  top: number;
}

function groupRelationships(
  edges: readonly CanvasEdgeModel[],
  nodes: ReadonlyMap<string, CanvasNodeModel>,
): CallGroup[] {
  const grouped = new Map<string, CallGroup>();
  for (const edge of edges) {
    const targetOrder = nodes.get(edge.target)?.order ?? Number.MAX_SAFE_INTEGER;
    const current = grouped.get(edge.callGroupKey);
    if (current) {
      current.edges.push(edge);
      current.order = Math.min(current.order, targetOrder);
    } else {
      grouped.set(edge.callGroupKey, {
        id: edge.callGroupKey,
        source: edge.source,
        sourcePort: edge.sourcePort,
        edges: [edge],
        order: targetOrder,
      });
    }
  }
  for (const group of grouped.values()) {
    group.edges.sort((left, right) => (
      (nodes.get(left.target)?.order ?? 0) - (nodes.get(right.target)?.order ?? 0)
      || left.id.localeCompare(right.id)
    ));
  }
  return [...grouped.values()].sort(
    (left, right) => left.order - right.order || left.id.localeCompare(right.id),
  );
}

function messageCardTop(
  node: AgentSessionCanvasNode,
  nodeTop: number,
  sourcePort: string | null,
): number {
  if (!sourcePort || sourcePort === "agent_header" || node.collapsed) return nodeTop;
  const index = node.messages.findIndex((message) => message.portKey === sourcePort);
  if (index < 0) return nodeTop;
  const expandedBefore = node.messages
    .slice(0, index)
    .filter((message) => message.expanded).length;
  return nodeTop
    + AGENT_HEADER_HEIGHT
    + AGENT_NODE_PADDING
    + index * (AGENT_MESSAGE_HEIGHT + AGENT_MESSAGE_GAP)
    + expandedBefore
      * (INLINE_AGENT_DETAIL_HEIGHT - AGENT_MESSAGE_COLLAPSED_CONTENT_HEIGHT);
}

function sourceCardTop(
  node: CanvasNodeModel,
  nodeTop: number,
  sourcePort: string | null,
): number {
  return node.kind === "agent_session"
    ? messageCardTop(node, nodeTop, sourcePort)
    : nodeTop;
}

function groupHeight(group: CallGroup, nodes: ReadonlyMap<string, CanvasNodeModel>): number {
  return group.edges.reduce((height, edge, index) => (
    height + (nodes.get(edge.target)?.height ?? 0) + (index ? CALL_GROUP_GAP : 0)
  ), 0);
}

/** Assign permanent columns and collapsed-detail top positions.
 *
 * A column can be reused only when its last group ends at least 16px above the
 * new parent card. If not, the entire call group moves right together. No
 * existing group is moved when a later event arrives.
 */
function placeBaseGraph(
  nodes: readonly CanvasNodeModel[],
  edges: readonly CanvasEdgeModel[],
): { placements: Map<string, BasePlacement>; groups: CallGroup[] } {
  const byId = new Map(nodes.map((node) => [node.id, node]));
  const groups = groupRelationships(edges, byId);
  const incoming = new Set(edges.map((edge) => edge.target));
  const placements = new Map<string, BasePlacement>();
  let rootBottom: number | null = null;

  for (const root of [...nodes]
    .filter((node) => !incoming.has(node.id))
    .sort((left, right) => left.order - right.order || left.id.localeCompare(right.id))) {
    const top: number = rootBottom === null ? 0 : rootBottom + ROOT_GAP;
    placements.set(root.id, { column: 0, top });
    rootBottom = top + root.height;
  }

  const columnBottom = new Map<number, number>();
  const pending = [...groups];
  // Normal events are topological. Bounded passes also tolerate a child that
  // arrives one reducer frame before its source without risking an endless
  // loop if malformed data contains a cycle.
  for (let pass = 0; pending.length && pass <= nodes.length; pass += 1) {
    let progressed = false;
    for (let index = 0; index < pending.length;) {
      const group = pending[index];
      const source = byId.get(group.source);
      const sourcePlacement = placements.get(group.source);
      if (!source || !sourcePlacement) {
        index += 1;
        continue;
      }
      const parentTop = sourceCardTop(source, sourcePlacement.top, group.sourcePort);
      let column = sourcePlacement.column + 1;
      while ((columnBottom.get(column) ?? -Infinity) + CALL_GROUP_GAP > parentTop) {
        column += 1;
      }
      let top = parentTop;
      for (const edge of group.edges) {
        const target = byId.get(edge.target);
        if (!target) continue;
        placements.set(target.id, { column, top });
        top += target.height + CALL_GROUP_GAP;
      }
      columnBottom.set(column, parentTop + groupHeight(group, byId));
      pending.splice(index, 1);
      progressed = true;
    }
    if (!progressed) break;
  }

  // Orphaned or cyclic nodes remain inspectable as late root cards instead of
  // disappearing from the canvas because their relationship was incomplete.
  for (const node of [...nodes]
    .filter((candidate) => !placements.has(candidate.id))
    .sort((left, right) => left.order - right.order || left.id.localeCompare(right.id))) {
    const top = rootBottom === null ? 0 : rootBottom + ROOT_GAP;
    placements.set(node.id, { column: 0, top });
    rootBottom = top + node.height;
  }
  return { placements, groups };
}

function columnLefts(
  nodes: readonly CanvasNodeModel[],
  placements: ReadonlyMap<string, BasePlacement>,
): Map<number, number> {
  const widths = new Map<number, number>();
  for (const node of nodes) {
    const column = placements.get(node.id)?.column ?? 0;
    widths.set(column, Math.max(widths.get(column) ?? 0, node.width));
  }
  const lastColumn = Math.max(0, ...widths.keys());
  const lefts = new Map<number, number>([[0, 0]]);
  for (let column = 1; column <= lastColumn; column += 1) {
    lefts.set(
      column,
      (lefts.get(column - 1) ?? 0) + (widths.get(column - 1) ?? 0) + COLUMN_GAP,
    );
  }
  return lefts;
}

/** Calculate current positions and shared connection groups for one frame. */
export function layoutCanvasModel(model: CanvasModel): CanvasLayoutResult {
  const basisById = new Map(model.layoutNodes.map((node) => [node.id, node]));
  const visibleById = new Map(model.nodes.map((node) => [node.id, node]));
  const currentById = new Map(model.layoutNodes.map((node) => [
    node.id,
    visibleById.get(node.id) ?? node,
  ]));
  const { placements: base, groups } = placeBaseGraph(model.layoutNodes, model.layoutEdges);
  const lefts = columnLefts(model.layoutNodes, base);
  const actualTop = new Map<string, number>();
  const incoming = new Set(model.layoutEdges.map((edge) => edge.target));
  let rootBottom: number | null = null;

  for (const root of [...model.layoutNodes]
    .filter((node) => !incoming.has(node.id))
    .sort((left, right) => (base.get(left.id)?.top ?? 0) - (base.get(right.id)?.top ?? 0))) {
    const current = currentById.get(root.id) ?? root;
    const top = Math.max(
      base.get(root.id)?.top ?? 0,
      rootBottom === null ? 0 : rootBottom + ROOT_GAP,
    );
    actualTop.set(root.id, top);
    rootBottom = top + current.height;
  }

  const columnBottom = new Map<number, number>();
  const pending = [...groups];
  for (let pass = 0; pending.length && pass <= model.layoutNodes.length; pass += 1) {
    let progressed = false;
    for (let index = 0; index < pending.length;) {
      const group = pending[index];
      const source = currentById.get(group.source);
      const sourceTop = actualTop.get(group.source);
      const firstTargetBase = base.get(group.edges[0]?.target ?? "");
      if (!source || sourceTop === undefined || !firstTargetBase) {
        index += 1;
        continue;
      }
      const parentTop = sourceCardTop(source, sourceTop, group.sourcePort);
      const column = firstTargetBase.column;
      const groupTop = Math.max(
        firstTargetBase.top,
        parentTop,
        (columnBottom.get(column) ?? -Infinity) + CALL_GROUP_GAP,
      );
      let top = groupTop;
      for (const edge of group.edges) {
        const target = currentById.get(edge.target);
        if (!target) continue;
        actualTop.set(target.id, top);
        top += target.height + CALL_GROUP_GAP;
      }
      columnBottom.set(column, top - CALL_GROUP_GAP);
      pending.splice(index, 1);
      progressed = true;
    }
    if (!progressed) break;
  }

  for (const node of model.layoutNodes) {
    if (!actualTop.has(node.id)) actualTop.set(node.id, base.get(node.id)?.top ?? 0);
  }

  const positions = new Map<string, CanvasNodePosition>();
  for (const [id, node] of currentById) {
    const placement = base.get(id) ?? { column: 0, top: 0 };
    const left = lefts.get(placement.column) ?? 0;
    const top = actualTop.get(id) ?? placement.top;
    positions.set(id, {
      id,
      column: placement.column,
      left,
      top,
      width: node.width,
      height: node.height,
      x: left + node.width / 2,
      y: top + node.height / 2,
    });
  }

  const visibleEdgeIds = new Set(model.edges.map((edge) => edge.id));
  const visibleGroups = groupRelationships(
    model.layoutEdges.filter((edge) => visibleEdgeIds.has(edge.id)),
    basisById,
  );
  const connectionGroups = visibleGroups.flatMap((group): CanvasConnectionGroup[] => {
    const source = currentById.get(group.source);
    const sourcePosition = positions.get(group.source);
    const edges = group.edges.filter((edge) => visibleById.has(edge.target));
    if (!source || !sourcePosition || !edges.length) return [];
    const sourceTop = sourceCardTop(source, sourcePosition.top, group.sourcePort);
    return [{
      id: group.id,
      source: group.source,
      sourcePort: group.sourcePort,
      sourceTop,
      sourceY: sourceTop + CONNECTION_OFFSET_Y,
      targets: edges.map((edge) => edge.target),
      edges,
    }];
  });
  return { positions, connectionGroups };
}
