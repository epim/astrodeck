// FlowNodeCard.tsx — one node on the graph.
//
// THIS IS THE COMPONENT THE RE-RENDER DISCIPLINE EXISTS FOR. The README's rule
// is that a `flow.node` status tick re-renders ONE node, not the canvas, and
// §B.3 spells out how that holds: the canvas is the only subscriber to the node
// ARRAY and passes each node down as a prop, while the card subscribes to its
// own status and its own selected-ness with selectors that return a PRIMITIVE.
// zustand compares the selector's result with Object.is, so a status write for
// node B cannot wake node A. `memo` below then stops the canvas's own re-renders
// from walking every card.
//
// Never reach for a `useFlowNode(id)` shape here (§B.3 marks it a trap): it
// returns the node object, which is reference-equal only for as long as every
// writer maps just the node it touched. One careless `nodes.map(n => ({...n}))`
// anywhere would re-render every card on every edit, with nothing failing.
//
// Two independent booleans describe where the card is drawn, and they are NOT
// the same question:
//   phone — the DEVICE tier. 150px wide, no footer. True on both phone tabs.
//   auto  — the phone FLOW tab's auto-graph only. No drag, tap-to-wire ports.
// The phone CANVAS tab is `phone && !auto`; README §5 calls it "the full editor".
import { memo } from "react";
import type { CSSProperties, PointerEvent as RPointerEvent, MouseEvent as RMouseEvent } from "react";
import { useStore } from "../../store";
import { Led } from "../ui";
import type { LedState } from "../../types";
import { NODE_DEFS } from "./nodeDefs";
import { nodeW, type PortDir } from "./geometry";
import { nodeLossDetail, nodeLossLevel } from "./flowsTypes";
import type { FlowNodeRec, FlowNodeStatus } from "./flowsTypes";
import FlowPort from "./FlowPort";

/** §C.5: `idle→"off"`, `busy→"busy"`, `ok→"on"`, `warn→"warn"`, `bad→"bad"`.
 *
 *  The shipped `.led-*` are reused rather than re-cut at the design's 9px
 *  (§G-16 is open on that 2px delta). Reuse is what buys the Do-not list's
 *  "everything meaningful survives prefers-reduced-motion": `.led-on` carries a
 *  checkmark and `.led-off` is a 2px dash, so ok / busy / idle stay tellable
 *  apart with every animation and every colour removed. */
const LED_BY_STATUS: Record<FlowNodeStatus, LedState> = {
  idle: "off", busy: "busy", ok: "on", warn: "warn", bad: "bad",
};

/** `flows.statuses` is a loose `Record<string, string>` because it is filled
 *  from a WS frame. Anything the vocabulary does not know reads as idle — an
 *  unknown word must not blank the LED, which would look like "no node here". */
function asStatus(raw: string | undefined): FlowNodeStatus {
  return raw === "busy" || raw === "ok" || raw === "warn" || raw === "bad"
    ? raw : "idle";
}

export interface FlowNodeCardProps {
  node: FlowNodeRec;
  /** Device tier. 150px card and no footer line on phone (§C.5: "device-based,
   *  not tab-based"). */
  phone?: boolean;
  /** The phone FLOW tab's auto-graph. Suppresses the header drag entirely and
   *  switches the ports to tap-to-wire. */
  auto?: boolean;
  /** World position. Defaults to the node's own coordinates; the auto-graph
   *  passes `computeAutoLayout()`'s instead, which is why this is a prop and not
   *  read off `node` unconditionally. */
  x?: number;
  y?: number;
  /** Header drag, canvas only. The canvas owns the world-space pointer maths,
   *  so it hands down a handler rather than the card computing anything.
   *  Take a STABLE callback (useCallback) — an inline arrow defeats `memo`. */
  onStartDrag?: (nodeId: string, e: RPointerEvent) => void;
  onStartWire?: (nodeId: string, portId: string, e: RPointerEvent) => void;
  onTapPort?: (nodeId: string, portId: string, dir: PortDir) => void;
}

