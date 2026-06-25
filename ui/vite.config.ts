import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vite";

export default defineConfig({
  // relative asset paths so the SPA loads at the server root (/) AND tunnelled
  // under the relay (/h/<home_id>/). Runtime API/WS/auth URLs are base-prefixed
  // via src/lib/base.ts.
  base: "./",
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      "/api": "http://127.0.0.1:8800",
      "/ws": { target: "ws://127.0.0.1:8800", ws: true },
    },
  },
});
