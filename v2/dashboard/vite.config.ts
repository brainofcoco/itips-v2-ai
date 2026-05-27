import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The dev server proxies everything the Python app owns to the itips
// container (in Docker) or localhost:5050 (when you run `pnpm dev`
// directly on the host). Production serves the build via nginx and
// proxies the same paths to the same target — see dashboard/nginx.conf.
const API_TARGET = process.env.VITE_API_TARGET || "http://localhost:5050";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "0.0.0.0",
    port: 5173,
    proxy: {
      "/api":      { target: API_TARGET, changeOrigin: true },
      "/events":   { target: API_TARGET, changeOrigin: true },
      "/live":     { target: API_TARGET, changeOrigin: true },
      "/snap":     { target: API_TARGET, changeOrigin: true },
      "/status":   { target: API_TARGET, changeOrigin: true },
      "/health":   { target: API_TARGET, changeOrigin: true },
    },
  },
  build: {
    outDir: "dist",
    target: "es2022",
  },
});
