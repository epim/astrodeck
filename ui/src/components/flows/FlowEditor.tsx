// FlowEditor.tsx — the editor host (contract §A.1, §C.4, §C.13, §F.1).
//
// It owns three markers, one layout decision and the Target modal's host (see
// below), and nothing else. Every surface it places already exists and already
// owns its own store reads, so this file subscribes to three primitives: the
// run phase (which is a marker value), the phone tab (which is a layout
// branch) and the open record's id (which the modal is pinned to). A node
// status tick, a pan, a param edit and a log line all re-render nothing here;
// the counts line is its own component with its own string selector.
//
// WHY THE OVERLAYS ARE MOUNTED HERE. `FlowEditSheet`, `FlowPaletteSheet` and
// `TonightPanel` each read their own open-flag and render null when closed, so
// they cost nothing mounted — but SOMETHING has to mount them, and until this
// file landed nothing did. They portal through `Overlay` (never `fixed inset-0`
// inside the view tree), so their position in this tree is irrelevant to where
// they paint; what matters is that they are inside the editor's lifetime, so
// closing the editor cannot leave a sheet stranded over the library.
//
// WHY `onPick` IS COMPUTED HERE. `FlowPalette` deliberately refuses to guess a
// drop position (§C.7): only the editor knows whether a canvas is mounted and
// where its centre is. `flowCanvasDropPoint(tier)` reads the live canvas box;
// its documented fallback, `PALETTE_FALLBACK_DROP`, is the prototype's (120,120)
// and stacks stages on top of each other, which is why the real point is tried
// first.
//
// THE TARGET MODAL IS HOSTED HERE (#189 S4; spec 2026-09-23 flows mosaic, 2.1).
// Three doors open it and they sit in three different components: the FRAME ON
// SKY row in the inspector column, the same row in the tablet/phone edit sheet,
// and a palette drop of a TARGET, which opens it "at once" on the id
// `flowsAddNode` returns, with no inspector on screen at all. So the one open
// modal is state of this component, the opener reaches the inspector as
// `FramingDoorContext`, and `onPick` below calls it directly. The sheet is
// imported through `./framing` (index.ts), which imports its module only
// dynamically: the survey canvas, the catalogue search and the night card
// arrive when a block is first framed, never with the editor
// (__tests__/classicFrameHost.test.tsx walks the static imports to hold it).
//
// THE COUNTS LINE IS ALSO HERE (spec Revision 2 ruling 2; S4 orchestrator
// ruling 8). While any TARGET or POOL still counts every sub taken, the editor
// carries `countsNotice`'s line above every tier's body, for as long as it is
// true. It is not a log line: the flow log is a ring that scrolls, and the
// ruling's line is a standing fact about the flow that only a save changes.
import {
  Component, Suspense, useCallback, useMemo, useState,
  type ComponentType, type JSX, type ReactNode,
} from "react";

import { useStore } from "../../store";
import { Overlay } from "../Overlay";
import type { FlowNodeType } from "./flowsTypes";
import type { FlowTier } from "./geometry";
import FlowCanvas, { flowCanvasDropPoint } from "./FlowCanvas";
import { FlowPalette, PALETTE_FALLBACK_DROP } from "./FlowPalette";
import FlowPaletteSheet from "./FlowPaletteSheet";
import FlowInspector, { FramingDoorContext, type FramingDoor } from "./FlowInspector";
import FlowEditSheet from "./FlowEditSheet";
import TonightPanel from "./TonightPanel";
import FlowPhoneTabs from "./FlowPhoneTabs";
import FlowPhoneGraph from "./FlowPhoneGraph";
import FlowPhoneMonitor from "./FlowPhoneMonitor";
import FlowTapWireBar from "./FlowTapWireBar";
import { countsNotice } from "./countsNotice";
import { TargetFramingSheetLazy, type TargetFramingSheetProps } from "./framing";

// ------------------------------------------------------------ the framing host

