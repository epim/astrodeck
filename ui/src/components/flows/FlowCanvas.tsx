// FlowCanvas.tsx — the pan/zoom surface the graph is drawn on. §C.4, all of §D.
//
// WHY THIS IS NOT A SECOND POINTER SYSTEM. §D.1 is explicit that
// `components/atlas/SkyCanvas.tsx` is the model and that inventing another one
// is forbidden, because SkyCanvas has already paid for every trap this file
// would otherwise re-discover. Five of its guards are reproduced below and each
// one is a bug someone shipped:
//
//   * `if (e.button !== 0) return` — a right/middle press starts a drag whose
//     release the native context menu eats, so nothing ever ends it.
//   * `window.blur` + `visibilitychange` — alt-tab, an OS app switch and a
//     screen timeout take the gesture away with NO pointer event at all.
//   * `buttons === 0 && pointerType !== "touch"` self-heal — a latch left by a
//     release we never saw ends on the first hover. A pending wire following the
//     cursor with no finger on the glass is exactly that latch.
//   * `pointercancel` ends the gesture and resolves NOTHING — the browser taking
//     the gesture is not a release, so it must not drop a wire.
//   * a NON-PASSIVE wheel listener — React registers `onWheel` as passive and
//     silently ignores `preventDefault()`, which is how the page scrolls out
//     from under a zooming canvas.
//
// The ONE deliberate divergence from SkyCanvas: it uses `setPointerCapture`,
// this uses WINDOW-level pointermove/pointerup. README §"State management" says
// *"Drag/pan/wire use window-level pointermove/pointerup"* and §D.1 rules the
// README (authority 1) wins. Nothing is lost — pointer capture redirects EVENTS,
// not `elementFromPoint` hit-testing, which is what resolves a wire drop.
//
// WHERE THE GESTURES COME FROM. The canvas owns the world-space maths, so the
// node card and the port row report a grab and let this file do the rest —
// `FlowNodeCard.onStartDrag` and `FlowPort.onStartWire` are the seams. The
// background's own press lands on the GRID div. That split is why a press on the
// ✎ (which stops propagation) drags nothing, and why an input port — which gets
// no `onPointerDown` at all — cannot start a reverse drag (§D.4 rule 2).
import { useCallback, useEffect, useRef } from "react";
import type { CSSProperties, PointerEvent as RPointerEvent } from "react";
import { useShallow } from "zustand/react/shallow";
import { useStore } from "../../store";
import { NODE_DEFS } from "./nodeDefs";
import { clampZoom, nodeW, type FlowTier, type PortDir } from "./geometry";
import type { PortKind } from "./flowsTypes";
import FlowNodeCard from "./FlowNodeCard";
import FlowWireLayer from "./FlowWireLayer";
import FlowWireDelete from "./FlowWireDelete";
import FlowZoomCluster from "./FlowZoomCluster";
import FlowLogStrip from "./FlowLogStrip";

// ------------------------------------------------------------------ constants
/** Fixed ±10% per wheel notch, ignoring `deltaMode` and magnitude (§D.2). A
 *  trackpad's 400-unit fling and a mouse's one click are the same step on
 *  purpose: scaling by `deltaY` makes a trackpad unusable. */
const WHEEL_IN = 1.1;
const WHEEL_OUT = 0.9;

/** §D.2's drop rule: horizontally centred, vertically ABOVE centre (`/2.4`, not
 *  `/2`) so a dropped stage lands in the reading half of the canvas rather than
 *  under the log strip. */
const DROP_Y_DIVISOR = 2.4;

// ------------------------------------------------------- the mounted instance
/** The mounted canvas element.
 *
 *  Exists for exactly one caller: `flowCanvasDropPoint()` below. §D.2 puts a new
 *  stage at the canvas's centre-ish point in WORLD coordinates, and the thing
 *  that adds stages is the palette — which `FlowPalette.PALETTE_FALLBACK_DROP`
 *  documents as unable to see the canvas rect, so it asks its caller for an
 *  `onPick`. The editor sits BETWEEN the two and has no ref to this element
 *  either. A module-scoped handle is the smallest bridge; there is one canvas
 *  mounted at a time by construction (the editor renders one), and it is nulled
 *  on unmount so a stale rect can never outlive the surface. */
