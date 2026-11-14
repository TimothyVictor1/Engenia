import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { fileURLToPath } from "node:url";

// `npm run build` writes the app into the Python package, where FastAPI serves it. The built
// files are committed so a Codespace or demo needs no Node.
export default defineConfig({
  base: "./",
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
  plugins: [react(), tailwindcss()],
  build: { outDir: "../engenia/dashboard/static", emptyOutDir: true },
  server: { proxy: { "/api": "http://127.0.0.1:8080" } },
  test: { environment: "node" },
});
