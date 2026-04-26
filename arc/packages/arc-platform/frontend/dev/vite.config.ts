import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// arc-platform-dev (engineer dashboard) — Vite dev server.
//
// Port 5174 (one off from ops on 5173) so both can run concurrently.
// API requests proxied to the FastAPI backend on port 8000.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5174,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
});
