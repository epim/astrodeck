// power.tsx - the POWER device sheet (plan hub-rig.md B.2, task T-RIG-2).
//
// This is `views/PowerView.tsx` re-skinned, and the four behaviours it carries
// are the whole reason the file is not a list of switches. All four were bugs
// once, and all four are invisible when they regress:
//
//  1. A UPB TOGGLE IS NOT A REGISTER WRITE. The driver walks the box with 1+5N
//     sequential serial GETs, so for the whole round trip the LED, the border
//     and the ON/OFF word stay on the port's REAL state - and the row narrates
//     the request as "-> ON" through `aria-describedby`, never by swapping the
//     control's accessible NAME out from under a voice command.
//  2. NEVER A SECOND WALK WHILE ONE IS OUT. A tap on a port that is already
//     answering is dropped (the row is already showing the walk that IS out); a
//     dimmer level released during one is QUEUED and sent when the walk returns.
//     Dropping it was invisible: the draft cleared, the thumb snapped back, and
//     nothing said the second adjustment went nowhere.
//  3. DRAFT-THEN-COMMIT DIMMERS. `draggingRef` holds the port being edited so
//     the 5 s poll cannot yank the thumb; the reconcile effect drops a draft
//     only when the port is neither dragging, in flight nor queued; and
//     `pointercancel` / `lostpointercapture` snap the draft back to the last
//     level that was actually commanded.
//  4. `aria-disabled`, NEVER `disabled`. An operator without `control.power`
//     sees every port, its live value and the reason - not a grey rectangle
//     that cannot be focused to ask why.
//
// THE SESSION LOCK IS A HEURISTIC, AND SAYS SO (deviation E13). The design locks
// "mount, camera and USB" while a session runs. `SwitchPort` is
// `{id, name, value, min, max, unit, is_boolean, can_write}` - there is no lock
// flag on the wire, and the prototype hard-codes which ports lock
// (seams/proto/logic.js:112). So the match is by NAME, and the footer note says
// so, because a user whose dew port is called "USB DEW" needs to know why it
// locked and that renaming it on the box is the fix.

import { useCallback, useEffect, useRef, useState, type JSX } from "react";
import type { SheetProps } from "../../sheets";
import {
  ActionButton, BannerCard, Card, EmptyCard, Mono, Sheet,
} from "../../../ui";
import { NxIcon } from "../../../icons";
import { nav } from "../../../router";
import { useLock } from "../../../lib/gateHook";
import { useEquipConnected, useSequence, useStatus, useStore } from "../../../../store";
import { resolveRoleConnected } from "../../../../lib/caps";
import { api } from "../../../../api";
import type { SwitchPort } from "../../../../types";

/** How often the ports are re-read while the role is connected
 *  (views/PowerView.tsx:59-65). Torn down the moment it drops. */
const POLL_MS = 5000;

/** E13. A port whose name matches this is session-critical: cutting it mid-run
 *  ends the run and possibly the mount's alignment with it. */
export const SESSION_CRITICAL = /mount|camera|usb/i;

export function sessionLockReason(name: string): string {
  return `${name} is locked while a run is going. Stop the run on Session - Now first.`;
}

/** The design's sentence plus the one it needs to be actionable. */
export const FOOTER_LOCK_NOTE =
  "Mount, camera and USB are locked while a session runs; stop the session to "
  + "unlock them. Ports are matched by name; rename a port on the power box if "
  + "the wrong one locks.";

/** E24: the fragment claims dew heaters "on auto follow the dew margin from
 *  Weather". Nothing in this engine drives a switch port from the dew margin. */
export const FOOTER_DEW_NOTE =
  "Dew heater ports set here hold their power until you change them.";

/** The shipped empty state, with its tail repointed at the new IA. */
export const NO_BOX_HINT =
  "connect a switch device (Pegasus UPB, etc.) on ADD A DEVICE";

/** The header's live line, from the ports themselves. Every clause is dropped
 *  when no read-only port supplies it - a box that reports no voltage must not
 *  be described as running at 0 V. */
