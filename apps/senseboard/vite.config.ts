/**
 * Vite config for SenseBoard.
 *
 * - `@contracts/types` resolves to the hand-written TS entry of the frozen contracts
 *   package (`packages/contracts/ts/index.ts`), so every type the board uses is the
 *   exact mirror of the pydantic models the edge/cloud emit. `server.fs.allow` lets
 *   the dev server read outside the app folder.
 * - `/edge` and `/cloud` are dev-only proxies to the two backends for convenience;
 *   the app still talks to `VITE_EDGE_URL` / `VITE_CLOUD_URL` directly by default.
 */
import path from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

const here = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(here, "../..");

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@contracts/types": path.resolve(repoRoot, "packages/contracts/ts/index.ts"),
      "@": path.resolve(here, "src"),
    },
  },
  server: {
    port: 5173,
    fs: { allow: [repoRoot] },
    proxy: {
      "/edge": { target: "http://localhost:8001", changeOrigin: true, ws: true, rewrite: (p) => p.replace(/^\/edge/, "") },
      "/cloud": { target: "http://localhost:8000", changeOrigin: true, ws: true, rewrite: (p) => p.replace(/^\/cloud/, "") },
    },
  },
  preview: { port: 5173 },
  build: {
    outDir: "dist",
    sourcemap: false,
    chunkSizeWarningLimit: 900,
    rollupOptions: {
      output: {
        manualChunks: { vendor: ["react", "react-dom", "react-router-dom"], charts: ["recharts"] },
      },
    },
  },
});
