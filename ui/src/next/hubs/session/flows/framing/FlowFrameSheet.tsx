// FlowFrameSheet.tsx - the #/next `flowFrame` sheet: the Target modal, opened
// on one TARGET stage (#189 S4 item 5; spec 2026-09-23 flows mosaic, 2.1).
//
// NO FORK. The modal is ONE shared component,
// `components/flows/framing/TargetFramingSheet.tsx`, which the classic
// inspector mounts too. This file is the adapter between the router's sheet
// contract (`{ params, depth }`) and that component's props (`{ nodeId,
// onClose, viewOnly }`), and nothing else: no layout, no copy, no draft. A
// #/next copy of the modal would be the second implementation of every rule
// in it (the DONE lock, the re-frame question, the loop wire), and the day
// one was fixed the other would still ship the defect.
//
// THE MODAL STAYS LAZY TWICE OVER (D-FU-2). `reg.ts` fetches this module with a
// dynamic `import()`, and this module mounts the shared door
// `TargetFramingSheetLazy`, which fetches the modal only when it renders. So
// the two surfaces that import the door helpers below statically - the stage
// editor's FRAME ON SKY row and the palette's drop - pay for this adapter and
// the router, never for the survey canvas, the catalogue search or
// `framing.css`.
//
// WHERE IT SITS IN THE STACK. The modal draws itself as a full-screen Overlay
// portalled above every sheet layer (`.overlay-host` is z-index 45, the phone
// sheet layer 30), so the router's slot under it stays empty. On a phone the
// door is inside the `flowNode` sheet at depth 2, and `nav.sheet` puts this
// sheet IN PLACE OF `flowNode` rather than burying the stage list three deep
// (`router.ts` MAX_SHEETS). The palette's drop replaces the palette sheet the
// same way, so BACK from the modal lands on the graph or the stage list, never
// on a palette whose one tap has already been spent.
//
// EVERY DOOR CARRIES `?open=`. `nav.sheet` builds the whole hash from the
// params it is handed, so a door that passed only the node would leave the URL
// naming no flow: reload or share it and the modal opens on a stage in a flow
// nobody named, and on a phone the stage list under it loses its flow too.
// `flowFrameParams` is the one place those params are built.
//
// AND THE SHEET FRAMES ONLY THE FLOW `?open=` NAMES. The modal reads its node
// from whatever flow the store holds, by node id, and node ids repeat across
// flows (every copy of an Example has an `n2`). Measured on the real page
// (tools/ui_probe routes_s4_frame.json, 2026-09-26, #384): with flow B
// open, a link to flow A's `flowFrame` mounted the modal on B's `n2` at once
// (its chunk was cached); the stage list under it then opened A, the
// modal's node became A's `n2` under the same key, and its draft stayed
// B's. It showed M33 and "moved 901'" against A's anchor, and DONE would
// have written B's target onto A. So while the route names a flow the store
// does not hold, the sheet says so and waits (with BACK, for a flow that
// never loads), and the modal mounts once that flow has loaded, fresh from
// its params; a flow that changes under an open modal unmounts it by the
// same test. With no `?open=` (an unsaved flow, which has no id to name) the
// sheet frames the open flow, as before. The classic host pins its modal to
// the record it was opened in (FlowEditor.tsx `frame.recordId`).
//
// RUN MODE IS THE DOOR'S TO DECIDE (#189 S5; spec 2.6). While the open flow's
// session is running, the modal opens with `viewOnly`: read-only, drawing the
// run's panels. Decided by flowRunState `flowRunLive`, the reader the classic
// host (FlowEditor.tsx `FramingHostSheet`) asks too, so a flow is running in
// both UIs or in neither.

import { Suspense, type JSX } from "react";

import { TargetFramingSheetLazy } from "../../../../../components/flows/framing";
import { flowRunLive } from "../../../../../components/flows/flowRunState";
import { useStore } from "../../../../../store";
import { buildHash, currentRoute, nav } from "../../../../router";
import { ActionButton, EmptyCard } from "../../../../ui";
import type { SheetProps } from "../../../sheets";

/** The sheet's own name in the route and the registry. `reg.ts` spells it as
 *  a literal key (it may not import this module), and `flowsDom.test.tsx`
 *  asserts the two are the same word. */
export const FLOW_FRAME_SHEET = "flowFrame";

/** What the slot shows for the moment between the route naming this sheet and
 *  the modal's chunk arriving. */
export const FRAME_LOADING = "Loading the framing for this stage.";

/** What the slot shows while the route names a flow the store does not hold
 *  (see the header): the stage list or the canvas under it is opening that
 *  flow, and the framing opens once it has. A flow that never opens (a link
 *  to one since deleted) leaves the card up, so it carries BACK: on a phone
 *  the slot is bare, and without it the only way out was the browser's own. */
export const FRAME_OTHER_FLOW =
  "This framing belongs to the flow the link names, and another flow is open. It opens once that flow has loaded.";

