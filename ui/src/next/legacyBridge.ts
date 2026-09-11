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
// So: watch the four legacy fields and translate a CHANGE into a route. Only a
// change after mount, never the value that is already there - `view` starts at
// "connect" and treating the initial value as an instruction would slam every
// cold start onto Rig - Devices no matter what the user deep-linked.
//
// FOUR FIELDS, NOT TWO. `view` and `helpTopic` were the first pair; `logOpen`
// and `wizardOpen` are the same shape and were missed (review #6 and #13).
// Both are booleans a reused component sets and this root never read:
//
//   * `store.openLog()` is the sticky sequence-fatal toast's VIEW LOG button
//     and `views/SequenceView.tsx:931`'s log link. `components/LogDrawer` is
//     the only thing that ever rendered off `logOpen`, and it is not mounted
//     here, so both presses did nothing at all.
//   * `store.openWizard()` is `views/HelpView.tsx:88`'s SETUP GUIDE button.
//     `components/FirstRunWizard` is likewise not mounted, so the Help sheet
//     shipped one live SETUP GUIDE row and one dead one about 200 px apart.
//
// Each is SPENT the way `helpTopic` is: navigate, then clear the flag through
// the store's own closer. A flag left set can never fire again (it is already
// true, so the next press is not a change), which is the same trap the topic
// had. Note that `closeWizard()` also marks the first-run coach key seen -
// deliberate, and identical to what finishing the legacy docked wizard did:
// the user has been handed the guide. The setup BANNER in `shell/Banners.tsx`
// is dismissed per session and not by that key, so the global nudge survives.
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
  // ATLAS IS ITS OWN MODE NOW, not the finder with FRAME switched on. This
  // used to be `/sky?frame=1`, which resumed the framing session in the Sky
  // hub's FRAME mode - and where there was no session (an old `#/atlas`
  // bookmark, the classic root's own Atlas link) it landed on the schematic
  // finder with a toast saying nothing was framed, which reads as "no atlas
  // loaded". `?mode=atlas` mounts the pannable survey canvas the classic
  // `views/AtlasView` always showed, whether or not anything is framed.
  // `?frame=1` is still honoured by `SkyHub` for a link that specifically
  // means "resume the framing", and `SkyHub` ignores `?mode=atlas` while FRAME
  // is already on, because `store.openFraming()` writes `view: "atlas"` and
  // that write travels back through here.
  atlas: "/sky?mode=atlas",
  tonight: "/sky/targets",
  report: "/session/gallery/report",
  help: "/settings/help",
  gallery: "/session/gallery",
  flows: "/session/flows",
};

/** Where `store.openLog()` lands. The Monitor hub's LOG screen is the new
 *  home of what `components/LogDrawer` used to be. */
export const LOG_ROUTE = "/monitor/log";

/** Where `store.openWizard()` lands: the setup sheet over Settings - General,
 *  which is the same guide the FIRST-TIME SETUP card opens. */
export const WIZARD_ROUTE = "/settings/general/setup";

/** Mount once, in `NextApp`. Returns nothing: it is an effect, not a value. */
export function useLegacyBridge(): void {
  const view = useStore((s) => s.view);
  const helpTopic = useStore((s) => s.helpTopic);
  const clearHelpTopic = useStore((s) => s.clearHelpTopic);
  const logOpen = useStore((s) => s.logOpen);
  const closeLog = useStore((s) => s.closeLog);
  const wizardOpen = useStore((s) => s.wizardOpen);
  const closeWizard = useStore((s) => s.closeWizard);

  const mounted = useRef(false);
  const lastView = useRef<ViewName>(view);
  const lastTopic = useRef<string | null>(helpTopic);
  const lastLog = useRef<boolean>(logOpen);
  const lastWizard = useRef<boolean>(wizardOpen);

  useEffect(() => {
    if (!mounted.current) {
      // First commit: adopt the current values as the baseline and navigate
      // nowhere. The route the user arrived on wins over the store's default.
      mounted.current = true;
      lastView.current = view;
      lastTopic.current = helpTopic;
      lastLog.current = logOpen;
      lastWizard.current = wizardOpen;
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

    // The wizard before the log before the view, for the same reason the topic
    // comes first: each is MORE specific than a bare view change, and none of
    // them writes `view` at all, so the ordering only decides which fires when
    // two flags move in one set().
    if (wizardOpen && !lastWizard.current) {
      lastWizard.current = false;   // spent below by closeWizard()
      lastView.current = view;
      nav.go(WIZARD_ROUTE);
      closeWizard();
      return;
    }
    lastWizard.current = wizardOpen;

    if (logOpen && !lastLog.current) {
      lastLog.current = false;      // spent below by closeLog()
      lastView.current = view;
      nav.go(LOG_ROUTE);
      closeLog();
      return;
    }
    lastLog.current = logOpen;

    if (view !== lastView.current) {
      lastView.current = view;
      const path = LEGACY_VIEW_ROUTE[view];
      if (path) nav.go(path);
    }
  }, [view, helpTopic, clearHelpTopic, logOpen, closeLog, wizardOpen, closeWizard]);
}
