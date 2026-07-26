import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vite";

export default defineConfig({
  // relative asset paths so the SPA loads at the server root (/) AND tunnelled
  // under the relay (/h/<home_id>/). Runtime API/WS/auth URLs are base-prefixed
  // via src/lib/base.ts.
  base: "./",
  plugins: [react(), tailwindcss()],
  build: {
    rollupOptions: {
      output: {
        // The views are code-split (src/lib/lazyViews.tsx). Left alone, Rollup
        // also emits a long tail of sub-1 kB shared chunks for helpers that any
        // two views happen to share — each one a separate HTTP request to a
        // Raspberry-Pi-class box over field WiFi, for a couple of hundred bytes,
        // and each one worse-compressing than the same bytes inlined. Folding
        // chunks under this floor into their importers trades a little
        // duplication for many fewer round trips.
        //
        // 5 kB is the measured knee. Sweeping the threshold (entry gzip / total
        // gzip / chunk count): 0 -> 94.35 / 256.37 / 27; 3k -> 94.67 / 253.69 / 20;
        // 5k -> 95.37 / 253.16 / 18; 8k -> 100.75 / 252.02 / 15. Past 5 kB the
        // entry chunk — the only one that blocks first paint — starts absorbing
        // real weight for a couple of saved requests, which is the wrong trade.
        experimentalMinChunkSize: 5_000,
      },
    },
  },
  server: {
    proxy: {
      "/api": "http://127.0.0.1:8800",
      "/ws": { target: "ws://127.0.0.1:8800", ws: true },
    },
  },
});
