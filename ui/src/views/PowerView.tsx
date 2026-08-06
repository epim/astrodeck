import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { useStore, useStatus } from "../store";
import { EmptyState, Led, Panel } from "../components/ui";
import { Icon } from "../components/icons";
import { useCanControlPower } from "../lib/caps";
import ReadOnlyBadge from "../components/ReadOnlyBadge";
import type { SwitchPort } from "../types";

export default function PowerView() {
  const status = useStatus();
  const showToast = useStore((s) => s.showToast);
  const canControl = useCanControlPower(); // viewer => read-only ports
  const [ports, setPorts] = useState<SwitchPort[] | null>(null);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  // Local slider drafts (UX-17): the PWM sliders draft locally while dragging and
  // POST only on release, so the thumb no longer fights the 5s server echo and we
  // don't flood /api/switch/set with a POST-per-tick. `draggingRef` holds the port
  // currently being edited so a refresh mid-drag can't yank the thumb (reconcile
  // effect below), mirroring CaptureView's cooler/dew draft-then-commit pattern.
  const [drafts, setDrafts] = useState<Record<number, number>>({});
  const draggingRef = useRef<number | null>(null);
  // Which ports have a set in flight. A UPB toggle is not a register write: the
  // driver walks the box with 1+5N sequential serial GETs, so the LED, the
  // border and the ON/OFF word all sat unchanged for the whole round trip and
  // the row read as if the tap had missed. Extra taps then queued extra full
  // cycles. Per-port rather than one flag, because tapping port 2 while port 1
  // is still answering is a legitimate thing to do.
  //
  // Held in a REF as well as state: the ref is the guard (a handler that fires
  // twice inside one React batch reads the same stale state value both times),
  // the state is what the rows render from.
  const inFlightRef = useRef<Set<number>>(new Set());
  const [pendingPorts, setPendingPorts] = useState<ReadonlySet<number>>(new Set());
  const publishPending = () => setPendingPorts(new Set(inFlightRef.current));
  // A dimmer level released while that port's walk was still out. The guard
  // above must not send a second walk — but DROPPING the value is invisible:
  // the reconcile effect then clears the draft, the thumb snaps back to the
  // level the box is still holding, and nothing on the row says the second
  // adjustment went nowhere. Hold the LATEST released value per port instead
  // and send it when the walk returns. Last write wins: the intermediate levels
  // of a two-stage adjustment are not worth another 1+5N walk of the box.
  const queuedRef = useRef<Map<number, number>>(new Map());
  const connected = !!status?.connected?.switch?.connected;

  const refresh = async () => {
    try {
      setPorts(await api.get<SwitchPort[]>("/api/switch/ports"));
      setLoadErr(null);
    } catch (e) {
      // UX-18: a failed GET must read as an error, NOT "no switches". Keep any
      // already-loaded ports (a transient poll failure must not blank the view)
      // and surface the reason distinctly — full-panel when we have nothing yet,
      // inline banner when data is already on screen (EquipmentView's idiom).
      setLoadErr(e instanceof Error ? e.message : "couldn't load power ports");
    }
  };

  useEffect(() => {
    if (!connected) { setPorts(null); setLoadErr(null); return; }
    refresh();
    const t = setInterval(refresh, 5000);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [connected]);

  // Reconcile slider drafts with server truth: once a fresh ports snapshot lands,
  // drop the draft for every port that is not still being edited, walked or
  // queued. The commit POST returns the updated ports, so release → commit →
  // clear shows the new value with no snap-back — and a level still waiting
  // behind a walk stays on the thumb until the box actually has it.
  //
  // Per-port rather than all-or-nothing: a walk on port 2 must not blank the
  // thumb a second finger is holding on port 3.
  useEffect(() => {
    setDrafts((d) => {
      let dropped = false;
      const keep: Record<number, number> = {};
      for (const key of Object.keys(d)) {
        const id = Number(key);
        if (id === draggingRef.current || inFlightRef.current.has(id)
            || queuedRef.current.has(id)) keep[id] = d[id];
        else dropped = true;
      }
      return dropped ? keep : d; // same reference when nothing changed
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ports]);

  /** POST one port. `queueIfBusy` remembers the value instead of dropping it. */
  const setPort = async (id: number, value: number, queueIfBusy = false) => {
    if (!canControl) return; // viewer: read-only, controls are inert anyway
    if (inFlightRef.current.has(id)) {
      // Never a second walk of the box while one is out (#78). A slider commit
      // is remembered and re-sent below; a toggle tap is not, because the row
      // is already showing "→ ON" for the walk that IS out and a second tap
      // during it is asking for what is already happening.
      if (queueIfBusy) queuedRef.current.set(id, value);
      return;
    }
    inFlightRef.current.add(id);
    publishPending();
    try { setPorts(await api.post<SwitchPort[]>("/api/switch/set", { port_id: id, value })); }
    catch (e) { showToast("error", (e as Error).message); }
    finally {
      inFlightRef.current.delete(id);
      const queued = queuedRef.current.get(id);
      if (queued !== undefined) {
        queuedRef.current.delete(id);
        // Re-arms inFlightRef SYNCHRONOUSLY (setPort runs to its first await),
        // so the reconcile effect that fires on the ports this POST just
        // returned still sees the port as busy and keeps its draft — otherwise
        // the thumb visibly bounces to the old level between the two walks.
        void setPort(id, queued, queueIfBusy);
      }
      publishPending();
    }
  };

  // Commit a dimmer slider once, on pointer/key release: POST the released value,
  // then let the reconcile effect clear the draft when the echo returns.
  const commitDimmer = (id: number, value: number) => {
    if (draggingRef.current !== id) return; // nothing was edited on this port
    draggingRef.current = null;
    void setPort(id, value, true); // queue behind a walk in flight, never drop
  };
  // A drag the browser takes away — a scroll gesture claiming the pointer, the
  // page being hidden, the capture being lost — delivers pointercancel or
  // lostpointercapture INSTEAD of pointerup, so `commitDimmer` never runs. The
  // port then stayed latched in `draggingRef` forever, which is the one thing
  // the reconcile effect below checks: the thumb sat on a level that was never
  // sent and the 5s poll could not correct it. SlewPad pairs the same three
  // handlers, guarded the same way — pointerup clears the id first, so a normal
  // release reaches this and returns.
  const abandonDimmer = (id: number) => {
    if (draggingRef.current !== id) return;
    draggingRef.current = null;
    // Snap back to the last level that WAS commanded: the one waiting behind an
    // in-flight walk if there is one, otherwise the box's own value (drop the
    // draft). Never leave the thumb on a number nothing was told about.
    const queued = queuedRef.current.get(id);
    setDrafts((d) => {
      const n = { ...d };
      if (queued === undefined) delete n[id]; else n[id] = queued;
      return n;
    });
  };

  if (!connected) {
    return (
      <Panel title="Power & Dew Control">
        <p className="text-dim text-xs py-8 text-center tracking-widest uppercase">
          no power box connected — connect a switch device (Pegasus UPB, etc.) on the Rig page
        </p>
      </Panel>
    );
  }

  // UX-18: first-load failure has no data to fall back to — a DISTINCT error
  // (with retry) instead of the blank/empty "no ports" render (EquipmentView §render).
  if (loadErr && !ports) {
    return (
      <Panel title="Power & Dew Control">
        <EmptyState
          icon="alert"
          title="Couldn't load power ports"
          hint={loadErr}
          action={
            <button type="button" className="btn btn-accent !py-1.5" onClick={() => void refresh()}>
              Retry
            </button>
          }
        />
      </Panel>
    );
  }

  const toggles = ports?.filter((p) => p.can_write && p.is_boolean) ?? [];
  const dimmers = ports?.filter((p) => p.can_write && !p.is_boolean) ?? [];
  const sensors = ports?.filter((p) => !p.can_write) ?? [];

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      {/* UX-18: background-refresh failure with data already shown — a non-destructive
          inline banner + retry (never the full-panel blank above), spanning both cols. */}
      {loadErr && ports && (
        <div className="lg:col-span-2 flex items-center gap-2 border border-bad/50 bg-bad/5 px-3 py-2">
          <Icon name="alert" size={14} className="text-bad shrink-0" />
          <span className="text-sm text-ink flex-1">Couldn't refresh power ports: {loadErr}</span>
          <button type="button" className="btn !py-1 !px-2 text-[11px]" onClick={() => void refresh()}>
            Retry
          </button>
        </div>
      )}
      <Panel title="Power Outputs" right={!canControl && <ReadOnlyBadge />}>
        <div className="flex flex-col gap-2">
          {toggles.map((p) => {
            const pending = pendingPorts.has(p.id);
            // Mid-cycle the LED and the border keep showing the port's REAL
            // state — the box has not switched yet — and the word carries the
            // request, in the "→ target" vocabulary the filter picker already
            // taught (lib/filterSlots). `.blink` is the secondary cue only:
            // prefers-reduced-motion kills it, so the arrow has to be the fact.
            const state = p.value > 0 ? "ON" : "OFF";
            return (
              <button key={p.id}
                disabled={!canControl}
                aria-disabled={!canControl || undefined}
                aria-busy={pending || undefined}
                aria-label={pending
                  ? `${p.name} — switching ${p.value > 0 ? "off" : "on"}, waiting for the power box`
                  : undefined}
                onClick={() => setPort(p.id, p.value > 0 ? 0 : 1)}
                className={`flex items-center gap-3 border px-3 py-3 transition-all text-left
                  ${canControl ? "cursor-pointer" : "cursor-default opacity-60"}
                  ${p.value > 0 ? "border-accent2 bg-accent/5" : "border-line bg-bg/60 " + (canControl ? "hover:border-line2" : "")}`}>
                <Led on={p.value > 0} />
                <span className="text-sm flex-1">{p.name}</span>
                <span className={`label ${pending ? "text-accent blink" : p.value > 0 ? "text-good" : ""}`}>
                  {pending ? `→ ${p.value > 0 ? "OFF" : "ON"}` : state}
                </span>
              </button>
            );
          })}
        </div>
      </Panel>

      <div className="flex flex-col gap-4">
        <Panel title="Dew Heaters · PWM" right={!canControl && <ReadOnlyBadge />}>
          <div className="flex flex-col gap-4">
            {dimmers.map((p) => {
              // Draft wins while THIS port is being dragged, walked or queued;
              // otherwise the server value shows (see the reconcile effect).
              const shown = drafts[p.id] ?? p.value;
              // The same 1+5N serial walk the toggles above narrate, in the same
              // "→ target" vocabulary. A dimmer release used to be silent for
              // the whole round trip, which is what made a second adjustment
              // during it — dropped, until the queue above — look like nothing.
              const pending = pendingPorts.has(p.id);
              return (
                <div key={p.id}>
                  <div className="flex justify-between mb-1.5">
                    <span className="text-xs">{p.name}</span>
                    <span className={`mono text-xs ${pending ? "text-accent blink" : "text-accent"}`}>
                      {pending ? "→ " : ""}{shown.toFixed(0)}{p.unit}
                    </span>
                  </div>
                  {/* The port name beside it is a sibling <span>, not a <label>,
                      so the accname algorithm gave this slider NOTHING — a
                      screen-reader user heard an unnamed range. Phone sweep
                      (S25 Ultra / iPhone Pro Max / 390) reported both dimmers
                      unnamed on every device.

                      `aria-disabled` rather than the native attribute (house
                      rule §11.8): `disabled` strips the control AND its reason
                      from the a11y tree, and a range has no `readOnly` to fall
                      back on, so the handlers are made inert instead. */}
                  <input type="range" min={p.min} max={p.max} value={shown}
                    aria-label={`${p.name} level`}
                    aria-valuetext={pending
                      ? `${shown.toFixed(0)}${p.unit}, waiting for the power box`
                      : `${shown.toFixed(0)}${p.unit}`}
                    aria-busy={pending || undefined}
                    aria-disabled={!canControl || undefined}
                    className={`w-full accent-(--accent) ${canControl ? "cursor-pointer" : "opacity-50 cursor-default"}`}
                    onChange={(e) => {
                      if (!canControl) return;
                      // Drag/keyboard tick: update the local draft only — no POST.
                      draggingRef.current = p.id;
                      const v = Number(e.target.value);
                      setDrafts((d) => ({ ...d, [p.id]: v }));
                    }}
                    onPointerUp={(e) => commitDimmer(p.id, Number(e.currentTarget.value))}
                    onKeyUp={(e) => commitDimmer(p.id, Number(e.currentTarget.value))}
                    onPointerCancel={() => abandonDimmer(p.id)}
                    onLostPointerCapture={() => abandonDimmer(p.id)} />
                </div>
              );
            })}
            {dimmers.length === 0 && <p className="text-dim text-xs">no PWM ports</p>}
          </div>
        </Panel>

        <Panel title="Telemetry">
          <div className="grid grid-cols-2 gap-3">
            {sensors.map((p) => (
              <div key={p.id} className="border border-line bg-bg/60 px-3 py-2.5">
                <div className="label mb-1">{p.name}</div>
                <div className="mono text-lg text-accent">
                  {p.value.toFixed(1)}<span className="text-xs text-dim ml-1">{p.unit}</span>
                </div>
              </div>
            ))}
          </div>
        </Panel>
      </div>
    </div>
  );
}
