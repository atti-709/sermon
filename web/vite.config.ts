import react from "@vitejs/plugin-react";
import path from "node:path";
import { defineConfig } from "vite";

// The Remotion composition core lives in ../captions/src (shared with Remotion
// Studio). dedupe forces its remotion/react imports to resolve to THIS app's
// node_modules — two copies would break the Player's React context.
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@captions": path.resolve(__dirname, "../captions/src"),
    },
    dedupe: [
      "react",
      "react-dom",
      "remotion",
      "@remotion/captions",
      "@remotion/fonts",
      "@remotion/media",
    ],
  },
  server: {
    fs: { allow: [path.resolve(__dirname, "..")] },
    proxy: {
      "/api": "http://127.0.0.1:8756",
      "/media": "http://127.0.0.1:8756",
    },
  },
});
