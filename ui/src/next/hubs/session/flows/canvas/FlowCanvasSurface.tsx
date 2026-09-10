// FlowCanvasSurface.tsx - the pan/zoom/wire surface the graph is drawn on
// (wave R7 parity row A3). Tablet and desktop only; the phone's editing path is
// `FlowStagesPhoneSheet`.
//
// WHY THIS IS NOT A SECOND POINTER SYSTEM. `components/atlas/SkyCanvas.tsx` is
// the house model and has already paid for every trap this file would otherwise
// re-discover. Five of its guards are reproduced below and each one is a bug
// someone shipped:
//
//   * `if (e.button !== 0) return` - a right or middle press starts a drag whose
//     release the native context menu eats, so nothing ever ends it.
//   * `window.blur` + `visibilitychange` - alt-tab, an OS app switch and a screen
//     timeout take the gesture away with NO pointer event at all.
//   * `buttons === 0 && pointerType !== "touch"` self-heal - a latch left by a
//     release we never saw ends on the first hover. A pending wire following the
//     cursor with no finger on the glass is exactly that latch.
//   * `pointercancel` ends the gesture and resolves NOTHING - the browser taking
//     the gesture is not a release, so it must not drop a wire.
//   * a NON-PASSIVE wheel listener - React registers `onWheel` as passive and
//     silently ignores `preventDefault()`, which is how the page scrolls out
//     from under a zooming canvas.
//
// The one deliberate divergence from SkyCanvas: it uses `setPointerCapture`,
// this uses WINDOW-level pointermove/pointerup. Nothing is lost - pointer
// capture redirects EVENTS, not `elementFromPoint` hit-testing, which is what
// resolves a wire drop.
//
// WHERE THE GESTURES COME FROM. This file owns the world-space maths, so the
// stage card and the port row report a grab and let it do the rest. The
// background's own press lands on the GRID div. That split is why a press on the
// pencil (which stops propagation) drags nothing, and why an input port - which
// gets no `onPointerDown` at all - cannot start a reverse drag.

import { useCallback, useEffect, useRef, type JSX, type PointerEvent as RPointerEvent } from "react";
import { useShallow } from "zustand/react/shallow";

import { clampZoom, type FlowTier, type PortDir } from "../../../../../components/flows/geometry";
import type { PortKind } from "../../../../../components/flows/flowsTypes";
import { useStore } from "../../../../../store";
import { useBreakpoint } from "../../../../breakpoint";
import { nav } from "../../../../router";
import { ActionButton } from "../../../../ui";
import { portKind, resolveWireDrop } from "./canvasModel";
import { setMountedFlowCanvas } from "./canvasMount";
import { FlowNodeCard } from "./FlowNode";
import { FlowWireDelete, FlowWireLayer } from "./FlowWires";
import { FlowZoomCluster } from "./FlowZoomCluster";
import { FlowLogStrip } from "./FlowLogStrip";
import "./canvas.css";

// ------------------------------------------------------------------ constants

/** Fixed 10% per wheel notch, ignoring `deltaMode` and magnitude. A trackpad's
 *  400-unit fling and a mouse's one click are the same step on purpose: scaling
 *  by `deltaY` makes a trackpad unusable. */
const WHEEL_IN = 1.1;
const WHEEL_OUT = 0.9;

/** The label the design gives the add control. */
export const ADD_STAGE_LABEL = "+ ADD STAGE";

// ------------------------------------------------------------------ gestures

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

// ----------------------------------------------------------------- component

export interface FlowCanvasSurfaceProps {
  /** Device tier. Defaults to the live breakpoint. Load-bearing three ways: card
   *  width (150 vs 188), every output wire anchor (`node.x + nodeW`), and
   *  whether `touch-action: none` is applied. */
  tier?: FlowTier;
  /** Show the floating add control. Defaults to true at tablet, where no palette
   *  rail fits; at desktop the composing screen mounts the rail instead. Pass it
   *  explicitly rather than letting a composition end up with two ways to add a
   *  stage, or none. */
  showAddStage?: boolean;
  /** What the add control does. Defaults to opening the palette sheet. */
  onAddStage?: () => void;
}

