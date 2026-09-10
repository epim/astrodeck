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
//  5. THE SESSION LOCK IS THE ENGINE'S ANSWER NOW, NOT THIS FILE'S GUESS
//     (D-RIG-5, closing deviation E13). The design locks "mount, camera and
//     USB" while a session runs, and this sheet used to decide that alone, with
//     a regex over the port's label. That made the lock advice rather than
//     enforcement - anything that was not this sheet could cut power to the
//     mount mid-sequence - and it made two copies of one rule. `power_guard.py`
//     now holds the pattern byte-identical as the DEFAULT, every port row
//     arrives carrying `protected_now`, and each port carries a THREE-STATE
//     decision (BY NAME / PROTECTED / NOT PROTECTED) that beats the name in
//     both directions. The model, the sentences and the legacy fallback are in
//     `../lib/portSettings.ts`; read its header before changing any of this.
//  6. FOLLOW DEW IS A PER-PORT POLICY (D-RIG-3, closing deviation E24). The dew
//     loop drives any writable port whose `follow_dew` is set, scaled into that
//     port's OWN range - so the note beside the switch names that range and
//     never a percentage.
//
// An engine that predates S7h/S7L sends no annotation at all. Those rows keep
// the shipped name heuristic, the shipped sentence and the shipped footer, and
// grow no settings group: a control bound to a field that does not exist is
// worse than no control.

import { useCallback, useEffect, useRef, useState, type JSX } from "react";
import type { SheetProps } from "../../sheets";
import {
  ActionButton, BannerCard, Card, Disclosure, EmptyCard, Label, LockNote, Mono,
  Segmented, Sheet, Switch,
} from "../../../ui";
import { NxIcon } from "../../../icons";
import { nav } from "../../../router";
import { useLock } from "../../../lib/gateHook";
import { useEquipConnected, useSequence, useStatus, useStore } from "../../../../store";
import { resolveRoleConnected } from "../../../../lib/caps";
import {
  getSwitchPorts, putSwitchPortSettings, setSwitchPort,
  type SwitchPortSettings,
} from "../../../../api/power";
import {
  BY_NAME_NOTE, followDewNote, followDewSub, isAnnotated, mergeSettings, portLockReason,
  protectLine, protectPatch, protectStop, PROTECT_STOPS, protectedSub,
  SESSION_CRITICAL, settingsRefusal, settingsSummary, switchRefusal,
} from "../lib/portSettings";
import type { SwitchPort } from "../../../../types";

export { SESSION_CRITICAL, legacyLockReason } from "../lib/portSettings";

/** How often the ports are re-read while the role is connected
 *  (views/PowerView.tsx:59-65). Torn down the moment it drops. */
const POLL_MS = 5000;

/** The footer for an engine that answers with annotated ports: it names the two
 *  per-port controls one tap below, and what each does when it is left alone. */
export const FOOTER_NOTE =
  "Ports marked PROTECTED are refused while a run is live; BY NAME follows the "
  + "port's label. Dew ports set to FOLLOW DEW are driven from the dew margin; "
  + "the rest hold their level until you change them.";

/** The footer for an engine with no `power_guard` (pre-S7h/S7L). Kept WORD FOR
 *  WORD because on that engine it is still exactly true: the lock is a name
 *  match this file makes, there is no per-port store to point at, and there is
 *  no dew loop. Shown only when the ports themselves prove it - see
 *  `legacyEngine` below, which needs real rows and no annotation on any of
 *  them, never a guess. */
export const FOOTER_LOCK_NOTE =
  "Mount, camera and USB are locked while a session runs; stop the session to "
  + "unlock them. Ports are matched by name; rename a port on the power box if "
  + "the wrong one locks.";

