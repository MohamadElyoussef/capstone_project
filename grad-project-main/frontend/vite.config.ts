import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vite";
import path from "path";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    host: "0.0.0.0",
    port: 5000,
    allowedHosts: true,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: false,
        proxyTimeout: 600000,
        timeout: 600000,
        configure: (proxy) => {
          proxy.on("error", (err, _req, _res) => {
            console.error("[proxy error]", err.message);
          });
        },
      },
    },
  },
  preview: {
    host: "0.0.0.0",
    port: 5000,
    allowedHosts: true,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: false,
        proxyTimeout: 600000,
        timeout: 600000,
      },
    },
  },
});
