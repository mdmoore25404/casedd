import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const backendPort = process.env.CASEDD_HTTP_PORT || "8080";
const backendTarget = `http://127.0.0.1:${backendPort}`;

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": backendTarget,
      "/image": backendTarget,
      "/update": backendTarget,
    },
  },
});
