import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import "./index.css";
import App from "./App";
import NextApp from "./next/NextApp";
import { classicView, isClassicHash } from "./next/router";
import { LEGACY_VIEW_ROUTE } from "./next/legacyBridge";
import { useStore } from "./store";
import type { ViewName } from "./types";

// TWO ROOTS, ONE AT A TIME (next/ARCHITECTURE.md section 2). `#/classic` and
// `#/classic/<view>` mount the legacy `App`, untouched; everything else mounts
// the new `NextApp`. Only one is ever rendered, which is what makes it safe for
// both to call `connectWs()` and both to publish the auth gate.
//
// The hash is re-read on `hashchange` rather than only at load, so typing
// `#/classic` in the address bar switches roots without a reload - and so does
// typing `#/` to come back.

// Derived, not hand-listed: `LEGACY_VIEW_ROUTE` is a `Record<ViewName, string>`,
// so the compiler already forces it to name every view. A second hand-written
// copy of the union here would go stale the first time a view is added, and the
// symptom would be a `#/classic/<view>` deep link that silently lands on the
// default view instead.
const VIEW_NAMES: ReadonlySet<string> = new Set(Object.keys(LEGACY_VIEW_ROUTE));

/** `#/classic/mount` -> tell the store which view to open, ONCE, before the
 *  legacy root renders. `App` itself is not modified: it reads `store.view`, so
 *  the deep link is delivered by writing that field rather than by teaching the
 *  old shell about a router it has never had. */
function applyClassicView(hash: string): void {
  const v = classicView(hash);
  if (v && VIEW_NAMES.has(v)) useStore.getState().setView(v as ViewName);
}

function Root() {
  const [classic, setClassic] = useState(() => {
    const c = isClassicHash(location.hash);
    if (c) applyClassicView(location.hash);
    return c;
  });

  useEffect(() => {
    const on = () => {
      const c = isClassicHash(location.hash);
      if (c) applyClassicView(location.hash);
      setClassic(c);
    };
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);

  return classic ? <App /> : <NextApp />;
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Root />
  </StrictMode>,
);
