/** Share the observer's Ant Design theme with the main page and G6 React nodes. */

import { theme, type ThemeConfig } from "antd";

export type ThemeMode = "dark" | "light";

/** Return one theme contract so React nodes look identical inside and outside G6. */
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
