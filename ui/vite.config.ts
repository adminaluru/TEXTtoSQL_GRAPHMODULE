/// <reference types="vitest" />
import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

// The browser only ever talks to the UI's own origin. During development
// Vite forwards /api/* to the FlowOne API, so no cross-origin access (CORS)
// has to be opened on the backend.
//
// Where the API runs: FLOWONE_API_URL from the terminal, or from ui/.env.local
// (one line, e.g. FLOWONE_API_URL=http://localhost:8001). Default: port 8000.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "FLOWONE_");
  const apiTarget = process.env.FLOWONE_API_URL ?? env.FLOWONE_API_URL ?? "http://localhost:8000";

  return {
    plugins: [react()],
    server: {
      port: 5173,
      proxy: {
        "/api": {
          target: apiTarget,
          changeOrigin: true,
          rewrite: (path: string) => path.replace(/^\/api/, ""),
        },
      },
    },
    test: {
      environment: "jsdom",
      setupFiles: ["./src/test/setup.ts"],
      globals: false,
    },
  };
});