/** The sheet this editor mounts. A module variable only so a test can stand a
 *  failing chunk in for it (`__setFramingSheetForTest`), the seam lazyViews
 *  keeps for the routed views; app code never reassigns it. */
let FramingSheet: ComponentType<TargetFramingSheetProps> = TargetFramingSheetLazy;

/** Test seam: mount `c` in place of the lazy sheet. Not used by app code. */
export function __setFramingSheetForTest(c: ComponentType<TargetFramingSheetProps>): void {
  FramingSheet = c;
}

/** Said when the sheet could not be mounted: its chunk did not arrive, or it
 *  threw. A dynamic import that failed is remembered as failed for the life
 *  of the page (lazyViews.ts, "THE BROWSER FACT"), so only a reload fetches it
 *  again; the editor and its unsaved graph stay up so the operator can save
 *  first. */
export const FRAMING_FAILED =
  "The framing sheet could not open. The flow is still here: save it, then reload the page to try again.";

/** Keeps a failed sheet from taking the editor down with it. Without it the
 *  error climbs to the view's boundary, which replaces the whole editor and
 *  offers a reload that would throw away every unsaved edit on the canvas. */
class FramingBoundary extends Component<
  { onClose: () => void; children: ReactNode }, { error: string | null }
> {
  state: { error: string | null } = { error: null };

  static getDerivedStateFromError(e: unknown): { error: string } {
    return { error: e instanceof Error ? e.message : String(e) };
  }

  render(): ReactNode {
    if (this.state.error === null) return this.props.children;
    return (
      <Overlay open label="Frame" onClose={this.props.onClose} variant="center">
        <div data-flows-frame-failed className="flex flex-col gap-3 p-4 text-[12px] leading-[1.5]">
          <p>{FRAMING_FAILED}</p>
          <p className="font-mono text-[10.5px] text-faint break-words">{this.state.error}</p>
          <button type="button" className="btn self-start" onClick={this.props.onClose}>CLOSE</button>
        </div>
      </Overlay>
    );
  }
}

/** The counts line (see the header). A string-or-null selector, so it
 *  re-renders when the line changes and at no other graph edit. */
function FlowCountsLine(): JSX.Element | null {
  const line = useStore((s) => countsNotice(s.flows.graph, s.flows.countsNote));
  if (line === null) return null;
  return (
    <div
      role="status"
      data-flows-counts
      className="flex-none px-3 py-2 border-b border-line text-[11px] leading-[1.45]
                 text-warn bg-[color-mix(in_srgb,var(--warn)_8%,transparent)] [text-wrap:pretty]"
    >
      {line}
    </div>
  );
}

export interface FlowEditorProps {
  /** Resolved once by `FlowsView` (§C.1) and threaded down, so only one
   *  component in the tree subscribes to the two media queries. */
  tier: FlowTier;
}