/** E24's honest replacement, for that same engine. */
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
  // The engine's refusals are shown VERBATIM, which `showToast` cannot do: it
  // runs every message through `humanizeLog`, which truncates at 137 characters,
  // and `power_guard`'s refusal is longer than that - the clause that gets cut
  // is the second way out ("clear the protection for this port in Power
  // settings"), which is the one the user is standing in front of.
  const enqueueToast = useStore((s) => s.enqueueToast);

  const role = resolveRoleConnected("switch", status?.backend_links, status?.connected, equipConnected);
  const connected = role.connected;
  const seqState = sequence?.state ?? null;
  const runOwns = seqState === "running" || seqState === "paused";

  const { lockedReason: capReason, onExplain } = useLock({
    cap: "control.power", needsRole: "switch",
  });
  const canControl = capReason == null;

  // The two POLICIES are a different capability from operating the box: they
  // decide what the ENGINE refuses during a run and how the dew loop drives a
  // port, so the route is `config.safety`, while the on/off controls stay
  // `control.power`. Both resolve to "needs admin access" on today's role table
  // (`lib/caps.ts` gives an operator neither), so the phrase is the same - but
  // the CAPABILITY is what the server checks, and folding these into one lock
  // would be a hand-written claim about the route rather than a reading of it.
  const { lockedReason: settingsReason, onExplain: explainSettings } = useLock({
    cap: "config.safety", needsRole: "switch",
  });

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

  // ---- the settings writes, with the SAME never-two-walks-at-once guard the
  // port writes have. `PUT /api/switch/ports/{id}` answers with the annotated
  // list, which costs the box a walk, and a fast second tap on the segmented
  // control would otherwise stack one - and land whichever response arrived
  // last, not whichever the user chose last.
  const settingsFlightRef = useRef<Set<number>>(new Set());
  const [settingsPending, setSettingsPending] = useState<ReadonlySet<number>>(new Set());
  const settingsQueueRef = useRef<Map<number, SwitchPortSettings>>(new Map());
  const publishSettingsPending =
    useCallback(() => setSettingsPending(new Set(settingsFlightRef.current)), []);

  const refresh = useCallback(async () => {
    try {
      setPorts(await getSwitchPorts());
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
      setPorts(await setSwitchPort(id, value));
    } catch (e) {
      // A protection refusal is not an error message, it is THE answer, and it
      // arrives complete: the port's name, what the switch would have done, and
      // both ways out. Shown verbatim, and matched by `code` rather than by any
      // part of the sentence, so re-wording it server-side cannot silently turn
      // it back into a generic red toast. No re-read first - the 409 already
      // carries everything, and the 5 s poll corrects the row's own lock.
      const refused = switchRefusal(e);
      if (refused) enqueueToast({ level: "error", title: refused });
      else showToast("error", (e as Error).message, { verbatim: true });
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
  }, [canControl, publishPending, showToast, enqueueToast]);

  /** PUT one port's policies. Sends ONLY the keys in `patch`: absent means
   *  unchanged on the server, and `protect_during_run: null` is one of its
   *  three real values, so there is no value that could mean "leave it alone"
   *  (`api/power.ts`'s `putSwitchPortSettings` enforces that). */
  const writeSettings = useCallback(async (
    id: number, patch: SwitchPortSettings,
  ): Promise<void> => {
    if (settingsReason) return; // read-only: the controls are inert anyway
    if (settingsFlightRef.current.has(id)) {
      // MERGED, not replaced: the two keys are independent and each patch says
      // nothing about the key it omits, so the newer press wins on its own key
      // and an earlier press on the other key is still sent.
      settingsQueueRef.current.set(
        id, mergeSettings(settingsQueueRef.current.get(id) ?? {}, patch),
      );
      return;
    }
    settingsFlightRef.current.add(id);
    publishSettingsPending();
    try {
      setPorts(await putSwitchPortSettings(id, patch));
    } catch (e) {
      enqueueToast({ level: "error", title: settingsRefusal(e) });
    } finally {
      settingsFlightRef.current.delete(id);
      const queued = settingsQueueRef.current.get(id);
      if (queued !== undefined) {
        settingsQueueRef.current.delete(id);
        void writeSettings(id, queued);
      }
      publishSettingsPending();
    }
  }, [settingsReason, publishSettingsPending, enqueueToast]);

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

  // `gate.ts`'s priority order is link -> cap -> role -> lane -> extra, and the
  // protection is the EXTRA: last. Composing it as `capReason ?? ...` keeps that
  // order exactly, so a viewer is told they cannot switch anything before being
  // told this particular port is busy being used by a run.
  const reasonFor = (p: SwitchPort): string | null =>
    capReason ?? portLockReason(p, runOwns);

  /** Proof, not a guess, that this engine has no `power_guard`: real rows, and
   *  not one of them annotated. Absent ports (not connected, still loading) say
   *  nothing either way and get this build's normal copy. */
  const legacyEngine = ports != null && ports.length > 0 && !ports.some(isAnnotated);

  /** The two per-port policies, one tap below the row. Rendered only for a port
   *  the engine actually annotated: on an older engine the PUT route does not
   *  exist, and a control whose write has nowhere to land is the defect this
   *  wave is closing, not a nicety. */
  const settingsFor = (p: SwitchPort): JSX.Element | null => {
    if (!isAnnotated(p)) return null;
    const saving = settingsPending.has(p.id);
    return (
      <div style={{ padding: "0 14px 12px" }}>
        <Disclosure
          summary="PORT SETTINGS"
          // The summary STAYS while a write is out, with the state beside it:
          // it is still what the port is set to until the answer lands, and
          // replacing it with the word "saving" would take away the one thing
          // the row is for in order to say something the caret already implies.
          sub={saving ? `${settingsSummary(p)} · saving` : settingsSummary(p)}
          data-testid={`port-settings-${p.id}`}
        >
          <Label size={10}>PROTECT DURING RUN</Label>
          <Segmented
            label={`protect ${p.name} during a run`}
            options={PROTECT_STOPS}
            value={protectStop(p)}
            onChange={(next) => void writeSettings(p.id, protectPatch(next))}
            lockedReason={settingsReason}
            onExplain={explainSettings}
            data-testid={`port-protect-${p.id}`}
          />
          <Mono size={10.5} tone="dim">{protectLine(p)}</Mono>
          <Mono size={10.5} tone="dim">{BY_NAME_NOTE}</Mono>
          <Switch
            checked={p.follow_dew === true}
            onChange={(next) => void writeSettings(p.id, { follow_dew: next })}
            label="FOLLOW DEW"
            note={followDewNote(p)}
            lockedReason={settingsReason}
            onExplain={explainSettings}
            data-testid={`port-follow-dew-${p.id}`}
          />
          <LockNote reason={settingsReason} />
        </Disclosure>
      </div>
    );
  };

  /** The sub-line under a port's name. Three different sentences on purpose -
   *  "why is this locked" and "who is driving this level" are different
   *  questions and the name heuristic used to leave both unanswerable. */
  const rowSub = (p: SwitchPort): string | null => {
    if (isAnnotated(p)) {
      if (p.protected_now) return protectedSub(p);
      return p.follow_dew ? followDewSub() : null;
    }
    return runOwns && SESSION_CRITICAL.test(p.name)
      ? "session-critical · matched by name"
      : null;
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
            const sub = rowSub(p);
            return (
              <div key={p.id}>
              <button
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
                  {sub != null && <span className="nx-row-sub">{sub}</span>}
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
              {settingsFor(p)}
              </div>
            );
          })}

          {dimmers.map((p) => {
            // The draft wins while THIS port is being dragged, walked or queued;
            // otherwise the box's own value shows (see the reconcile effect).
            const shown = drafts[p.id] ?? p.value;
            const pending = pendingPorts.has(p.id);
            const reason = reasonFor(p);
            const sub = rowSub(p);
            return (
              <div key={p.id}>
              <div className="nx-row" data-testid={`port-${p.id}`}
                style={{ flexDirection: "column", alignItems: "stretch", gap: 6 }}>
                <span style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
                  <span className="nx-row-title">{p.name}</span>
                  <Mono size={10.5} tone={pending ? "accent" : "dim"}>
                    {pending ? "-> " : ""}{shown.toFixed(0)}{p.unit}
                  </Mono>
                </span>
                {sub != null && <span className="nx-row-sub">{sub}</span>}
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
              {settingsFor(p)}
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
        <Mono size={10.5} tone="dim">
          {legacyEngine ? `${FOOTER_LOCK_NOTE} ${FOOTER_DEW_NOTE}` : FOOTER_NOTE}
        </Mono>
      </div>
      <div style={{ height: 8 }} />
    </Sheet>
  );
}