export function powerLiveLine(ports: SwitchPort[] | null, connected: boolean): string {
  if (!connected) return "NOT CONNECTED";
  if (ports == null) return "reading the ports";
  const bits: string[] = [];
  const volts = ports.find((p) => !p.can_write && /^V$/i.test(p.unit.trim()));
  const amps = ports.find((p) => !p.can_write && /^A$/i.test(p.unit.trim()));
  if (volts) bits.push(`${volts.value.toFixed(1)} V`);
  if (amps) bits.push(`${amps.value.toFixed(1)} A`);
  const switches = ports.filter((p) => p.can_write && p.is_boolean);
  if (switches.length) {
    bits.push(`${switches.filter((p) => p.value > 0).length} of ${switches.length} ports on`);
  }
  return bits.length ? bits.join(" · ") : "connected · no readings yet";
}

export function PowerSheet(_p: SheetProps): JSX.Element {
  const status = useStatus();
  const equipConnected = useEquipConnected();
  const sequence = useSequence();
  const showToast = useStore((s) => s.showToast);

  const role = resolveRoleConnected("switch", status?.backend_links, status?.connected, equipConnected);
  const connected = role.connected;
  const seqState = sequence?.state ?? null;
  const runOwns = seqState === "running" || seqState === "paused";

  const { lockedReason: capReason, onExplain } = useLock({
    cap: "control.power", needsRole: "switch",
  });
  const canControl = capReason == null;

  const [ports, setPorts] = useState<SwitchPort[] | null>(null);
  const [loadErr, setLoadErr] = useState<string | null>(null);

  // ---- drafts and the in-flight bookkeeping, verbatim in shape from PowerView.
  const [drafts, setDrafts] = useState<Record<number, number>>({});
  const draggingRef = useRef<number | null>(null);
  // Held in a REF as well as state: the ref is the GUARD (a handler that fires
  // twice inside one React batch reads the same stale state both times), the
  // state is what the rows render from.
  const inFlightRef = useRef<Set<number>>(new Set());
  const [pendingPorts, setPendingPorts] = useState<ReadonlySet<number>>(new Set());
  const queuedRef = useRef<Map<number, number>>(new Map());

  const publishPending = useCallback(() => setPendingPorts(new Set(inFlightRef.current)), []);

  const refresh = useCallback(async () => {
    try {
      setPorts(await api.get<SwitchPort[]>("/api/switch/ports"));
      setLoadErr(null);
    } catch (e) {
      // A failed GET must read as an ERROR, not as "no switches": keep whatever
      // is already on screen and say why the refresh failed.
      setLoadErr(e instanceof Error ? e.message : "couldn't load power ports");
    }
  }, []);

  useEffect(() => {
    if (!connected) { setPorts(null); setLoadErr(null); return; }
    void refresh();
    const t = setInterval(() => void refresh(), POLL_MS);
    return () => clearInterval(t);
  }, [connected, refresh]);

  // Reconcile drafts with server truth: once a fresh snapshot lands, drop the
  // draft for every port that is not still being edited, walked or queued. Per
  // port, not all-or-nothing - a walk on port 2 must not blank the thumb a
  // second finger is holding on port 3.
  useEffect(() => {
    setDrafts((d) => {
      let dropped = false;
      const keep: Record<number, number> = {};
      for (const key of Object.keys(d)) {
        const id = Number(key);
        if (id === draggingRef.current || inFlightRef.current.has(id) || queuedRef.current.has(id)) {
          keep[id] = d[id];
        } else dropped = true;
      }
      return dropped ? keep : d; // same reference when nothing changed
    });
  }, [ports]);

  /** POST one port. `queueIfBusy` REMEMBERS the value instead of dropping it. */
  const setPort = useCallback(async (id: number, value: number, queueIfBusy = false): Promise<void> => {
    if (!canControl) return; // read-only: the rows are inert anyway
    if (inFlightRef.current.has(id)) {
      // Never a second walk of the box while one is out. A slider commit is
      // remembered and re-sent below; a toggle tap is not, because the row is
      // already showing "-> ON" for the walk that IS out.
      if (queueIfBusy) queuedRef.current.set(id, value);
      return;
    }
    inFlightRef.current.add(id);
    publishPending();
    try {
      setPorts(await api.post<SwitchPort[]>("/api/switch/set", { port_id: id, value }));
    } catch (e) {
      showToast("error", (e as Error).message);
    } finally {
      inFlightRef.current.delete(id);
      const queued = queuedRef.current.get(id);
      if (queued !== undefined) {
        queuedRef.current.delete(id);
        // Re-arms inFlightRef SYNCHRONOUSLY (this runs to its first await), so
        // the reconcile effect that fires on the ports this POST just returned
        // still sees the port as busy and keeps its draft - otherwise the thumb
        // visibly bounces to the old level between the two walks.
        void setPort(id, queued, queueIfBusy);
      }
      publishPending();
    }
  }, [canControl, publishPending, showToast]);

  const commitDimmer = (id: number, value: number) => {
    if (draggingRef.current !== id) return; // nothing was edited on this port
    draggingRef.current = null;
    void setPort(id, value, true); // queue behind a walk in flight, never drop
  };
  // A drag the browser takes away delivers pointercancel or lostpointercapture
  // INSTEAD of pointerup, so `commitDimmer` never runs and the port stays
  // latched in `draggingRef` forever - the thumb sitting on a level nothing was
  // told about, which the 5 s poll then cannot correct.
  const abandonDimmer = (id: number) => {
    if (draggingRef.current !== id) return;
    draggingRef.current = null;
    const queued = queuedRef.current.get(id);
    setDrafts((d) => {
      const n = { ...d };
      if (queued === undefined) delete n[id]; else n[id] = queued;
      return n;
    });
  };

  const deviceName = status?.connected?.switch?.name;
  const live = powerLiveLine(ports, connected);
  const title = deviceName ? `POWER · ${deviceName}` : "POWER";

  const toggles = ports?.filter((p) => p.can_write && p.is_boolean) ?? [];
  const dimmers = ports?.filter((p) => p.can_write && !p.is_boolean) ?? [];
  const sensors = ports?.filter((p) => !p.can_write) ?? [];

  const reasonFor = (p: SwitchPort): string | null => {
    if (capReason) return capReason;
    if (runOwns && SESSION_CRITICAL.test(p.name)) return sessionLockReason(p.name);
    return null;
  };

  return (
    <Sheet
      title={title}
      backLabel="RIG"
      icon={<NxIcon name="power" size={18} />}
      live={live}
      onBack={() => nav.back()}
      data-testid="rig-power"
    >
      {!connected && (
        <EmptyCard
          title="No power box connected"
          hint={role.error ?? NO_BOX_HINT}
          action={(
            <ActionButton kind="secondary" onPress={() => nav.sheet("addDevice")}>
              GO TO ADD A DEVICE
            </ActionButton>
          )}
          data-testid="power-empty"
        />
      )}

      {connected && loadErr && ports == null && (
        <EmptyCard
          title="Couldn't load power ports"
          hint={loadErr}
          action={<ActionButton kind="secondary" onPress={() => void refresh()}>RETRY</ActionButton>}
          data-testid="power-load-error"
        />
      )}

      {connected && loadErr && ports != null && (
        <BannerCard
          tone="warn"
          text={`Couldn't refresh power ports: ${loadErr}`}
          cta={{ label: "RETRY", onPress: () => void refresh() }}
          data-testid="power-refresh-error"
        />
      )}

      {connected && ports != null && (
        <Card padding={0} data-testid="power-ports">
          {toggles.map((p) => {
            const on = p.value > 0;
            const pending = pendingPorts.has(p.id);
            const reason = reasonFor(p);
            return (
              <button
                key={p.id}
                type="button"
                role="switch"
                aria-checked={on}
                // A STATIC accessible name. The ON/OFF word is aria-hidden and the
                // walk rides on aria-describedby, so the control's identity never
                // moves out from under a voice command mid-interaction.
                aria-label={p.name}
                aria-busy={pending || undefined}
                aria-describedby={pending ? `nx-port-${p.id}-pending` : undefined}
                className={reason ? "nx-row nx-locked" : "nx-row"}
                data-tone={on ? "good" : undefined}
                aria-disabled={reason ? true : undefined}
                data-locked={reason ? "true" : undefined}
                title={reason ?? undefined}
                onClick={() => {
                  if (reason) { onExplain(reason); return; }
                  void setPort(p.id, on ? 0 : 1);
                }}
                data-testid={`port-${p.id}`}
              >
                <span className="nx-switch" data-checked={on ? "true" : "false"} aria-hidden="true">
                  <span className="nx-switch-knob" />
                  <span className="nx-switch-state">{on ? "ON" : "OFF"}</span>
                </span>
                <span className="nx-row-text">
                  <span className="nx-row-title">{p.name}</span>
                  {runOwns && SESSION_CRITICAL.test(p.name) && (
                    <span className="nx-row-sub">session-critical · matched by name</span>
                  )}
                </span>
                <span
                  className="nx-row-right"
                  id={`nx-port-${p.id}-pending`}
                  aria-hidden={!pending || undefined}
                >
                  {/* DEVIATION: the design's right column reads "0.9 A" per port.
                      `SwitchPort` is {id,name,value,min,max,unit,is_boolean,
                      can_write} and a boolean port's `value` is 0 or 1 with an
                      empty unit - there is no per-port current anywhere on the
                      wire, and printing `value` with an "A" after it would
                      invent one. The box's total current is a read-only port and
                      is in the telemetry rows and the live line. */}
                  <Mono size={10.5} tone={pending ? "accent" : on ? "good" : "dim"}>
                    {pending ? `-> ${on ? "OFF" : "ON"}` : on ? "ON" : "OFF"}
                  </Mono>
                </span>
              </button>
            );
          })}

          {dimmers.map((p) => {
            // The draft wins while THIS port is being dragged, walked or queued;
            // otherwise the box's own value shows (see the reconcile effect).
            const shown = drafts[p.id] ?? p.value;
            const pending = pendingPorts.has(p.id);
            const reason = reasonFor(p);
            return (
              <div key={p.id} className="nx-row" data-testid={`port-${p.id}`}
                style={{ flexDirection: "column", alignItems: "stretch", gap: 6 }}>
                <span style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
                  <span className="nx-row-title">{p.name}</span>
                  <Mono size={10.5} tone={pending ? "accent" : "dim"}>
                    {pending ? "-> " : ""}{shown.toFixed(0)}{p.unit}
                  </Mono>
                </span>
                <input
                  type="range"
                  min={p.min}
                  max={p.max}
                  value={shown}
                  // The name beside a range is a sibling span, not a label, so the
                  // accname algorithm gives an unnamed slider without this.
                  aria-label={`${p.name} level`}
                  aria-valuetext={pending
                    ? `${shown.toFixed(0)}${p.unit}, waiting for the power box`
                    : `${shown.toFixed(0)}${p.unit}`}
                  aria-busy={pending || undefined}
                  // A range has no `readOnly` to fall back on, so the handlers are
                  // made inert instead of using the native `disabled`.
                  aria-disabled={reason ? true : undefined}
                  data-locked={reason ? "true" : undefined}
                  title={reason ?? undefined}
                  style={{ width: "100%" }}
                  onChange={(e) => {
                    if (reason) return;
                    // Drag / keyboard tick: the local draft only, no POST.
                    draggingRef.current = p.id;
                    const v = Number(e.target.value);
                    setDrafts((d) => ({ ...d, [p.id]: v }));
                  }}
                  onPointerDown={() => { if (reason) onExplain(reason); }}
                  onPointerUp={(e) => commitDimmer(p.id, Number(e.currentTarget.value))}
                  onKeyUp={(e) => commitDimmer(p.id, Number(e.currentTarget.value))}
                  onPointerCancel={() => abandonDimmer(p.id)}
                  onLostPointerCapture={() => abandonDimmer(p.id)}
                  data-testid={`dimmer-${p.id}`}
                />
              </div>
            );
          })}

          {sensors.map((p) => (
            <div key={p.id} className="nx-row" data-testid={`port-${p.id}`}>
              <span className="nx-row-text">
                <span className="nx-row-title">{p.name}</span>
                <span className="nx-row-sub">telemetry · this box reports it, nothing sets it</span>
              </span>
              <span className="nx-row-right">
                <Mono size={12}>{p.value.toFixed(1)}{p.unit ? ` ${p.unit}` : ""}</Mono>
              </span>
            </div>
          ))}

          {toggles.length === 0 && dimmers.length === 0 && sensors.length === 0 && (
            <div className="nx-row">
              <span className="nx-row-text">
                <span className="nx-row-title">NO PORTS</span>
                <span className="nx-row-sub">this box answered with an empty port list</span>
              </span>
            </div>
          )}
        </Card>
      )}

      {connected && capReason && (
        <div data-testid="power-readonly-note">
          <Mono size={10.5} tone="warn">{capReason}</Mono>
        </div>
      )}

      <div data-testid="power-footer">
        <Mono size={10.5} tone="dim">{`${FOOTER_LOCK_NOTE} ${FOOTER_DEW_NOTE}`}</Mono>
      </div>
      <div style={{ height: 8 }} />
    </Sheet>
  );
}