export function FlowCanvasSurface({ tier, showAddStage, onAddStage }: FlowCanvasSurfaceProps): JSX.Element {
  const bp = useBreakpoint();
  const t: FlowTier = tier ?? bp;

  // Subscriptions, all narrow. This is the ONE subscriber to the node array and
  // it passes each stage down as a prop; the cards are memo'd and read only
  // their own status. That is what makes a `flow.node` tick re-render one card
  // instead of the graph.
  const nodes = useStore((s) => s.flows.graph.nodes);
  const edges = useStore((s) => s.flows.graph.edges);
  const pan = useStore(useShallow((s) => s.flows.pan));
  const zoom = useStore((s) => s.flows.zoom);
  // A string id, not the `sel` object - selecting a STAGE must not re-render the
  // delete-control branch.
  const selEdgeId = useStore((s) => (s.flows.sel?.kind === "edge" ? s.flows.sel.id : null));

  const setPan = useStore((s) => s.flowsSetPan);
  const setZoom = useStore((s) => s.flowsSetZoom);
  const fitTo = useStore((s) => s.flowsFit);
  const select = useStore((s) => s.flowsSelect);
  const moveNode = useStore((s) => s.flowsMoveNode);
  const beginWire = useStore((s) => s.flowsBeginWire);
  const moveWire = useStore((s) => s.flowsMoveWire);
  const endWire = useStore((s) => s.flowsEndWire);
  const deleteSel = useStore((s) => s.flowsDeleteSel);
  const enqueueToast = useStore((s) => s.enqueueToast);

  const boxRef = useRef<HTMLDivElement | null>(null);
  const gestureRef = useRef<Gesture>(null);
  const pinchRef = useRef<Pinch | null>(null);
  /** Live pointers on the BACKGROUND. Two of them is a pinch. */
  const touchesRef = useRef(new Map<number, { x: number; y: number }>());

  // Mirrors, so every imperative handler reads the CURRENT viewport without
  // being re-created (and re-attached) on each pan.
  const panRef = useRef(pan);
  panRef.current = pan;
  const zoomRef = useRef(zoom);
  zoomRef.current = zoom;

  useEffect(() => {
    setMountedFlowCanvas(boxRef.current);
    return () => setMountedFlowCanvas(null);
  }, []);

  // ---------------------------------------------------------- world maths

  /** Client (screen) point to world point. Stable: it reads refs only. */
  const toWorld = useCallback((cx: number, cy: number) => {
    const r = boxRef.current?.getBoundingClientRect();
    const p = panRef.current;
    const z = zoomRef.current;
    return { x: (cx - (r?.left ?? 0) - p.x) / z, y: (cy - (r?.top ?? 0) - p.y) / z };
  }, []);

  const kindOf = useCallback(
    (nodeId: string, portId: string, dir: PortDir): PortKind | null =>
      portKind(useStore.getState().flows.graph.nodes, nodeId, portId, dir),
    [],
  );

  // ------------------------------------------------- the one gesture exit

  /** EVERY path that can end a gesture routes through here - pointerup,
   *  pointercancel, the window going away, the tab being hidden, and a hover
   *  that arrives with no button held. A gesture that is never ended keeps
   *  dragging a stage under an unpressed pointer, and a pending wire that is
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

  // ------------------------------------------------------- gesture starts

  /** Background press: pan, or the second finger of a pinch. Lives on the grid
   *  div, which is a SIBLING of the world layer - a press on a stage never
   *  reaches it, which is why stage drags need their own seam. */
  const onBgDown = useCallback((e: RPointerEvent<HTMLDivElement>) => {
    if (e.button !== 0) return;
    // A second pointer landing on the background must not hijack a stage drag or
    // a wire in flight: overwriting the gesture would abandon the wire with no
    // pointerup left to resolve or clear it. A pan IS overwritable - that is the
    // first finger of a pinch.
    const held = gestureRef.current;
    if (held && held.kind !== "pan") return;
    const touches = touchesRef.current;
    touches.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (touches.size === 2) {
      const [a, b] = [...touches.values()];
      const r = boxRef.current?.getBoundingClientRect();
      // A pinch supersedes the pan the first finger started: otherwise the graph
      // slides toward whichever finger happens to report first.
      gestureRef.current = null;
      pinchRef.current = {
        d0: Math.hypot(a.x - b.x, a.y - b.y) || 1,
        z0: zoomRef.current,
        pan0: { ...panRef.current },
        mid0: { x: (a.x + b.x) / 2 - (r?.left ?? 0), y: (a.y + b.y) / 2 - (r?.top ?? 0) },
      };
      return;
    }
    gestureRef.current = {
      kind: "pan", sx: e.clientX, sy: e.clientY,
      px: panRef.current.x, py: panRef.current.y,
    };
  }, []);

  /** Clicking the background clears the selection. This ALSO fires at the end of
   *  a background drag, because a drag that starts and ends on the same element
   *  still produces a click - which is the prototype's behaviour. */
  const onBgClick = useCallback(() => select(null), [select]);

  /** A stage's header was pressed. Passed to every card; stable, so `memo` on
   *  the card still bails out. */
  const onStartDrag = useCallback((nodeId: string, e: RPointerEvent) => {
    if (e.button !== 0) return;
    const n = useStore.getState().flows.graph.nodes.find((x) => x.id === nodeId);
    if (!n) return;
    // Stops the press reaching anything above and, for touch, stops the browser
    // treating the drag as a scroll or a text selection.
    e.stopPropagation();
    const pt = toWorld(e.clientX, e.clientY);
    // The grab OFFSET, not the position: without it the card jumps so its
    // top-left corner sits under the finger on the first move.
    gestureRef.current = { kind: "node", id: nodeId, dx: pt.x - n.x, dy: pt.y - n.y };
    select({ kind: "node", id: nodeId });
  }, [select, toWorld]);

  /** An OUTPUT port was pressed. Inputs never call this: there is no reverse
   *  drag. */
  const onStartWire = useCallback((nodeId: string, portId: string, e: RPointerEvent) => {
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
      // WORLD coordinates: the pending wire is drawn with the same bezier as a
      // real edge, and a screen-space endpoint would bend differently from the
      // wire it becomes on drop.
      to: toWorld(e.clientX, e.clientY),
    });
  }, [beginWire, kindOf, toWorld]);

  // -------------------------------------------- window move / up / cancel
  // Attached for the surface's whole lifetime rather than per-gesture: the
  // self-heal below has to run on a hover that arrives with NO gesture running
  // in this file's opinion but a latch still held, and a listener that only
  // exists during a gesture cannot see that. Both handlers return on their first
  // line when nothing is in flight.
  useEffect(() => {
    const onMove = (e: PointerEvent): void => {
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
        // Re-anchored on the LIVE midpoint, so the graph under two fingers stays
        // under them even as the fingers travel.
        setZoom(z, {
          x: mid.x - (pinch.mid0.x - pinch.pan0.x) * k,
          y: mid.y - (pinch.mid0.y - pinch.pan0.y) * k,
        });
        return;
      }
      if (!g) return;

      // Self-heal. A mouse or pen move with NO button held cannot belong to a
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
        // Screen-space delta, NOT divided by zoom: the graph tracks the cursor
        // 1:1 at any zoom, which is what makes a pan feel like dragging paper.
        setPan({ x: g.px + e.clientX - g.sx, y: g.py + e.clientY - g.sy });
      } else {
        moveWire(toWorld(e.clientX, e.clientY));
      }
    };

    const onUp = (e: PointerEvent): void => {
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
      if (!res.ok && res.refusal) enqueueToast({ level: "warning", title: res.refusal });
      // One call either way: `flowsEndWire` clears the pending wire and connects
      // only when handed a target, so a refusal cannot leave a wire on screen.
      endWire(res.ok ? { nodeId: res.nodeId, portId: res.portId } : null);
    };

    // pointercancel is the browser TAKING the gesture (a scroll started, a
    // system gesture won). It is not a release, so it resolves no drop.
    const onCancel = (): void => endGesture();
    const onWinBlur = (): void => endGesture();
    const onVis = (): void => {
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

  // ------------------------------------------ wheel zoom, under the cursor
  // A REAL `{ passive: false }` listener. React registers `onWheel` as passive,
  // so `preventDefault()` in a React handler is silently ignored and the page
  // scrolls under the canvas. Attached once - the refs keep it current.
  useEffect(() => {
    const el = boxRef.current;
    if (!el) return;
    const handler = (e: WheelEvent): void => {
      e.preventDefault();
      const r = el.getBoundingClientRect();
      const mx = e.clientX - r.left;
      const my = e.clientY - r.top;
      const z0 = zoomRef.current;
      const z = clampZoom(z0 * (e.deltaY < 0 ? WHEEL_IN : WHEEL_OUT));
      const k = z / z0;
      const p = panRef.current;
      // Keeps the world point under the cursor stationary. The +/- buttons
      // deliberately do NOT do this.
      setZoom(z, { x: mx - (mx - p.x) * k, y: my - (my - p.y) * k });
    };
    el.addEventListener("wheel", handler, { passive: false });
    return () => el.removeEventListener("wheel", handler);
  }, [setZoom]);

  // ------------------------------------------------------------------ fit

  /** Fit-to-view needs the canvas's measured box, which only this file has.
   *  `flowsFit` no-ops on an empty graph rather than yanking the viewport to a
   *  default - "fit nothing" has no meaningful framing. */
  const fit = useCallback(() => {
    const el = boxRef.current;
    if (!el) return;
    fitTo(el.getBoundingClientRect());
  }, [fitTo]);

  // ------------------------------------------------------------- keyboard
  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      // Never while typing. Backspace in a param field must delete a character,
      // not the stage being edited.
      const el = e.target as HTMLElement | null;
      const tag = el?.tagName?.toLowerCase() ?? "";
      if (tag === "input" || tag === "select" || tag === "textarea") return;
      if (el?.isContentEditable) return;
      if (e.key === "Delete" || e.key === "Backspace") {
        // Also stops Backspace navigating back in browsers that still do that.
        e.preventDefault();
        deleteSel();
      }
      if (e.key === "f" || e.key === "F") {
        e.preventDefault();
        fit();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [deleteSel, fit]);

  // --------------------------------------------------------------- render

  const selEdge = selEdgeId ? edges.find((e) => e.id === selEdgeId) : undefined;
  const showAdd = showAddStage ?? t === "tablet";

  return (
    // `nx-flow-fill` is the surface's own height floor, and it is on the
    // element rather than in `.nx-flow-canvas` so that a mount which really
    // does hand this box a height can leave it off. Read `canvas.css`'s host
    // section before removing it: without a height of its own this element is
    // an `overflow: hidden` box of zero height with the whole graph inside it.
    <div
      ref={boxRef}
      data-testid="flows-canvas"
      data-flows-canvas
      className="nx-flow-canvas nx-flow-fill"
      data-tier={t}
    >
      {/* GRID - a SIBLING of the world layer, so it does NOT pan and does NOT
          zoom: a fixed screen-space texture. It also owns the background press
          and the click-to-deselect, which is why it sits under the world rather
          than over it. */}
      <div
        className="nx-flow-grid"
        data-testid="flow-canvas-bg"
        onPointerDown={onBgDown}
        onClick={onBgClick}
      />

      {/* WORLD - one transform for the whole graph. `transform-origin: 0 0` is
          what makes the pan/zoom maths above (and `toWorld`) a plain affine
          pair; centring the origin would put a factor of the canvas size in
          every one of them. */}
      <div
        className="nx-flow-world"
        style={{ transform: `translate3d(${pan.x}px,${pan.y}px,0) scale(${zoom})` }}
      >
        {/* BEFORE the stages, so wires paint UNDER the cards. */}
        <FlowWireLayer tier={t} />
        {nodes.map((n) => (
          <FlowNodeCard
            key={n.id}
            node={n}
            phone={t === "phone"}
            onStartDrag={onStartDrag}
            onStartWire={onStartWire}
          />
        ))}
        {/* Inside the world transform on purpose: the control sits on the wire's
            midpoint and has to scale with it. */}
        {selEdge && <FlowWireDelete edge={selEdge} tier={t} />}
      </div>

      <FlowZoomCluster onFit={fit} />

      {showAdd && (
        <div className="nx-flow-add">
          {/* The label carries its own `+`, and no plus GLYPH is added beside
              it: the design's string is `+ ADD STAGE`, and a glyph plus the
              same character would read "+ + ADD STAGE". */}
          <ActionButton
            kind="purple"
            data-testid="flow-add-stage"
            onPress={onAddStage ?? (() => nav.sheet("flowPalette"))}
          >
            {ADD_STAGE_LABEL}
          </ActionButton>
        </div>
      )}

      <FlowLogStrip />
    </div>
  );
}

export default FlowCanvasSurface;
