// SafetyPanel.tsx — the Settings → Safety surface (W1.10). Today this hosts the
// SUN-AVOIDANCE control: the server-side sun-exclusion cone (hub._check_solar)
// that blocks any slew toward the Sun. It is ON BY DEFAULT to protect normal
// deep-sky gear; turning it OFF is a SOLAR-ASTRONOMY toggle (a solar scope with
// proper filtration that WANTS to point at the Sun).
//
// RBAC (W2.5): the disarm path (solar_avoidance=false) is gated server-side by
// BOTH config.safety + config.solar_override. config.solar_override is admin-only
// (an operator never holds it), so this panel HIDES/DISABLES the toggle for a
// caller without it and surfaces a passive read-only note — never a 403-on-tap.
//
// DANGER (destructive-tier): disabling avoidance lets the mount slew AT the Sun.
// We require a deliberate hold-to-confirm (confirmDialog mode:"hold", tone:
// "danger") that names the consequence before we ever POST solar_avoidance=false.
// Re-arming (turning it back ON) is always safe — no confirm.
//
// SAVE: the server's set_safety REPLACES the whole SafetyConfig, and the
// field-level cap rule keys off model_fields_set, so we ECHO the full current
// safety block with our edits applied (the auth-panel contract). A 403 from the
// server (override cap lost mid-session) surfaces inline.

import { useEffect, useRef, useState, type JSX } from "react";
import type { SafetyConfig } from "../../types";
import {
  setSafetyConfig,
  getDomeState,
  closeDome,
  type DomeState,
} from "../../api/backends";
import { ApiError } from "../../api";
import { useConfig, useStore } from "../../store";
import { accessPhrase, useCan } from "../../lib/caps";
import { useBusyOrPending } from "../../lib/useBusy";
import { confirmDialog } from "../ConfirmDialog";
import { domeStatusLabel } from "../../lib/dome";
import { Panel, Field, Toggle } from "../ui";
import { Icon } from "../icons";

