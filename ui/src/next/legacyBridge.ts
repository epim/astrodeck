// legacyBridge.ts - the one-way door from the OLD navigation model into the new
// router (ARCHITECTURE.md sections 2 and 9; store-api.md section 1.5).
//
// Twenty-odd reused components navigate by calling `useStore.getState().setView(v)`
// or `openHelp(topic)`. Those calls are baked into the components, not into a
// router the new UI could decline to mount, so "send me to Monitor" from inside
// a reused panel would otherwise write a store field nothing reads and the tap
// would do nothing at all - the worst failure shape there is, because the screen
// looks fine.
//
// So: watch the two legacy fields and translate a CHANGE into a route. Only a
// change after mount, never the value that is already there - `view` starts at
// "connect" and treating the initial value as an instruction would slam every
// cold start onto Rig - Devices no matter what the user deep-linked.
//
// The reverse direction does not exist. Nothing in the new UI writes `store.view`
// (ARCHITECTURE.md section 9), except `main.tsx`'s one classic handoff.

import { useEffect, useRef } from "react";
import { useStore } from "../store";
import type { ViewName } from "../types";
import { nav } from "./router";

/** Where each legacy destination lands in the new information architecture.
 *  Keyed by the full `ViewName` union, so adding a view to `types.ts` without
 *  giving it a home here is a type error rather than a dead tap. */
export const LEGACY_VIEW_ROUTE: Record<ViewName, string> = {
  connect: "/rig/devices",
  capture: "/rig/capture",
  focus: "/rig/devices/focuser",
  mount: "/rig/devices/mount",
  polar: "/rig/devices/mount/polar",
  guide: "/rig/devices/guider",
  sequence: "/session/flows/planEditor",
  power: "/rig/devices/power",
  settings: "/settings",
  monitor: "/monitor/live",
  atlas: "/sky?frame=1",
  tonight: "/sky/targets",
  report: "/session/gallery/report",
  help: "/settings/help",
  gallery: "/session/gallery",
  flows: "/session/flows",
};

/** Mount once, in `NextApp`. Returns nothing: it is an effect, not a value. */
export function useLegacyBridge(): void {
  const view = useStore((s) => s.view);
  const helpTopic = useStore((s) => s.helpTopic);
  const clearHelpTopic = useStore((s) => s.clearHelpTopic);

  const mounted = useRef(false);
  const lastView = useRef<ViewName>(view);
  const lastTopic = useRef<string | null>(helpTopic);

  useEffect(() => {
    if (!mounted.current) {
      // First commit: adopt the current values as the baseline and navigate
      // nowhere. The route the user arrived on wins over the store's default.
      mounted.current = true;
      lastView.current = view;
      lastTopic.current = helpTopic;
      return;
    }

    // A help topic is more specific than the view change that accompanies it
    // (`openHelp` writes both in one set), so it is checked first and it
    // consumes the view change with it.
    if (helpTopic && helpTopic !== lastTopic.current) {
      lastTopic.current = helpTopic;
      lastView.current = view;
      nav.go(`${LEGACY_VIEW_ROUTE.help}?topic=${encodeURIComponent(helpTopic)}`);
      // The topic has been spent on a route; leaving it set would re-fire this
      // branch the next time anything else in the store moves.
      clearHelpTopic();
      return;
    }
    lastTopic.current = helpTopic;

    if (view !== lastView.current) {
      lastView.current = view;
      const path = LEGACY_VIEW_ROUTE[view];
      if (path) nav.go(path);
    }
  }, [view, helpTopic, clearHelpTopic]);
}
