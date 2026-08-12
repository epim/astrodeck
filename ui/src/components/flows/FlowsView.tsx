// FlowsView.tsx — the Flows route entry (MILESTONE2-CONTRACT §C.1).
//
// It renders three things and decides nothing else: the view marker, the tier,
// and which of the two screens is up. Everything with an opinion lives one
// level down.
//
// WHY `data-view="flows"` IS HERE AND NOT IN App.tsx. It is the harness's
// outermost marker (§F.1) and `scripts/flows_visual_check.py` waits for it to be
// VISIBLE before it photographs anything — gate 1 exists because "a capture can
// otherwise be a perfectly-rendered photograph of the wrong page." Putting it on
// this root keeps App.tsx to the one-line NAV append the scope fence allows.
//
// WHY THE TIER COMES FROM JS. The design's boundaries are 700 / 1080. `index.css`
// defines no `--breakpoint-*` (its only `@theme inline` block is colours and
// fonts) and `ui/src` contains zero arbitrary `min-[Npx]:` variants, so neither
// number is expressible as a Tailwind variant without editing the token file.
// `useMediaQuery` is the codebase's own escape hatch for exactly this and is
// what §C.1 assumes. ⚠ §G-6 is OPEN: it also has to be confirmed whether 700 /
// 1080 measure the VIEWPORT (what this measures, matching the prototype's
// full-bleed root) or the Flows container, which inside `<main>` is ~104px
// narrower at >=640px because of the 72px rail and the 32px `p-4`.
import type { JSX } from "react";

import { useStore } from "../../store";
import { useMediaQuery } from "../ui";
import type { FlowTier } from "./geometry";
import FlowHeader from "./FlowHeader";
import FlowLibrary from "./FlowLibrary";
import FlowEditor from "./FlowEditor";
import FlowWizard from "./FlowWizard";

/** Viewport tier, per §C.1. Exported because it is the one piece of layout
 *  policy the whole surface shares; every screen below takes it as a prop so
 *  only this component subscribes to the two queries. */
export function useFlowsTier(): FlowTier {
  const desktop = useMediaQuery("(min-width: 1080px)");
  const notPhone = useMediaQuery("(min-width: 700px)");
  return desktop ? "desktop" : notPhone ? "tablet" : "phone";
}

/** Narrow, primitive-returning selector: zustand compares the RESULT with
 *  Object.is, so a string result is exact and a pan, a status tick or a log
 *  line cannot re-render this component. */
const useFlowScreen = () => useStore((s) => s.flows.ui.screen);

export default function FlowsView(): JSX.Element {
  const screen = useFlowScreen();
  const tier = useFlowsTier();

  return (
    <div data-view="flows" className="fill-grow flex flex-col min-h-0 min-w-0">
      {/* The 54px toolbar rides above BOTH screens — capture 01 carries it on
          the library too (provider badge + i, no back/TONIGHT/RUN). */}
      <FlowHeader tier={tier} />
      {screen === "library" ? <FlowLibrary /> : <FlowEditor tier={tier} />}
      {/* The wizard is armed from the LIBRARY (`+ NEW FLOW` sets
          `ui.wizardOpen`) but produces a graph the EDITOR shows, so it is
          mounted above the screen switch rather than inside either one. It
          reads its own open flag and renders null when closed, and it portals
          through `Overlay`, so its position here costs nothing and decides
          nothing about where it paints. */}
      <FlowWizard />
    </div>
  );
}