export default function SafetyPanel(): JSX.Element {
  const config = useConfig();
  const safety = config?.safety;
  // config.solar_override — admin-only. Gates the disarm toggle + the exclusion
  // angle (both are solar fields the server requires the override cap to write).
  const canOverride = useCan("config.solar_override");
  // The roof SETTINGS live in the same SafetyConfig block, which set_safety gates
  // on config.safety — and neither operator nor viewer holds any config.* cap, so
  // without this gate their tap on "Close roof on unsafe" 403'd while the switch
  // sat there reading ON. An interlock nobody armed must not LOOK armed.
  const canSafety = useCan("config.safety");
  // ...but "Close roof now" is NOT a config write. POST /api/dome/close is gated
  // on CAP_CONTROL_MOUNT (app.py:3748) because it MOVES THE MOUNT, and operator
  // holds control.mount while holding no config.* cap at all. Folding the two
  // together dimmed the only manual roof close in the whole app away from the
  // operator — the person actually standing at the rig, and the rental persona
  // the role exists for — and told them it needed an admin, which is false.
  // Two permissions, two gates, two sentences.
  const canCloseRoof = useCan("control.mount");

  // Draft mirrors the server block; re-seed whenever a fresh config lands (a save
  // broadcasts `config` over the WS → loadConfig refreshes the store slice).
  const [avoidance, setAvoidance] = useState(true);
  const [coneDeg, setConeDeg] = useState(30);
  const [busy, setBusy] = useState(false);
  // A refusal is reported AT THE CONTROL that raised it, not only in a line at
  // the foot of the panel: with the roof block sitting between the sun toggle
  // and that line, a refused re-arm reported itself a screenful away from the
  // switch it was about. The tag says which control to print it beside.
  const [err, setErr] = useState<{ where: "solar" | "roof"; msg: string } | null>(
    null,
  );
  const [savedAt, setSavedAt] = useState<number | null>(null);

  // Re-seed from the server. `solar_*` are additive on the backend, so a legacy
  // config without them deserializes ON @ 30 — but guard for an older UI payload
  // that predates the type by falling back to the same protective defaults.
  const seedAvoidance = safety?.solar_avoidance ?? true;
  const seedCone = safety?.solar_exclusion_deg ?? 30;
  useEffect(() => {
    setAvoidance(seedAvoidance);
    setConeDeg(seedCone);
    setErr(null);
    setSavedAt(null);
  }, [seedAvoidance, seedCone]);

  // --- observatory roof / dome (PRO-4) ----------------------------------------
  // The two auto-close flags live in the SAME safety block (persisted via the
  // full-block echo below); the live shutter status comes from GET /api/dome/state.
  const [dome, setDome] = useState<DomeState | null>(null);
  const seedCloseOnUnsafe = safety?.close_dome_on_unsafe ?? false;
  const seedCloseWhenDone = safety?.close_dome_when_done ?? false;
  // PRO-4 D3: opt-in auto-reopen. Additive on the backend, so a legacy config
  // without the key deserializes false — fall back to the same protective default.
  const seedReopenWhenSafe = safety?.reopen_dome_when_safe ?? false;
  const [closeOnUnsafe, setCloseOnUnsafe] = useState(false);
  const [closeWhenDone, setCloseWhenDone] = useState(false);
  const [reopenWhenSafe, setReopenWhenSafe] = useState(false);
  useEffect(() => {
    setCloseOnUnsafe(seedCloseOnUnsafe);
    setCloseWhenDone(seedCloseWhenDone);
    setReopenWhenSafe(seedReopenWhenSafe);
  }, [seedCloseOnUnsafe, seedCloseWhenDone, seedReopenWhenSafe]);

  // The shutter status used to be read ONCE, on mount. A rain trip could close
  // the roof five minutes later and this badge went on saying "Roof open" for
  // the rest of the visit — the one place in Settings that reports the roof, and
  // it was a page-load snapshot. /api/dome/state is outside the WS status frame,
  // so it gets its own slow poll; 15 s matches MonitorView.tsx, and a shutter
  // takes tens of seconds to move.
  // Guards a reply that outlives the panel — a 15 s poll plus a one-shot after a
  // close means there is usually one in flight.
  const domeLive = useRef(true);
  const refreshDome = () =>
    getDomeState()
      .then((d) => {
        if (domeLive.current) setDome(d);
      })
      .catch(() => {
        /* no dome / offline — the honest-disabled note covers it */
      });
  useEffect(() => {
    domeLive.current = true;
    void refreshDome();
    const id = window.setInterval(() => void refreshDome(), 15000);
    return () => {
      domeLive.current = false;
      window.clearInterval(id);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // POST /api/dome/close `_spawn`s the "goto" lane with replace=True, and the
  // park AND the shutter travel both happen inside it — the POST itself resolves
  // in ~40 ms, which is why this button used to snap back to "Close roof now"
  // while the mount was still swinging to park. Worse, replace=True means a
  // second tap CANCELS the park in flight and starts the whole thing over, so
  // OUR OWN close is hard-disabled off the lane rather than merely relabelled.
  const { busy: gotoBusy, arm: armRoofClose } = useBusyOrPending("goto");
  // Ours vs the mount's: the lane is shared with every slew, so it says "the rig
  // is moving" but not "your close is running". "sending" = our POST is out and
  // the rig has not answered yet; "closing" = the server took it.
  //
  // Why a phase and not a boolean: the 6 s `arm()` latch must be armed only on
  // ACCEPTANCE. Armed before the POST, a refused close (403, no dome, dropped
  // link) left the lane reading busy for six more seconds, during which the
  // panel said "The mount is moving" over a mount that was not moving while the
  // error toast said the opposite.
  const [closePhase, setClosePhase] = useState<"idle" | "sending" | "closing">(
    "idle",
  );
  const ourClose = closePhase !== "idle";
  useEffect(() => {
    if (closePhase !== "closing" || gotoBusy) return;
    // The lane finished — or never took, in which case the latch expired.
    // Either way, re-read the shutter instead of assuming.
    setClosePhase("idle");
    void refreshDome();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [closePhase, gotoBusy]);

  if (!safety) {
    return (
      <Panel title="Sun avoidance">
        <p className="text-xs text-dim">Loading safety configuration…</p>
      </Panel>
    );
  }

  const dirty =
    avoidance !== seedAvoidance || Math.abs(coneDeg - seedCone) > 1e-9;

  // A zeroed cone makes the server-side guard INERT (hub._check_solar returns on
  // cone<=0), so "armed" must reflect BOTH the toggle AND a positive cone — else
  // the panel would claim protection while the mount can freely slew at the Sun
  // (UX-38). Drives the badge, the status copy, and the danger banner below.
  const effectiveArmed = avoidance && coneDeg > 0;

  // Persist: echo the FULL current safety block with our two edits applied. The
  // server replaces SafetyConfig wholesale, so anything we omit would be lost.
  // Returns TRUE only when the server accepted the write. The interlock toggle
  // uses that to put itself back where the SERVER is on a refusal — see
  // onToggle. Callers that own a typed draft (the cone field) ignore it.
  const persist = async (next: { avoidance: boolean; cone: number }) => {
    if (busy) return false;
    setErr(null);
    setBusy(true);
    try {
      const body: SafetyConfig = {
        ...safety,
        solar_avoidance: next.avoidance,
        solar_exclusion_deg: next.cone,
      };
      await setSafetyConfig(body);
      // Re-hydrate the config slice so this panel + any cone banner re-seed.
      await useStore.getState().loadConfig();
      setSavedAt(Date.now());
      return true;
    } catch (e) {
      // Keep the typed CONE draft so the user sees what they tried -- never
      // silently revert a number someone entered. That reasoning does NOT
      // extend to the avoidance BOOLEAN: it is not a draft, it is a readout of
      // whether the interlock is armed, and this panel is the only surface in
      // the app that renders it. Left optimistic, a refused write showed the
      // switch ON and hid the red SUN AVOIDANCE OFF banner while the server
      // still allowed a slew at the Sun -- failing in the dangerous direction
      // on the guard that protects the camera and anyone at the eyepiece.
      // onToggle restores it from the server on a false return.
      const msg =
        e instanceof ApiError
          ? e.status === 403
            ? "Sun avoidance can only be changed by an admin (config.solar_override)."
            : e.message || "Could not save."
          : "Could not save.";
      setErr({ where: "solar", msg });
      return false;
    } finally {
      setBusy(false);
    }
  };

  // Roof auto-close flags: echo the FULL current safety block with the one flag
  // changed (same wholesale-replace contract as the solar persist). No confirm —
  // enabling protective auto-close is always the safe direction.
  const domeConnected = !!dome?.connected;
  const shutter = dome?.shutter ?? "unknown";
  // Why the three AUTO-CLOSE SETTINGS are inert, or null when they are live.
  // Capability first: it is the reason that does not go away by plugging
  // something in. This gate covers CONFIG WRITES ONLY — see closeLock.
  const flagsLock = !canSafety
    ? `Changing what the roof does by itself needs ${accessPhrase("config.safety")} (config.safety)`
    : !domeConnected
      ? "Connect a dome/roof device first"
      : null;
  // Why the MANUAL CLOSE is inert. Different route, different capability: the
  // close moves the mount, so it is control.mount, which an operator holds.
  const closeLock = !canCloseRoof
    ? `Closing the roof needs ${accessPhrase("control.mount")} (control.mount)`
    : !domeConnected
      ? "No dome or roof device is connected — the server has nothing to close"
      : null;

  // Returns TRUE only when the server accepted the write — same contract as
  // `persist` above, and for the same reason: these switches are not drafts,
  // they are readouts of what the rig will do when the rain sensor trips. Left
  // optimistic, a 403 (every non-admin: no role but admin holds any config.*
  // cap) painted "Close roof on unsafe" ON, un-dimmed its reopen sub-toggle, and
  // left the operator believing a rain trip would shut the roof over their gear.
  const persistDomeFlag = async (patch: Partial<SafetyConfig>) => {
    if (busy) return false;
    setErr(null);
    setBusy(true);
    try {
      const body: SafetyConfig = { ...safety, ...patch };
      await setSafetyConfig(body);
      await useStore.getState().loadConfig();
      setSavedAt(Date.now());
      return true;
    } catch (e) {
      const msg =
        e instanceof ApiError
          ? e.status === 403
            ? `Changing the roof settings needs ${accessPhrase("config.safety")} (config.safety).`
            : e.message || "Could not save."
          : "Could not save.";
      setErr({ where: "roof", msg });
      return false;
    } finally {
      setBusy(false);
    }
  };

  // On a refusal each switch goes back to where the SERVER is. `safety` is the
  // stored config, which a failed write left untouched, so it IS that truth.
  const onToggleCloseOnUnsafe = async (on: boolean) => {
    setCloseOnUnsafe(on);
    if (!(await persistDomeFlag({ close_dome_on_unsafe: on }))) {
      setCloseOnUnsafe(seedCloseOnUnsafe);
    }
  };
  const onToggleCloseWhenDone = async (on: boolean) => {
    setCloseWhenDone(on);
    if (!(await persistDomeFlag({ close_dome_when_done: on }))) {
      setCloseWhenDone(seedCloseWhenDone);
    }
  };
  const onToggleReopenWhenSafe = async (on: boolean) => {
    setReopenWhenSafe(on);
    if (!(await persistDomeFlag({ reopen_dome_when_safe: on }))) {
      setReopenWhenSafe(seedReopenWhenSafe);
    }
  };

  // Manual close: fence + park + close via the tested ordering guard on the
  // server. The POST only CREATES the task, so the in-flight state comes from
  // the lane (see `gotoBusy` above), never from this promise.
  //
  // The goto lane is shared with every slew, and the two cases are NOT the same
  // control decision. A second tap on OUR OWN close cancels the park we already
  // started and begins it again — pure loss, so that one is hard-disabled. A
  // slew somebody else started is different: dome_close calls
  // hub.bump_motion_epoch() and re-spawns with replace=True precisely so it CAN
  // pre-empt one. Blocking the close for the 30–90 s of a goto_and_center and
  // saying "wait for it to finish" made the protective action wait on the
  // hazard. Rain does not wait for a slew. So it stays reachable, behind a hold
  // that names what it costs.
  const onCloseRoofNow = async () => {
    if (closeLock || ourClose) return;
    if (gotoBusy) {
      const ok = await confirmDialog({
        title: "Stop the slew and close the roof?",
        body: (
          <>
            The mount is moving right now. Closing the roof cancels that move,
            parks the mount and then shuts the shutter — whatever it was slewing
            to is abandoned and has to be started again. Do it if the weather is
            turning. If it is not urgent, stop the mount yourself (or let the
            move finish) and close after.
          </>
        ),
        tone: "warn",
        mode: "hold",
        confirmLabel: "Stop the mount and close",
        cancelLabel: "Leave the mount moving",
      });
      if (!ok) return;
    }
    // "sending" disables the button. It cannot latch: api.ts gives every request
    // a hard 15 s timeout that THROWS (ApiError timedOut), so the catch below
    // always runs and always returns the control. Do not replace that await with
    // anything that can hang forever.
    setClosePhase("sending");
    try {
      await closeDome();
      // Arm the pending latch only on ACCEPTANCE. Armed before the POST, a
      // refusal left the control dead — and mis-narrated — for 6 s.
      armRoofClose();
      setClosePhase("closing");
      useStore.getState().enqueueToast({
        level: "info",
        title: "Closing the roof — parking the mount first.",
      });
    } catch (e) {
      setClosePhase("idle");
      const msg =
        e instanceof ApiError ? e.message || "Could not close the roof." : "Could not close the roof.";
      useStore.getState().enqueueToast({ level: "error", title: msg });
    }
  };

  // The toggle handler. Turning avoidance OFF is the DANGER path: a deliberate
  // hold-to-confirm naming the consequence (the mount may slew at the Sun) BEFORE
  // we POST. Turning it back ON is always safe and saves immediately.
  const onToggle = async (wantOn: boolean) => {
    if (busy) return;
    if (!wantOn) {
      const ok = await confirmDialog({
        title: "Disable sun avoidance?",
        body: (
          <>
            This turns OFF the sun-exclusion cone. The mount will be allowed to
            slew AT and track the Sun. Only do this for a SOLAR scope with a
            proper solar filter installed — pointing unfiltered optics at the
            Sun can destroy the camera and instantly blind anyone looking
            through the gear.
          </>
        ),
        tone: "danger",
        mode: "hold",
        confirmLabel: "Disable sun avoidance",
        cancelLabel: "Keep protection on",
      });
      if (!ok) return; // user backed out — leave avoidance ON, no save
      setAvoidance(false);
      // A REFUSED disarm must snap back to armed: the server still has the cone
      // enforced, and showing OFF would invite someone to point unfiltered
      // optics at the Sun on the strength of a write that never landed. The
      // safe direction here happens to be the honest one.
      if (!(await persist({ avoidance: false, cone: coneDeg }))) {
        setAvoidance(safety.solar_avoidance);
      }
    } else {
      // Re-arming protection — always safe, no confirm.
      setAvoidance(true);
      // ...but "safe to ATTEMPT" is not "certain to SUCCEED". A 403 (the
      // override cap lost mid-session), a 409 or a dropped connection all leave
      // the server unprotected, and an optimistic switch would report Armed
      // with the danger banner gone. An interlock has to fail VISIBLY CLOSED:
      // show what the server actually has, with the error beside it. `safety`
      // is the stored config, which a failed write left untouched — so it IS
      // the server's truth.
      if (!(await persist({ avoidance: true, cone: coneDeg }))) {
        setAvoidance(safety.solar_avoidance);
      }
    }
  };

  const saveCone = async () => {
    if (busy || !dirty) return;
    await persist({ avoidance, cone: coneDeg });
  };

  return (
    <Panel
      title="Sun avoidance"
      right={
        <span className="inline-flex items-center gap-1.5 text-[11px] text-dim">
          <Icon name="sun" size={14} />
          Daytime guard
        </span>
      }
    >
      <p className="text-[11px] text-dim leading-relaxed max-w-xl mb-4">
        A sun-exclusion cone blocks any slew (and stops tracking) toward the Sun,
        protecting your camera and optics from solar damage during the day. It
        works even before you set a location. Leave it ON for normal deep-sky
        imaging.
      </p>

      {/* master toggle */}
      <div className="flex items-start justify-between gap-3 py-3 border-t border-line">
        <div className="min-w-0">
          <div className="text-sm text-ink inline-flex items-center gap-2">
            Sun avoidance
            <span
              className={`mono text-[9px] tracking-[0.16em] uppercase px-1.5 py-0.5 border ${
                effectiveArmed
                  ? "text-good border-good/50"
                  : "text-bad border-bad/60"
              }`}
            >
              {effectiveArmed ? "Armed" : "Disarmed"}
            </span>
          </div>
          <p className="text-[11px] text-dim max-w-md">
            {effectiveArmed
              ? "On — the mount refuses to point within the exclusion cone of the Sun."
              : avoidance
                ? "Cone set to 0° — protection is OFF until you set an angle above 0."
                : "OFF — SOLAR ASTRONOMY MODE. The mount may slew at the Sun. Use only with a proper solar filter installed."}
          </p>
        </div>
        <Toggle
          checked={avoidance}
          onChange={onToggle}
          disabled={busy || !canOverride}
          label="Sun avoidance"
          showState
        />
      </div>

      {/* A refused re-arm belongs BESIDE the switch that snapped back, not in a
          line under the whole panel — the roof block now sits between the two. */}
      {err?.where === "solar" && (
        <p className="text-xs text-bad inline-flex items-center gap-1.5 -mt-1 mb-1">
          <Icon name="alert" size={13} className="shrink-0" />
          {err.msg}
        </p>
      )}

      {/* exclusion angle */}
      <div className="grid gap-3 sm:grid-cols-2 pt-4 border-t border-line mt-1">
        <Field
          label="Exclusion angle (deg)"
          hint="Half-angle of the no-go cone around the Sun. A slew is blocked when the target is within this many degrees of the Sun. 30 is a safe default; 0 disables the cone."
        >
          <input
            className="field"
            type="number"
            min={0}
            max={90}
            step={1}
            value={coneDeg}
            onChange={(e) =>
              setConeDeg(
                Math.min(90, Math.max(0, Number(e.target.value) || 0)),
              )
            }
            disabled={busy || !canOverride}
          />
        </Field>
        <div className="flex flex-col justify-end items-start gap-1.5">
          <button
            type="button"
            className="btn btn-accent min-h-[44px] sm:min-h-0 inline-flex items-center gap-2"
            onClick={saveCone}
            disabled={busy || !canOverride || !dirty || !avoidance}
          >
            <Icon name="check" size={15} />
            {busy ? "Saving…" : "Save angle"}
          </button>
          {/* The field stays live while avoidance is off, so the greyed button
              beside it read as a bug. It is not — there is no cone to size while
              the guard is disarmed, and re-arming writes whatever is typed here,
              so nothing needs saving separately. Say that where it is read. */}
          {!avoidance && canOverride && (
            <p className="text-[11px] text-dim max-w-md">
              Nothing to save while avoidance is off — turning it back on stores
              the angle above with it.
            </p>
          )}
        </div>
      </div>

      {/* DANGER banner while disarmed — loud, persistent, distinct from a toast */}
      {!avoidance && (
        <div className="flex items-start gap-3 border border-bad/60 bg-bad/10 px-3 py-2 text-xs mt-4">
          <Icon name="alert" size={14} className="text-bad shrink-0 mt-0.5" />
          <span className="text-ink">
            <span className="mono tracking-[0.12em] uppercase text-bad">
              Sun avoidance off
            </span>{" "}
            — the mount is allowed to slew at the Sun. Confirm a solar filter is
            installed before any daytime slew. Turn this back on as soon as solar
            observing is done.
          </span>
        </div>
      )}

      {/* cone-zero warning: toggle ON but the cone is 0 → the guard is inert
          (UX-38). Distinct from the solar-mode banner so the state reads honestly. */}
      {avoidance && coneDeg <= 0 && (
        <div className="flex items-start gap-3 border border-bad/60 bg-bad/10 px-3 py-2 text-xs mt-4">
          <Icon name="alert" size={14} className="text-bad shrink-0 mt-0.5" />
          <span className="text-ink">
            <span className="mono tracking-[0.12em] uppercase text-bad">
              Exclusion cone is 0°
            </span>{" "}
            — sun avoidance is switched on but INERT: a 0° cone blocks nothing, so
            the mount can still slew at the Sun. Set an angle above 0 to re-arm.
          </span>
        </div>
      )}

      {/* read-only note for callers without the override cap (W2.5: passive) */}
      {!canOverride && (
        <div className="flex items-center gap-3 border border-line2 bg-raise/40 px-3 py-2 text-xs mt-4">
          <Icon name="lock" size={14} className="text-dim shrink-0" />
          <span className="text-dim">
            Changing sun avoidance needs {accessPhrase("config.solar_override")}{" "}
            (config.solar_override). The current setting is shown for reference.
          </span>
        </div>
      )}

      {/* ---------------------------------------- observatory roof / dome (PRO-4) */}
      <div className="pt-4 border-t border-line mt-4">
        <div className="flex items-center justify-between gap-2">
          <div className="text-sm text-ink inline-flex items-center gap-2">
            <Icon name="shield" size={14} className="text-dim" />
            Observatory roof
          </div>
          <span className="mono text-[9px] tracking-[0.16em] uppercase px-1.5 py-0.5 border text-dim border-line2">
            {domeStatusLabel(shutter)}
          </span>
        </div>
        <p className="text-[11px] text-dim leading-relaxed max-w-xl mt-1">
          Auto-close a motorized roll-off roof over the parked gear on an unsafe
          condition or at end-of-night. The mount is always parked clear first —
          if it can’t be confirmed parked, the roof is left open (a wet scope
          beats a crushed one).
        </p>

        {/* honest-disabled (§11.8) when the SETTINGS are unreachable: dim + lock +
            aria-disabled + title — NOT the native disabled attribute. Two very
            different reasons, so they get two different sentences: no capability
            is about who you are, no dome is about what is plugged in.
            SCOPE: this wrapper covers the three CONFIG toggles and nothing else.
            "Close roof now" used to live inside it, which meant config.safety —
            an admin-only cap — silently took the app's only manual roof close
            away from an operator who holds control.mount and could perform it.
            pointer-events-none also killed it for touch while leaving it live to
            a Tab and a Space, so it was dead on the tablet and mislabelled on
            the keyboard. It now sits OUTSIDE, with its own gate. */}
        <div
          aria-disabled={!!flagsLock}
          title={flagsLock ?? undefined}
          className={
            flagsLock ? "mt-3 opacity-50 pointer-events-none select-none" : "mt-3"
          }
        >
          <div className="flex items-start justify-between gap-3 py-2">
            <div className="min-w-0">
              <div className="text-sm text-ink">Close roof on unsafe</div>
              <p className="text-[11px] text-dim max-w-md">
                A rain/cloud trip parks the mount and closes the roof instead of
                pausing under an open sky.
              </p>
            </div>
            {/* Natively disabled without the capability, not merely dimmed: the
                container's pointer-events-none stops a tap but not a Tab and a
                Space, and behind that key is a 403 that used to leave the switch
                reading ON. */}
            <Toggle
              checked={closeOnUnsafe}
              onChange={onToggleCloseOnUnsafe}
              disabled={busy || !canSafety}
              label="Close roof on unsafe"
              showState
            />
          </div>

          {/* PRO-4 D3 opt-in auto-reopen — a SUB-control of "close on unsafe":
              reopening requires closing first, so it is honest-disabled (§11.8:
              dim + aria-disabled + pointer-events-none + title, NOT native
              disabled) whenever close-on-unsafe is OFF. */}
          <div
            aria-disabled={!closeOnUnsafe}
            title={
              closeOnUnsafe
                ? undefined
                : "Turn on “Close roof on unsafe” first — reopening requires closing"
            }
            className={
              closeOnUnsafe
                ? "flex items-start justify-between gap-3 py-2 pl-6 border-t border-line"
                : "flex items-start justify-between gap-3 py-2 pl-6 border-t border-line opacity-50 pointer-events-none select-none"
            }
          >
            <div className="min-w-0">
              <div className="text-sm text-ink">Reopen roof when safe again</div>
              <p className="text-[11px] text-dim max-w-md">
                Instead of ending the run, wait out the weather with the roof
                closed, then reopen and resume the target once conditions clear.
                If the roof can’t be reopened or stays unsafe past the max pause,
                the run ends with the roof left closed.
              </p>
            </div>
            <Toggle
              checked={reopenWhenSafe}
              onChange={onToggleReopenWhenSafe}
              disabled={busy || !closeOnUnsafe || !canSafety}
              label="Reopen roof when safe again"
              showState
            />
          </div>

          <div className="flex items-start justify-between gap-3 py-2 border-t border-line">
            <div className="min-w-0">
              <div className="text-sm text-ink">Close roof at end-of-night</div>
              <p className="text-[11px] text-dim max-w-md">
                Close the roof when a sequence finishes (requires park-on-finish).
              </p>
            </div>
            <Toggle
              checked={closeWhenDone}
              onChange={onToggleCloseWhenDone}
              disabled={busy || !canSafety}
              label="Close roof at end-of-night"
              showState
            />
          </div>

        </div>

        {/* A refused SETTING reports itself here, under the switch that snapped
            back — not at the foot of the panel below the manual close. */}
        {err?.where === "roof" && (
          <p className="text-xs text-bad inline-flex items-center gap-1.5 mt-2">
            <Icon name="alert" size={13} className="shrink-0" />
            {err.msg}
          </p>
        )}

        {!canSafety ? (
          <div className="flex items-center gap-3 border border-line2 bg-raise/40 px-3 py-2 text-xs mt-3">
            <Icon name="lock" size={14} className="text-dim shrink-0" />
            <span className="text-dim">
              Changing what the roof does BY ITSELF needs{" "}
              {accessPhrase("config.safety")} (config.safety) — an admin. The
              three settings above are shown for reference. Closing the roof by
              hand is a separate permission and is not affected by this.
            </span>
          </div>
        ) : !domeConnected ? (
          <div className="flex items-center gap-3 border border-line2 bg-raise/40 px-3 py-2 text-xs mt-3">
            <Icon name="lock" size={14} className="text-dim shrink-0" />
            <span className="text-dim">
              No dome/roof device is connected, so these are read-only — the
              server has nothing to close. Add a dome under Equipment and they
              become settable.
            </span>
          </div>
        ) : null}

        {/* MANUAL CLOSE — deliberately OUTSIDE the config wrapper above. This is
            not a settings write: POST /api/dome/close is control.mount, which an
            operator holds and an operator is who is standing at the rig when it
            starts raining. It is also the only manual roof close in the app. */}
        <div className="flex items-center justify-between gap-3 pt-3 mt-3 border-t border-line">
          <span className="text-[11px] text-dim max-w-md">
            {ourClose
              ? "Parking the mount, then closing. The shutter status above updates as it moves."
              : closeLock
                ? closeLock
                : gotoBusy
                  ? "The mount is moving — closing the roof stops that move first. Hold the button to confirm, or stop the mount yourself and close after."
                  : "Park the mount, then close the roof now."}
          </span>
          {/* Hard-disabled for OUR OWN close (replace=True: a second tap cancels
              the park in flight) and for a missing capability or device. NOT for
              somebody else's slew — that one is reachable behind a hold. */}
          <button
            type="button"
            className="btn min-h-[44px] sm:min-h-0 inline-flex items-center gap-2"
            onClick={onCloseRoofNow}
            aria-busy={ourClose || undefined}
            aria-disabled={!!closeLock || ourClose}
            title={closeLock ?? undefined}
            disabled={!!closeLock || ourClose}
          >
            <Icon name="lock" size={15} />
            {ourClose ? "Closing…" : "Close roof now"}
          </button>
        </div>
      </div>

      {savedAt && !busy && !err && (
        <p className="text-[11px] text-good inline-flex items-center gap-1.5 mt-3">
          <Icon name="check" size={13} /> Saved
        </p>
      )}
    </Panel>
  );
}
