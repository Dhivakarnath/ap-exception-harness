/// <reference types="vitest/config" />
import path from "node:path";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vite";

// Tailwind v4 is a Vite plugin (CSS-first config lives in src/index.css via
// @theme), so there is no tailwind.config.js. The `@` alias points at src, the
// convention shadcn/ui components expect.
// Override when host :8080 is occupied: VITE_DEV_API_URL=http://localhost:8082 npm run dev
const apiTarget = process.env.VITE_DEV_API_URL ?? "http://localhost:8080";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    port: 5173,
    // The backend API. Proxying keeps the browser same-origin in dev, which
    // also sidesteps CORS for XHR — though the SSE EventSource hits the API
    // origin directly, so the backend CORS allowlist still matters.
    proxy: {
      "/api": {
        target: apiTarget,
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api/, ""),
      },
    },
  },
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    css: true,
  },
});
