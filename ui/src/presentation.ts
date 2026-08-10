/** Search and redundant visual metadata for schema-v3 conversation events. */

import type { EventStatus, TimelineEvent, TimelineFilters } from "./types";

export const EMPTY_FILTERS: TimelineFilters = {
  search: "",
  kinds: [],
  toolFamilies: [],
  statuses: [],
};

export const STATUS_LABELS: Record<EventStatus, string> = {
  running: "运行中",
  succeeded: "成功",
  warning: "警告",
  failed: "失败",
};

export const KIND_LABELS: Record<TimelineEvent["kind"], string> = {
  agent_turn: "Agent",
  tool_call: "Tool",
};

export const ROLE_LABELS: Record<string, string> = {
  system: "System",
  user: "User / Harness",
  assistant: "Assistant",
  tool: "Tool result",
};

export const TOOL_FAMILY_LABELS: Record<string, string> = {
  worklist: "Worklist",
  plan: "Plan",
  openapi: "OpenAPI",
  test_case: "Test Case",
  parameter_patch: "Parameter Memory / Patch",
  resource: "Resource",
  http: "HTTP",
  mcp: "MCP",
  other: "其他工具",
};

export function toolFamily(event: TimelineEvent): string | null {
  const family = event.attributes["restscope.tool.family"];
  return event.kind === "tool_call" && typeof family === "string" ? family : null;
}

export function visibleStatusLabel(event: TimelineEvent): string {
  return event.status === "warning" && event.detail.stopped === true
    ? "已停止"
    : STATUS_LABELS[event.status];
}

function searchableText(event: TimelineEvent): string {
  return JSON.stringify({
    name: event.name,
    summary: event.summary,
    agent: event.agent,
    operation: event.operation_key,
    detail: event.detail,
  }).toLocaleLowerCase();
}

export function eventMatches(event: TimelineEvent, filters: TimelineFilters): boolean {
  const search = filters.search.trim().toLocaleLowerCase();
  if (search && !searchableText(event).includes(search)) return false;
  if (filters.kinds.length && !filters.kinds.includes(event.kind)) return false;
  const family = toolFamily(event);
  if (
    filters.toolFamilies.length &&
    (!family || !filters.toolFamilies.includes(family))
  ) {
    return false;
  }
  return !filters.statuses.length || filters.statuses.includes(event.status);
}
