import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const backendTarget =
  process.env.CASEDD_API_BASE ||
  `http://127.0.0.1:${process.env.CASEDD_HTTP_PORT || "8080"}`;

export default defineConfig(({ command }) => ({
  // In dev mode the SPA is served from root via the Vite proxy,
  // so API calls go to :5173/api → proxied to :8080.
  // In production builds, assets must live under /app/ to match
  // the FastAPI StaticFiles mount point at /app.
  base: command === "build" ? "/app/" : "/",
  plugins: [react()],
  server: {
    host: true,
    allowedHosts: true,
    port: 5173,
    proxy: {
      "/api": backendTarget,
      "/image": backendTarget,
      "/update": backendTarget,
    },
  },
}));