function FlowNodeCard({
  node, phone = false, auto = false, x, y,
  onStartDrag, onStartWire, onTapPort,
}: FlowNodeCardProps) {
  // The two subscriptions this whole file exists to keep narrow. Both return a
  // primitive, so both are exact under Object.is.
  const status = useStore((s) => asStatus(s.flows.statuses[node.id]));
  const selected = useStore((s) =>
    s.flows.sel?.kind === "node" && s.flows.sel.id === node.id);
  // THE THIRD, and it obeys the same rule: `nodeLossLevel` returns a string or
  // null, never a fresh object, so a compile that changes nothing for this type
  // does not wake this card. See flowsTypes.ts for why notes are excluded.
  const loss = useStore((s) => nodeLossLevel(s.flows.compiled?.unmapped, node.type));
  const lossWhy = useStore((s) => nodeLossDetail(s.flows.compiled?.unmapped, node.type));

  // Actions are stable references on the store, so selecting them costs nothing.
  const select = useStore((s) => s.flowsSelect);
  const setEditNode = useStore((s) => s.flowsSetEditNode);

  const def = NODE_DEFS[node.type];
  const w = nodeW(phone ? "phone" : "desktop");
  const px = x ?? node.x;
  const py = y ?? node.y;

  // SELECTION OUTRANKS STATUS (§C.5) — a selected busy node reads as selected.
  // Both are token-derived: the prototype's literal rgba(0,210,255,·) values are
  // `--accent` at various mixes, and its idle rgba(120,140,200,0.22) is `--line`
  // at 0.18 (a 4% delta the contract authorises; §G-14 is open on it).
  //
  // A LOSS SITS BELOW BOTH, and above idle. Selection is what the operator is
  // doing right now and busy is what the rig is doing right now; a loss is a
  // standing fact about the graph, true whether or not anyone is looking. It
  // still has to be visible at rest, which is the case this was missing: the
  // list existed in the inspector and behind the RUN confirm, and the canvas —
  // the surface the operator is actually reading — said nothing.
  const lossVar = loss === "danger" ? "--bad" : "--warn";
  const borderColor = selected ? "var(--accent)"
    : status === "busy" ? "color-mix(in srgb, var(--accent) 55%, transparent)"
    : loss ? `color-mix(in srgb, var(${lossVar}) 70%, transparent)`
    : "var(--line)";
  const boxShadow = selected
    ? "0 0 16px color-mix(in srgb, var(--accent) 35%, transparent)"
    : status === "busy"
      ? "0 0 14px color-mix(in srgb, var(--accent) 25%, transparent)"
      : loss
        ? `0 0 12px color-mix(in srgb, var(${lossVar}) 22%, transparent), 0 4px 14px rgba(0,0,0,0.45)`
        : "0 4px 14px rgba(0,0,0,0.45)";

  const cardStyle: CSSProperties = {
    width: w,
    transform: `translate3d(${px}px,${py}px,0)`,
    borderColor,
    boxShadow,
  };

  // A missing def means the client's vocabulary and the server's have drifted —
  // the exact risk nodeDefs.ts's header names (§G-4: two hand-maintained copies
  // of one table, no endpoint serving it). TypeScript says it cannot happen;
  // models.py's permissive validation says it can arrive anyway. Drawing the
  // raw type is the least-committal rendering: the node stays visible and
  // countable, and its lack of ports is the honest report of what we know about
  // it. The design specifies no such state.
  if (!def) {
    return (
      <div
        data-node-id={node.id}
        data-node-type={node.type}
        onClick={(e: RMouseEvent) => {
          e.stopPropagation();
          select({ kind: "node", id: node.id });
        }}
        className="absolute left-0 top-0 rounded-xl bg-panel border"
        style={cardStyle}
      >
        <div className="flex items-center gap-[7px] h-8 px-[9px]">
          <span className="flex-1 min-w-0 truncate font-mono text-[9.5px] text-faint">
            {node.type}
          </span>
        </div>
      </div>
    );
  }

  return (
    <div
      data-node-id={node.id}
      data-node-type={node.type}
      onClick={(e: RMouseEvent) => {
        // Without this the click reaches the canvas background, which deselects
        // — i.e. tapping a node would clear the selection it just made.
        e.stopPropagation();
        select({ kind: "node", id: node.id });
      }}
      className="absolute left-0 top-0 rounded-xl bg-panel border backdrop-blur-[8px]"
      style={cardStyle}
    >
      {/* header — the drag handle everywhere except the auto-graph */}
      <div
        onPointerDown={auto ? undefined : (e) => onStartDrag?.(node.id, e)}
        className={`flex items-center gap-[7px] h-8 px-[9px]
                    border-b border-[color-mix(in_srgb,var(--line)_78%,transparent)]
                    ${auto ? "" : "cursor-grab"}`}
      >
        {/* The category swatch. PER-NODE, not per-category: ABORT + PARK is
            --bad while the other ACTIONs are --warn, because the one node that
            parks the mount and warms the camera must not look like a message. */}
        <span
          className="w-[7px] h-[7px] rounded-[2px] flex-none"
          style={{
            background: `var(${def.colorVar})`,
            boxShadow: `0 0 6px var(${def.colorVar})`,
          }}
          aria-hidden
        />
        <span className="flex-1 min-w-0 truncate font-display font-semibold
                         text-[9.5px] tracking-[0.14em] text-ink">
          {def.label}
        </span>
        {/* The status word is the LED's accessible name, so the state is
            readable without the colour and without the silhouette. */}
        {/* NOT HONOURED BY A RUN, on the node it belongs to.
            A GLYPH AND A NAME, not a colour: the night palette collapses warn
            and bad toward coral (FlowInspector's ISSUE_INK says the same), so
            an amber ring alone is indistinguishable from a red one — and from
            nothing at all under prefers-reduced-motion or a colourblind eye.
            `title` carries to_plan's own sentence, which already names the
            setting and what the run does instead. */}
        {loss && (
          <span
            data-node-loss={loss}
            title={lossWhy}
            aria-label={`settings not honoured by a run: ${lossWhy}`}
            className={`flex-none font-display font-semibold text-[10px] leading-none
                        ${loss === "danger" ? "text-bad" : "text-warn"}`}
          >
            !
          </span>
        )}
        <Led state={LED_BY_STATUS[status]} label={status} />
        <EditGlyph
          size={auto ? 24 : 20}
          glyph={auto ? 13 : 12}
          onEdit={() => {
            // Selecting as well as opening matches the prototype: the sheet and
            // the desktop inspector both render the SELECTED node, so opening
            // the sheet on a node that is not selected would show another one's
            // parameters.
            select({ kind: "node", id: node.id });
            setEditNode(node.id);
          }}
        />
      </div>

      {/* ports — every input, then every output. Never interleaved (§C.5), which
          is also what geometry.portPos() assumes when it places an output's row
          at `ins.length + idx`. */}
      <div className={phone ? "pt-[5px] pb-1" : "pt-[5px] pb-0.5"}>
        {def.ins.map((p) => (
          <FlowPort key={p.id} nodeId={node.id} port={p} dir="in"
                    auto={auto} onStartWire={onStartWire} onTapPort={onTapPort} />
        ))}
        {def.outs.map((p) => (
          <FlowPort key={p.id} nodeId={node.id} port={p} dir="out"
                    auto={auto} onStartWire={onStartWire} onTapPort={onTapPort} />
        ))}
      </div>

      {/* footer — the params in one line, hidden on phone in BOTH tabs. It is
          computed from the CURRENT params, so an edit shows on the card without
          opening anything. */}
      {!phone && (
        <div className="px-2.5 pt-[3px] pb-2 font-mono text-[9.5px] text-faint truncate">
          {def.sum(node.params)}
        </div>
      )}
    </div>
  );
}

