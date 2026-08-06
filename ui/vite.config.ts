import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "../restscope/ui/static",
    emptyOutDir: true,
    sourcemap: false,
    chunkSizeWarningLimit: 1000,
  },
});
