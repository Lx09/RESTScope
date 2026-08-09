/** Share the observer's Ant Design theme across conversations and Drawers. */

import { theme, type ThemeConfig } from "antd";

export type ThemeMode = "dark" | "light";

/** Return one theme contract for every page-level and expanded detail surface. */
export function observerTheme(mode: ThemeMode): ThemeConfig {
  return {
    algorithm: mode === "dark" ? theme.darkAlgorithm : theme.defaultAlgorithm,
    token: {
      borderRadius: 8,
      colorPrimary: "#4f8cff",
      fontFamily: "Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
      fontFamilyCode: "'SFMono-Regular', Consolas, 'Liberation Mono', monospace",
    },
  };
}
