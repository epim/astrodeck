import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./index.css";
import "./components/cloudmap/classicSkyDome.css";
import "./components/sky/classicSkyTools.css";
import Root from "./AppRoot";

// The entry point, and nothing else. The two-roots rule and the hash handling
// live in `AppRoot.tsx`, and which root the bare hash opens on lives in the one
// `DEFAULT_ROOT` constant in `rootChoice.ts` - see the comments in both. The
// split is so that a test can mount `Root`; importing this file would call
// `createRoot` as a side effect.

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Root />
  </StrictMode>,
);
