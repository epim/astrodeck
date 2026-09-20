import { useEffect, useState } from "react";
import App from "./App";
import NextApp from "./next/NextApp";
import { NEXT_HOME, NEXT_ROOT_ALIAS, firstSegment, nav } from "./next/router";
import { classicViewForHash, rootForHash } from "./rootChoice";
import { LEGACY_VIEW_ROUTE } from "./next/legacyBridge";
import { useStore } from "./store";
import type { ViewName } from "./types";

// TWO ROOTS, ONE AT A TIME (next/ARCHITECTURE.md section 2). Which one a hash
// asks for is decided in ONE place, `rootChoice.ts`, off the `DEFAULT_ROOT`
// constant; this file only acts on the answer. Only one root is ever rendered,
// which is what makes it safe for both to call `connectWs()` and both to
// publish the auth gate.
//
// With `DEFAULT_ROOT = "classic"` (what ships): the bare root and every legacy
// view name at the root (`#/`, `#/atlas`, `#/mount`, ...) mount the classic
// `App`; the new UI keeps every one of its own routes (`#/sky`, `#/session`,
// `#/rig`, `#/weather`, `#/monitor`, `#/settings` and their sheets) and adds
// `#/next` as the alias for its home. `#/classic[/<view>]` still works, so no
// existing link breaks in either direction.
//
// The hash is re-read on `hashchange` rather than only at load, so typing
// `#/classic` or `#/next` in the address bar switches roots without a reload -
// and so does typing `#/` to come back.
//
// This component lives here rather than in `main.tsx` so that a test can mount
// it. `main.tsx` is the entry point and calls `createRoot` at import time;
// anything importing it would boot the whole app as a side effect.

// Derived, not hand-listed: `LEGACY_VIEW_ROUTE` is a `Record<ViewName, string>`,
// so the compiler already forces it to name every view. A second hand-written
// copy of the union here would go stale the first time a view is added, and the
// symptom would be a `#/classic/<view>` deep link that silently lands on the
// default view instead.
const VIEW_NAMES: ReadonlySet<string> = new Set(Object.keys(LEGACY_VIEW_ROUTE));

/** `#/classic/mount`, and now a bare `#/mount`, -> tell the store which view to
 *  open, ONCE, before the legacy root renders. `App` itself is not modified: it
 *  reads `store.view`, so the deep link is delivered by writing that field
 *  rather than by teaching the old shell about a router it has never had. */
export function applyClassicView(hash: string): void {
  const v = classicViewForHash(hash);
  if (v && VIEW_NAMES.has(v)) useStore.getState().setView(v as ViewName);
}

/** Decide the root for a hash AND pay whatever that decision costs before the
 *  chosen root renders: the classic deep-link store write, or canonicalising
 *  the `#/next` alias onto the new UI's home so the address bar names the
 *  screen the user is actually looking at. */
export function resolveRoot(hash: string): "classic" | "next" {
  const root = rootForHash(hash);
  if (root === "classic") {
    applyClassicView(hash);
  } else if (firstSegment(hash) === NEXT_ROOT_ALIAS) {
    nav.replace(NEXT_HOME);
  }
  return root;
}

export default function Root() {
  const [classic, setClassic] = useState(() => resolveRoot(window.location.hash) === "classic");

  useEffect(() => {
    // Give a bare classic entry its own destination before the first click,
    // so Back can restore it. Existing deep links (including query flags) stay
    // intact until the operator actually chooses another view.
    const nameClassicEntry = () => {
      const hash = window.location.hash;
      const path = hash.split("?")[0];
      const bare = ["", "#", "#/", "#/classic", "#/classic/"].includes(path);
      if (bare && rootForHash(hash) === "classic") {
        window.history.replaceState(window.history.state, "", `#/classic/${useStore.getState().view}${hash.slice(path.length)}`);
      }
    };
    const on = () => {
      setClassic(resolveRoot(window.location.hash) === "classic");
      nameClassicEntry();
    };
    nameClassicEntry();
    const unsubscribe = useStore.subscribe((state, previous) => {
      if (state.view === previous.view || rootForHash(window.location.hash) !== "classic") return;
      // A hash-driven store update is already at its destination. Do not add
      // another history entry when following a bookmark or pressing Back.
      if (classicViewForHash(window.location.hash) === state.view) return;
      const experience = new URLSearchParams(window.location.hash.split("?")[1]).get("experience");
      const presentation = experience === "guided" || experience === "pro" ? `?experience=${experience}` : "";
      window.location.hash = `/classic/${state.view}${presentation}`;
    });
    window.addEventListener("hashchange", on);
    return () => {
      unsubscribe();
      window.removeEventListener("hashchange", on);
    };
  }, []);

  return classic ? <App /> : <NextApp />;
}