let mountedCanvas: HTMLDivElement | null = null;

/** Where §D.2 says a newly-added stage goes, in world coordinates.
 *
 *  `null` when no canvas is mounted — the caller then owns the fallback, and
 *  `FlowPalette.PALETTE_FALLBACK_DROP` is the prototype's own (120,120).
 *
 *  The phone rule is NOT here: outside the CANVAS tab §D.2 appends at
 *  `max(n.x) + 250, 160`, which needs no canvas at all and belongs to whoever
 *  knows which tab is showing. */
export function flowCanvasDropPoint(
  tier: FlowTier,
): { x: number; y: number } | null {
  const el = mountedCanvas;
  if (!el) return null;
  const r = el.getBoundingClientRect();
  const { pan, zoom } = useStore.getState().flows;
  const cx = (r.width / 2 - pan.x) / zoom;
  const cy = (r.height / DROP_Y_DIVISOR - pan.y) / zoom;
  return { x: Math.round(cx - nodeW(tier) / 2), y: Math.round(cy) };
}

// -------------------------------------------------------------- drop decision
/** What a wire drop resolved to. A refusal carries the sentence to toast, or
 *  `null` when the refusal is SILENT — §D.4 rules 3 and 7 both refuse without
 *  saying anything, and they are not the same as "no refusal". */
export type WireDropResult =
  | { ok: true; nodeId: string; portId: string }
  | { ok: false; refusal: string | null };

/** §D.4's eight rules, as one pure function so they can be read in one place
 *  (and exercised without a DOM — `wireDropDom.test.tsx` in §A.1 owns the jsdom
 *  half). `portAttr` is the raw `data-port` value of whatever the pointerup
 *  landed on, or null for a miss.
 *
 *  The three fields are pipe-delimited and UNESCAPED (§D.4), which is why
 *  `flowsSlice.nextNodeId()` mints ids that cannot contain a `|`. */
export function resolveWireDrop(
  wire: { from: string; fromPort: string },
  portAttr: string | null,
  kindOf: (nodeId: string, portId: string, dir: PortDir) => PortKind | null,
): WireDropResult {
  // Rule 7: dropped on empty canvas. Clears the wire, no toast, no edge.
  if (!portAttr) return { ok: false, refusal: null };

  const [nodeId, portId, dir] = portAttr.split("|");
  // Rule 2: only an INPUT accepts a drop. There is no reverse drag.
  if (dir !== "in" || !nodeId || !portId) return { ok: false, refusal: null };
  // Rule 3: self-wiring is refused SILENTLY on drag. (Tap-to-wire toasts
  // "Can't wire a stage to itself" — §C.15 — and the asymmetry is deliberate:
  // a drag that ends where it started is usually a mis-grab, not an attempt.)
  if (nodeId === wire.from) return { ok: false, refusal: null };

  // Rule 4: the lanes are the grammar. A flow port carries the single run
  // cursor, an event port fires any number of times, and nodes.py runs them
  // through different machinery — so the mismatch is refused, with a sentence.
  const kOut = kindOf(wire.from, wire.fromPort, "out");
  const kIn = kindOf(nodeId, portId, "in");
  if (kOut && kIn && kOut !== kIn) {
    return {
      ok: false,
      refusal:
        `${kOut === "flow" ? "Flow" : "Event"} output can't feed ` +
        `${kIn === "flow" ? "a flow" : "an event"} input`,
    };
  }
  // Rule 5 (single-occupancy input) and rule 8 (no toast on success) are the
  // store's: `flowsConnect` filters the incumbent out before it concats.
  return { ok: true, nodeId, portId };
}

// ------------------------------------------------------------------ component
export interface FlowCanvasProps {
  /** Device tier, resolved once by `FlowsView` and threaded through the editor.
   *  Load-bearing three ways: card width (150 vs 188), every output wire anchor
   *  (`node.x + nodeW`), and whether `touch-action: none` is applied. */
  tier: FlowTier;
}

