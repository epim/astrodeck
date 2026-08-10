// Library build for the AstroDeck design system (src/design-system.ts).
//
// SEPARATE from vite.config.ts, which builds the rig's APP. Same source tree,
// two products: `npm run build` still emits the app that the FastAPI server
// serves, and `npm run build:lib` emits an importable ES module + real `.d.ts`
// for anything that wants to BUILD with these components — today, the
// claude.ai/design agent, which codes against those declarations.
//
// React is EXTERNAL. Bundling a second copy would give a consumer two Reacts
// and the hook errors that come with them; the design-sync bundler supplies
// React from its own `_vendor/`.
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { resolve } from "node:path";

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "dist-lib",
    // The app build owns `dist/`; this must never race or clobber it.
    emptyOutDir: true,
    lib: {
      entry: resolve(__dirname, "src/design-system.ts"),
      name: "AstroDeckUI",
      formats: ["es"],
      fileName: () => "index.es.js",
    },
    rollupOptions: {
      external: ["react", "react-dom", "react/jsx-runtime",
                 "react-dom/client"],
      output: {
        globals: {
          react: "React",
          "react-dom": "ReactDOM",
          "react/jsx-runtime": "jsxRuntime",
        },
        // One CSS file next to the entry, so `cssEntry` has something stable
        // to point at.
        assetFileNames: "style[extname]",
      },
    },
    sourcemap: true,
  },
});