/** The ✎.
 *
 *  Not an `IconButton`: the shipped `IconName` union has no pencil, folder,
 *  magnifier or chevron, and §G-9 — which is OPEN — is the question of whether
 *  to nominate substitutes from the 42 or author new glyphs. Picking one here
 *  would close it. README §Assets calls the prototype's pencil "a stand-in for
 *  its edit glyph", so the stand-in is what renders until that is ruled on; the
 *  path is the prototype's, verbatim.
 *
 *  `data-flows-edit` is load-bearing: flows_visual_check.py:438 hovers the node
 *  and clicks this control to open the edit sheet, and reports "the edit sheet
 *  has no opener the harness can find" if it is missing.
 *
 *  ⚠ 20px (24px on the auto-graph) is below the 44px touch floor. §G-20 covers
 *  exactly this control among the sub-44px graph affordances and is open — the
 *  design's sizes are reproduced rather than bought back InfoDot-style, because
 *  either choice would settle it. */
function EditGlyph({ size, glyph, onEdit }: {
  size: number; glyph: number; onEdit: () => void;
}) {
  return (
    <button
      type="button"
      data-flows-edit
      aria-label="Edit parameters"
      title="Edit parameters"
      onClick={(e: RMouseEvent) => { e.stopPropagation(); onEdit(); }}
      // THE ✎ NEVER DRAGS. Without this the pointerdown bubbles to the header,
      // which starts a node drag, and the click that follows lands after the
      // card has moved under the finger.
      onPointerDown={(e: RPointerEvent) => e.stopPropagation()}
      className="flex-none flex items-center justify-center rounded-[5px] p-0
                 border-0 bg-transparent text-faint cursor-pointer
                 hover:text-accent hover:bg-accent/10"
      style={{ width: size, height: size }}
    >
      <svg
        width={glyph} height={glyph} viewBox="0 0 24 24" fill="none"
        stroke="currentColor" strokeWidth={1.9}
        strokeLinecap="round" strokeLinejoin="round" aria-hidden
      >
        <path d="M17 3a2.8 2.8 0 0 1 4 4L7.5 20.5 2 22l1.5-5.5Z" />
      </svg>
    </button>
  );
}

export default memo(FlowNodeCard);
