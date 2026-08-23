import path from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

const here = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(here, "../..");

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@contracts/types": path.resolve(repoRoot, "packages/contracts/ts/index.ts"),
      "@": path.resolve(here, "src"),
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/tests/setup.ts"],
    include: ["src/tests/**/*.test.{ts,tsx}"],
    css: false,
    testTimeout: 10000,
  },
});
