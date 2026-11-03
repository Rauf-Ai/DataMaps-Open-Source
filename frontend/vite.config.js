import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// В dev-режиме API ходит на backend через docker-compose. В prod nginx сам проксирует /api.
export default defineConfig({
  plugins: [react()],
  server: {
    host: "0.0.0.0",
    port: 5173,
    proxy: {
      "/api": { target: process.env.VITE_API_TARGET || "http://backend:8000", changeOrigin: true },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
    target: "es2020",
  },
  test: {
    environment: "jsdom",
    setupFiles: "./src/test/setup.js",
    clearMocks: true,
    server: {
      deps: {
        inline: ["@gravity-ui/uikit"],
      },
    },
  },
});