/** The same wait with NO flow open: a link loaded fresh (reloaded, shared, or
 *  opened from a bookmark), while the stage list under it loads the flow it
 *  names. FRAME_OTHER_FLOW would tell the operator another flow is open when
 *  none is (seen on the real page, routes_s4_frame.json
 *  s4-frame-phone-fresh-missing-flow, 2026-09-26): for a moment on every
 *  fresh link, and for good on a link to a flow since deleted. */
export const FRAME_FLOW_LOADING =
  "This framing opens once the flow the link names has loaded.";

/** The params every door into this sheet passes: the stage, and the flow it
 *  belongs to as `?open=` whenever one is open. */
export function flowFrameParams(
  node: string, open: string | null | undefined,
): Record<string, string> {
  return open ? { open, node } : { node };
}

/** Open the framing sheet on `node`.
 *
 *  `inPlaceOfTop` REPLACES the sheet on top of the stack rather than stacking
 *  on it, without a history entry: the palette's drop uses it, because the
 *  palette's own rule is one tap, one stage, and a palette left under the
 *  modal would take a BACK press to get past and offer a second drop nobody
 *  asked for. With no sheet open (the docked palette rail at desktop) there is
 *  nothing to replace and the sheet is pushed. */
export function openFlowFrame(
  node: string, open: string | null | undefined, opts: { inPlaceOfTop?: boolean } = {},
): void {
  const params = flowFrameParams(node, open);
  const r = currentRoute();
  if (opts.inPlaceOfTop && r.sheets.length > 0) {
    nav.replace(buildHash({
      hub: r.hub, sub: r.sub, sheets: [...r.sheets.slice(0, -1), FLOW_FRAME_SHEET], params,
    }));
    return;
  }
  nav.sheet(FLOW_FRAME_SHEET, params);
}

/** The modal's CANCEL, CLOSE and DONE, which pop this sheet - but only while
 *  it is still the top of the stack.
 *
 *  The guard was built for DONE's close, which arrives AFTER an await (DONE
 *  writes the framing, then waits for the compile), at a time when CANCEL
 *  could already have popped this sheet. The shared sheet has since closed
 *  that door itself (#382): CANCEL, Escape and the scrim refuse while DONE
 *  writes, and DONE's late close runs only while the modal is still mounted.
 *  So the guard is now defence in depth, and it stays because the two sides
 *  of it are so uneven: the check is one comparison, while an unguarded
 *  `nav.back()` that finds another sheet on top pops the stage list under
 *  the modal, or, with no sheet left at all, calls the browser's own Back and
 *  leaves the canvas.
 *
 *  Escape reaches this ONLY through the modal. `SheetHost` binds Escape to
 *  `nav.back()` on the window, which would pop this sheet whatever the modal
 *  decided; the modal's Overlay takes the key on `document` in the capture
 *  phase and stops it there, so the #382 refusal holds in #/next too.
 *  `framing/__tests__/flowFrameSheet.test.tsx` holds that seam. */
function closeFrame(): void {
  const sheets = currentRoute().sheets;
  if (sheets[sheets.length - 1] === FLOW_FRAME_SHEET) nav.back();
}

export function FlowFrameSheet({ params }: SheetProps): JSX.Element {
  const recordId = useStore((s) => s.flows.record?.id ?? null);
  // A boolean selector: a run of this flow starting or ending re-renders this
  // sheet, a frame or a status tick does not.
  const running = useStore((s) => flowRunLive(s.flows.progress, s.sequence));
  const open = params.open ?? "";
  // The shared modal owns every other decision: a node that is gone or is not
  // a TARGET says so and offers CLOSE, a read-only Example opens in view mode
  // with its reason, and in run mode the modal reads the run itself.
  //
  // THE CARDS SIT IN THE SHEET'S OWN FRAME, `.nx-sheet` and `.nx-sheet-body`
  // (next.css): the slot's full height and the 16 px gutter every other sheet
  // gets from `Sheet`. The slot is a bare `display: flex` row, so a wrapper
  // with no frame shrank to its card and put it on the screen's corner.
  // Measured on the real page (routes_s4_frame.json, 2026-09-28): the waiting
  // card at x = 0, y = 0, its dashed border on the top and left edges of the
  // phone, 354 px wide or 390, as its text happened to wrap. `.nx-sheet` sets
  // no width either, so `flex: 1` makes the frame the slot's width whatever
  // the card's text. The frame holds the loading card too. The modal portals
  // out of it (see the header), so under the modal the frame is empty.
  return (
    <div className="nx-sheet" style={{ flex: 1 }} data-testid="session-flow-frame">
      <div className="nx-sheet-body">
        {open !== "" && open !== recordId ? (
          <EmptyCard
            title="FRAME" hint={recordId === null ? FRAME_FLOW_LOADING : FRAME_OTHER_FLOW}
            data-testid="flow-frame-other-flow"
            action={<ActionButton kind="secondary" onPress={closeFrame} data-testid="flow-frame-other-flow-back">BACK</ActionButton>}
          />
        ) : (
          <Suspense fallback={<EmptyCard title="FRAME" hint={FRAME_LOADING} />}>
            <TargetFramingSheetLazy nodeId={params.node ?? ""} onClose={closeFrame} viewOnly={running} />
          </Suspense>
        )}
      </div>
    </div>
  );
}

export default FlowFrameSheet;