export default function FlowEditor({ tier }: FlowEditorProps): JSX.Element {
  // Narrow, primitive-returning selectors. `phase` is a string that is ALSO the
  // harness's marker value (§F.1: state 07 waits ≤180 s for
  // `[data-flows-run='holding']`), so it has to be subscribed even though
  // nothing else on this surface reads it.
  const phase = useStore((s) => s.flows.run.phase);
  const phoneTab = useStore((s) => s.flows.ui.phoneTab);
  const addNode = useStore((s) => s.flowsAddNode);
  const setEditNode = useStore((s) => s.flowsSetEditNode);
  // The flow the open modal belongs to. Node ids are per flow ("n2" is a
  // TARGET in one flow and a capture in the next), so a modal is shown only
  // over the flow it was opened in, never re-pointed at a namesake.
  const recordId = useStore((s) => s.flows.record?.id ?? null);

  const phone = tier === "phone";

  // THE OPEN MODAL: the node it frames and the flow it was opened in.
  const [frame, setFrame] = useState<{ nodeId: string; recordId: string | null } | null>(null);
  const framing = frame !== null && frame.recordId === recordId ? frame.nodeId : null;
  // A CLOSE CLOSES ITS OWN OPENING (#382). Every open makes a new `frame`
  // object, and the sheet is handed a close that clears that object only, so
  // a close arriving after its modal was replaced (even by a second opening of
  // the same block) closes nothing. The shared sheet once called onClose a
  // second time, when DONE's compile answered after CANCEL; it now refuses
  // CANCEL while DONE writes, and #/next's adapter pops only while flowFrame is
  // on top. This is the classic host's half of that guard, so the host does
  // not rest on the sheet never doing it again.
  const closeFrame = useCallback(
    () => setFrame((cur) => (cur === frame ? null : cur)),
    [frame],
  );

  const door = useMemo<FramingDoor>(() => ({
    open: (nodeId: string) => {
      // THE MODAL REPLACES THE EDIT SHEET rather than stacking over it, as
      // #/next's flowFrame replaces flowNode (spec 2.1): two modal Overlays
      // both trap focus and both close on one Escape.
      setEditNode(null);
      setFrame({ nodeId, recordId: useStore.getState().flows.record?.id ?? null });
    },
  }), [setEditNode]);

  const onPick = useCallback(
    (type: FlowNodeType) => {
      const id = addNode(type, flowCanvasDropPoint(tier) ?? PALETTE_FALLBACK_DROP);
      // A TARGET is not placed until it is framed, so dropping one opens its
      // modal at once (spec 2.1), on the id the add itself returned. Every
      // other stage is placed by its drop.
      if (type === "target") door.open(id);
    },
    [addNode, tier, door],
  );

  return (
    <FramingDoorContext.Provider value={door}>
      <div
        data-flows-tab="editor"
        // README §IA names all three strings; the phone one is distinct because
        // the phone surface is a different editor, not a narrow one.
        data-screen-label={phone ? "Flow editor (phone)" : "Flow editor"}
        data-flows-run={phase}
        className="fill-grow flex flex-col min-h-0 min-w-0"
      >
        <FlowCountsLine />
        {phone ? (
          // PHONE — three tabs, one of which IS the tablet/desktop canvas
          // (README §5: "identical to tablet/desktop canvas"). The tab bar is
          // outside the scroll region so it never scrolls away.
          <>
            <div className="flex-1 flex min-h-0 min-w-0 relative">
              {phoneTab === "flow" && <FlowPhoneGraph />}
              {phoneTab === "canvas" && <FlowCanvas tier={tier} />}
              {phoneTab === "monitor" && <FlowPhoneMonitor />}
              {/* Armed tap-to-wire is a property of the FLOW tab only; the bar
                  renders null unless `tapWire` is set, and switching tabs clears
                  it (§C.15). */}
              <FlowTapWireBar />
            </div>
            <FlowPhoneTabs />
          </>
        ) : (
          <div className="flex-1 flex min-h-0 min-w-0">
            {/* DESKTOP — palette rail ∣ canvas ∣ inspector column. At tablet the
                rail and the column are both replaced by sheets: the canvas's own
                `+ ADD STAGE` float opens the palette, and the node's ✎ opens the
                edit sheet (README §3: the ✎ is "the ONLY thing that opens the
                edit sheet on tablet/phone"). */}
            {tier === "desktop" && <FlowPalette variant="rail" onPick={onPick} />}
            <FlowCanvas tier={tier} />
            {tier === "desktop" && <FlowInspector variant="column" />}
          </div>
        )}

        <FlowPaletteSheet onPick={onPick} />
        <FlowEditSheet />
        <TonightPanel />
        {/* It portals through Overlay 'full', so where it sits in this tree
            decides nothing about where it paints. Closing unmounts it, the
            boundary with it, so a failure is forgotten once it is closed. */}
        {framing !== null && (
          <FramingBoundary onClose={closeFrame}>
            <Suspense fallback={null}>
              <FramingSheet nodeId={framing} onClose={closeFrame} />
            </Suspense>
          </FramingBoundary>
        )}
      </div>
    </FramingDoorContext.Provider>
  );
}
