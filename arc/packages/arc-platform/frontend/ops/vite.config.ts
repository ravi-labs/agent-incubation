import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// arc-platform-ops (business-user dashboard) — Vite dev server.
//
// Default port 5173 (Vite default). API requests under /api/* are proxied
// to the FastAPI backend on port 8000, so the frontend can call relative
// URLs like /api/agents and they "just work" in dev.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
});