/** One gesture at a time, held in a ref so a pointermove writes no React state
 *  of its own. `pan` and `node` carry their anchor; `wire` carries nothing
 *  because the pending wire lives in the store, where the wire layer reads it. */
type Gesture =
  | { kind: "pan"; sx: number; sy: number; px: number; py: number }
  | { kind: "node"; id: string; dx: number; dy: number }
  | { kind: "wire" }
  | null;

interface Pinch {
  d0: number;
  z0: number;
  pan0: { x: number; y: number };
  mid0: { x: number; y: number };
}

export default function FlowCanvas({ tier }: FlowCanvasProps) {
  // ── subscriptions, all narrow (§B.3) ────────────────────────────────────
  // The canvas is the ONE subscriber to the node array and passes each node down
  // as a prop; the cards are memo'd and read only their own status. That is what
  // makes a `flow.node` tick re-render one card instead of the graph.
  const nodes = useStore((s) => s.flows.graph.nodes);
  const edges = useStore((s) => s.flows.graph.edges);
  const pan = useStore(useShallow((s) => s.flows.pan));
  const zoom = useStore((s) => s.flows.zoom);
  // A string id, not the `sel` object — selecting a NODE must not re-render the
  // canvas's delete-control branch.
  const selEdgeId = useStore((s) =>
    s.flows.sel?.kind === "edge" ? s.flows.sel.id : null);
  const phoneTab = useStore((s) => s.flows.ui.phoneTab);

  const setPan = useStore((s) => s.flowsSetPan);
  const setZoom = useStore((s) => s.flowsSetZoom);
  const fitTo = useStore((s) => s.flowsFit);
  const select = useStore((s) => s.flowsSelect);
  const moveNode = useStore((s) => s.flowsMoveNode);
  const beginWire = useStore((s) => s.flowsBeginWire);
  const moveWire = useStore((s) => s.flowsMoveWire);
  const endWire = useStore((s) => s.flowsEndWire);
  const deleteSel = useStore((s) => s.flowsDeleteSel);
  const setUi = useStore((s) => s.flowsSetUi);
  const enqueueToast = useStore((s) => s.enqueueToast);

  const boxRef = useRef<HTMLDivElement | null>(null);
  const gestureRef = useRef<Gesture>(null);
  const pinchRef = useRef<Pinch | null>(null);
  /** Live pointers on the BACKGROUND. Two of them is a pinch. */
  const touchesRef = useRef(new Map<number, { x: number; y: number }>());

  // Mirrors, so every imperative handler reads the CURRENT viewport without
  // being re-created (and re-attached) on each pan. §D.1's wheel snippet is the
  // same pattern for the same reason.
  const panRef = useRef(pan);
  panRef.current = pan;
  const zoomRef = useRef(zoom);
  zoomRef.current = zoom;

  useEffect(() => {
    mountedCanvas = boxRef.current;
    return () => {
      if (mountedCanvas === boxRef.current) mountedCanvas = null;
    };
  }, []);

  // ── world coordinates ───────────────────────────────────────────────────
  /** Client (screen) point → world point. Stable: it reads refs only. */
  const toWorld = useCallback((cx: number, cy: number) => {
    const r = boxRef.current?.getBoundingClientRect();
    const p = panRef.current;
    const z = zoomRef.current;
    return {
      x: (cx - (r?.left ?? 0) - p.x) / z,
      y: (cy - (r?.top ?? 0) - p.y) / z,
    };
  }, []);

  /** A port's lane, read off the live graph. Nullable on purpose: a saved graph
   *  can name a port the vocabulary has since dropped, and "unknown" must not
   *  be silently treated as a matching lane. */
  const kindOf = useCallback(
    (nodeId: string, portId: string, dir: PortDir): PortKind | null => {
      const n = useStore.getState().flows.graph.nodes.find((x) => x.id === nodeId);
      const def = n ? NODE_DEFS[n.type] : undefined;
      if (!def) return null;
      const p = (dir === "in" ? def.ins : def.outs).find((q) => q.id === portId);
      return p ? p.kind : null;
    },
    [],
  );

  // ── the one exit from a held gesture ────────────────────────────────────
  /** EVERY path that can end a gesture routes through here — pointerup,
   *  pointercancel, the window going away, the tab being hidden, and a hover
   *  that arrives with no button held. A gesture that is never ended keeps
   *  dragging a node under an unpressed pointer, and a pending wire that is
   *  never cleared follows the cursor across a canvas nobody is touching. */
  const endGesture = useCallback(() => {
    gestureRef.current = null;
    pinchRef.current = null;
    touchesRef.current.clear();
    // Only when there IS one: `flowsEndWire` replaces the `flows` object, so
    // calling it unconditionally would re-render every subscriber on every
    // alt-tab.
    if (useStore.getState().flows.wire) endWire(null);
  }, [endWire]);

  // ── gesture starts ──────────────────────────────────────────────────────
  /** Background press: pan, or the second finger of a pinch. Lives on the grid
   *  div, which is a SIBLING of the world layer — a press on a node never
   *  reaches it, which is why node drags need their own seam. */
  const onBgDown = useCallback((e: RPointerEvent<HTMLDivElement>) => {
    if (e.button !== 0) return;
    // A second pointer landing on the background must not hijack a node drag or
    // a wire in flight — overwriting the gesture would abandon the wire with no
    // pointerup left to resolve or clear it, which is the exact latch §D.1
    // names. A pan IS overwritable: that is the first finger of a pinch.
    const held = gestureRef.current;
    if (held && held.kind !== "pan") return;
    const touches = touchesRef.current;
    touches.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (touches.size === 2) {
      const [a, b] = [...touches.values()];
      const r = boxRef.current?.getBoundingClientRect();
      // A pinch supersedes the pan the first finger started, exactly as the
      // prototype does (`this.panD = null`): otherwise the graph slides toward
      // whichever finger happens to report first.
      gestureRef.current = null;
      pinchRef.current = {
        d0: Math.hypot(a.x - b.x, a.y - b.y) || 1,
        z0: zoomRef.current,
        pan0: { ...panRef.current },
        mid0: {
          x: (a.x + b.x) / 2 - (r?.left ?? 0),
          y: (a.y + b.y) / 2 - (r?.top ?? 0),
        },
      };
      return;
    }
    gestureRef.current = {
      kind: "pan",
      sx: e.clientX,
      sy: e.clientY,
      px: panRef.current.x,
      py: panRef.current.y,
    };
  }, []);

  /** Clicking the background clears the selection.
   *
   *  ⚠ This ALSO fires at the end of a background drag, because a drag that
   *  starts and ends on the same element still produces a click. §D.2 names that
   *  as the prototype's behaviour and says to reproduce it. */
  const onBgClick = useCallback(() => select(null), [select]);

  /** A node's header was pressed. Passed to every card; stable, so `memo` on
   *  `FlowNodeCard` still bails out. */
  const onStartDrag = useCallback(
    (nodeId: string, e: RPointerEvent) => {
      if (e.button !== 0) return;
      const n = useStore.getState().flows.graph.nodes.find((x) => x.id === nodeId);
      if (!n) return;
      // Stops the press reaching anything above and, for touch, stops the
      // browser treating the drag as a scroll or a text selection.
      e.stopPropagation();
      const pt = toWorld(e.clientX, e.clientY);
      // The grab OFFSET, not the position: without it the card jumps so its
      // top-left corner sits under the finger on the first move.
      gestureRef.current = { kind: "node", id: nodeId, dx: pt.x - n.x, dy: pt.y - n.y };
      select({ kind: "node", id: nodeId });
    },
    [select, toWorld],
  );

  /** An OUTPUT port was pressed. Inputs never call this — §D.4 rule 2. */
  const onStartWire = useCallback(
    (nodeId: string, portId: string, e: RPointerEvent) => {
      if (e.button !== 0) return;
      const kind = kindOf(nodeId, portId, "out");
      if (!kind) return;
      e.stopPropagation();
      // preventDefault stops the native drag/selection the press would otherwise
      // begin, which would fight the wire for the pointer.
      e.preventDefault();
      gestureRef.current = { kind: "wire" };
      beginWire({
        from: nodeId,
        fromPort: portId,
        kind,
        // WORLD coordinates. The pending wire is drawn with the same bezier as a
        // real edge, and a screen-space endpoint would bend differently from the
        // wire it becomes on drop.
        to: toWorld(e.clientX, e.clientY),
      });
    },
    [beginWire, kindOf, toWorld],
  );

  // ── window-level move / up / cancel ─────────────────────────────────────
  // Attached for the canvas's whole lifetime rather than per-gesture: the
  // self-heal below has to run on a hover that arrives with NO gesture running
  // in this file's opinion but a latch still held, and a listener that only
  // exists during a gesture cannot see that. Both handlers return on their first
  // line when nothing is in flight.
  useEffect(() => {
    const onMove = (e: PointerEvent) => {
      const pinch = pinchRef.current;
      const g = gestureRef.current;
      if (!pinch && !g) return;

      const touches = touchesRef.current;
      if (touches.has(e.pointerId)) touches.set(e.pointerId, { x: e.clientX, y: e.clientY });

      if (pinch && touches.size >= 2) {
        const [a, b] = [...touches.values()];
        const r = boxRef.current?.getBoundingClientRect();
        const d = Math.hypot(a.x - b.x, a.y - b.y) || 1;
        const z = clampZoom(pinch.z0 * (d / pinch.d0));
        const k = z / pinch.z0;
        const mid = {
          x: (a.x + b.x) / 2 - (r?.left ?? 0),
          y: (a.y + b.y) / 2 - (r?.top ?? 0),
        };
        // Re-anchored on the LIVE midpoint, so the sky under two fingers stays
        // under them even as the fingers travel.
        setZoom(z, {
          x: mid.x - (pinch.mid0.x - pinch.pan0.x) * k,
          y: mid.y - (pinch.mid0.y - pinch.pan0.y) * k,
        });
        return;
      }
      if (!g) return;

      // Self-heal. A mouse/pen move with NO button held cannot belong to a
      // gesture, so a latch left by a release we never saw ends on the first
      // hover. Touch is exempt: a finger reports buttons=1 only while it is down
      // and sends no hover moves at all, so the exits below are what serve it.
      if (e.buttons === 0 && e.pointerType !== "touch") {
        endGesture();
        return;
      }

      if (g.kind === "node") {
        const pt = toWorld(e.clientX, e.clientY);
        moveNode(g.id, Math.round(pt.x - g.dx), Math.round(pt.y - g.dy));
      } else if (g.kind === "pan") {
        // Screen-space delta, NOT divided by zoom (§D.2) — the graph tracks the
        // cursor 1:1 at any zoom, which is what makes a pan feel like dragging
        // paper rather than a proxy.
        setPan({ x: g.px + e.clientX - g.sx, y: g.py + e.clientY - g.sy });
      } else {
        moveWire(toWorld(e.clientX, e.clientY));
      }
    };

    const onUp = (e: PointerEvent) => {
      const touches = touchesRef.current;
      touches.delete(e.pointerId);
      if (touches.size < 2) pinchRef.current = null;

      const g = gestureRef.current;
      gestureRef.current = null;
      if (g?.kind !== "wire") return;

      // The drop is resolved on the POINTERUP COORDINATES, against the real
      // element under them. That is what makes it work for touch, where there is
      // no hover and no `e.target` on the port; and it is correct at any zoom
      // without this file duplicating a single line of the port geometry.
      const wire = useStore.getState().flows.wire;
      if (!wire) return;
      const host = document.elementFromPoint?.(e.clientX, e.clientY)?.closest?.("[data-port]");
      const res = resolveWireDrop(wire, host?.getAttribute("data-port") ?? null, kindOf);
      if (!res.ok && res.refusal) {
        enqueueToast({ level: "warning", title: res.refusal });
      }
      // One call either way: `flowsEndWire` clears the pending wire and connects
      // only when handed a target, so a refusal cannot leave a wire on screen.
      endWire(res.ok ? { nodeId: res.nodeId, portId: res.portId } : null);
    };

    // pointercancel is the browser TAKING the gesture (a scroll started, a
    // system gesture won). It is not a release, so it resolves no drop.
    const onCancel = () => endGesture();
    const onWinBlur = () => endGesture();
    const onVis = () => {
      if (document.visibilityState === "hidden") endGesture();
    };

    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    window.addEventListener("pointercancel", onCancel);
    window.addEventListener("blur", onWinBlur);
    document.addEventListener("visibilitychange", onVis);
    return () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("pointercancel", onCancel);
      window.removeEventListener("blur", onWinBlur);
      document.removeEventListener("visibilitychange", onVis);
    };
  }, [endGesture, endWire, enqueueToast, kindOf, moveNode, moveWire, setPan, setZoom, toWorld]);

  // ── wheel zoom, anchored under the cursor ───────────────────────────────
  // A REAL `{ passive: false }` listener. React registers `onWheel` as passive,
  // so `preventDefault()` in a React handler is silently ignored and the page
  // scrolls under the canvas. Attached once — the refs keep it current.
  useEffect(() => {
    const el = boxRef.current;
    if (!el) return;
    const handler = (e: WheelEvent) => {
      e.preventDefault();
      const r = el.getBoundingClientRect();
      const mx = e.clientX - r.left;
      const my = e.clientY - r.top;
      const z0 = zoomRef.current;
      const z = clampZoom(z0 * (e.deltaY < 0 ? WHEEL_IN : WHEEL_OUT));
      const k = z / z0;
      const p = panRef.current;
      // Keeps the world point under the cursor stationary. (The ± buttons
      // deliberately do NOT do this — §D.2, §G-24.)
      setZoom(z, { x: mx - (mx - p.x) * k, y: my - (my - p.y) * k });
    };
    el.addEventListener("wheel", handler, { passive: false });
    return () => el.removeEventListener("wheel", handler);
  }, [setZoom]);

  // ── fit ─────────────────────────────────────────────────────────────────
  /** Fit-to-view needs the canvas's measured box, which only this file has.
   *  `flowsFit` no-ops on an empty graph rather than yanking the viewport to a
   *  default — "fit nothing" has no meaningful framing. */
  const fit = useCallback(() => {
    const el = boxRef.current;
    if (!el) return;
    fitTo(el.getBoundingClientRect());
  }, [fitTo]);

  // ── keyboard ────────────────────────────────────────────────────────────
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      // Never while typing. Backspace in a param field must delete a character,
      // not the node being edited.
      const tag = (e.target as HTMLElement | null)?.tagName?.toLowerCase() ?? "";
      if (tag === "input" || tag === "select" || tag === "textarea") return;
      if (e.key === "Delete" || e.key === "Backspace") {
        // Marked consumed, following Tooltip's convention (ui.tsx:525-529): an
        // outer dismissable can then tell the key was already used. It also
        // stops Backspace navigating back in browsers that still do that.
        e.preventDefault();
        deleteSel();
      }
      // ⚠ `f` = fit has NO design source. It exists because
      // scripts/flows_visual_check.py:441 presses it with the comment
      // "# fit-to-view, per the editor", and §F.4 item 1 lists adding it here as
      // option (a) against changing the harness as option (b). §G-25 / §G-29 are
      // OPEN — this is the option §D.5 assumes, not a ruling.
      if (e.key === "f" || e.key === "F") {
        e.preventDefault();
        fit();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [deleteSel, fit]);

  // ── render ──────────────────────────────────────────────────────────────
  const selEdge = selEdgeId ? edges.find((e) => e.id === selEdgeId) : undefined;
  const phone = tier === "phone";
  // The prototype's `showAddFloat` (line 1543): tablet, OR the phone CANVAS tab.
  // §C.4's snippet abbreviates this to `tier === "tablet"`, but the same snippet
  // knows the phone CANVAS tab renders this component (its `touchAction` line
  // says so), and desktop has the palette rail instead. Dropping the phone half
  // would leave that tab with no way to add a stage at all.
  const showAddFloat = tier === "tablet" || (phone && phoneTab === "canvas");

  const surface: CSSProperties = {
    // Scoped to the phone CANVAS tab ONLY. An unconditional `touch-none` has bitten
    // this app twice (SkyCanvas.tsx:195-224: "two 260px upward swipes on the canvas
    // left main.scrollTop at 0 with 1648px of page still below. The user was
    // stranded on the Atlas."). At tablet/desktop the surface is mouse-driven.
    touchAction: phone && phoneTab === "canvas" ? "none" : undefined,
    // The literal scrim from §C.4, kept literal to match FlowLibrary's §C.2
    // ground exactly — the two surfaces are the same nebula and must not drift.
    // It is a scrim over a fixed bitmap, not a themed surface.
    backgroundImage:
      "linear-gradient(rgba(6,7,11,.86),rgba(6,7,11,.93)),url('/bg_nebula.png')",
    backgroundSize: "cover",
    backgroundPosition: "center",
  };

  return (
    <div ref={boxRef} data-flows-canvas className="flex-1 relative overflow-hidden min-w-0" style={surface}>
      {/* GRID — a SIBLING of the world layer, so it does NOT pan and does NOT
          zoom: a fixed 36px screen-space texture. That is the prototype's
          structure and §G-12 is the open question about whether it should
          instead live in graph space (every capture is at rest, so the pixels
          cannot tell). It also owns the background press and the
          click-to-deselect, which is why it sits under the world rather than
          over it. */}
      <div
        onPointerDown={onBgDown}
        onClick={onBgClick}
        className="absolute inset-0 cursor-grab"
        style={{
          backgroundImage:
            "repeating-linear-gradient(0deg,rgba(120,140,200,0.06) 0 1px,transparent 1px 36px)," +
            "repeating-linear-gradient(90deg,rgba(120,140,200,0.06) 0 1px,transparent 1px 36px)",
        }}
      />

      {/* WORLD — one transform for the whole graph. `transform-origin: 0 0` is
          what makes the pan/zoom maths above (and `toWorld`) a plain affine
          pair; centring the origin would put a factor of the canvas size in
          every one of them. */}
      <div
        className="absolute left-0 top-0"
        style={{
          transform: `translate3d(${pan.x}px,${pan.y}px,0) scale(${zoom})`,
          transformOrigin: "0 0",
        }}
      >
        {/* BEFORE the nodes, so wires paint UNDER the cards. */}
        <FlowWireLayer tier={tier} />
        {nodes.map((n) => (
          <FlowNodeCard
            key={n.id}
            node={n}
            phone={phone}
            onStartDrag={onStartDrag}
            onStartWire={onStartWire}
          />
        ))}
        {/* Inside the world transform on purpose: the ✕ sits on the wire's
            midpoint and has to scale with it. */}
        {selEdge && <FlowWireDelete edge={selEdge} tier={tier} />}
      </div>

      <FlowZoomCluster onFit={fit} />

      {showAddFloat && (
        // Purple border, cyan fill and ink — the same treatment as RUN,
        // GENERATE FLOW and COPY JSON (§C.3). 44px tall, so this one clears the
        // touch floor on its own.
        <button
          type="button"
          onClick={() => setUi({ paletteOpen: true })}
          className="absolute right-3 bottom-11 z-[5] h-11 px-4 rounded-[10px] border
                     border-accent2 bg-accent-fill text-accent cursor-pointer
                     font-display font-semibold text-[11px] tracking-[0.12em]"
        >
          {/* ⚠ The harness looks for `ADD NODE` (flows_visual_check.py:314) and
              the design's label is `+ ADD STAGE`. §F.4 item 2 / §G-28 are OPEN:
              an `aria-label` that replaced this text would break WCAG 2.5.3, so
              the design's words stand and the harness step is the thing to
              settle. */}
          + ADD STAGE
        </button>
      )}

      <FlowLogStrip />
    </div>
  );
}
