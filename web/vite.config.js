import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const backendTarget =
  process.env.CASEDD_API_BASE ||
  `http://127.0.0.1:${process.env.CASEDD_HTTP_PORT || "8080"}`;

export default defineConfig({
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
});
