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

import { useEffect, useState, type JSX } from "react";
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

  // Draft mirrors the server block; re-seed whenever a fresh config lands (a save
  // broadcasts `config` over the WS → loadConfig refreshes the store slice).
  const [avoidance, setAvoidance] = useState(true);
  const [coneDeg, setConeDeg] = useState(30);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
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
  const [roofBusy, setRoofBusy] = useState(false);
  useEffect(() => {
    setCloseOnUnsafe(seedCloseOnUnsafe);
    setCloseWhenDone(seedCloseWhenDone);
    setReopenWhenSafe(seedReopenWhenSafe);
  }, [seedCloseOnUnsafe, seedCloseWhenDone, seedReopenWhenSafe]);
  useEffect(() => {
    let live = true;
    getDomeState()
      .then((d) => {
        if (live) setDome(d);
      })
      .catch(() => {
        /* no dome / offline — the honest-disabled note covers it */
      });
    return () => {
      live = false;
    };
  }, []);

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
      setErr(msg);
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

  const persistDomeFlag = async (patch: Partial<SafetyConfig>) => {
    if (busy) return;
    setErr(null);
    setBusy(true);
    try {
      const body: SafetyConfig = { ...safety, ...patch };
      await setSafetyConfig(body);
      await useStore.getState().loadConfig();
      setSavedAt(Date.now());
    } catch (e) {
      const msg =
        e instanceof ApiError ? e.message || "Could not save." : "Could not save.";
      setErr(msg);
    } finally {
      setBusy(false);
    }
  };

  const onToggleCloseOnUnsafe = async (on: boolean) => {
    setCloseOnUnsafe(on);
    await persistDomeFlag({ close_dome_on_unsafe: on });
  };
  const onToggleCloseWhenDone = async (on: boolean) => {
    setCloseWhenDone(on);
    await persistDomeFlag({ close_dome_when_done: on });
  };
  const onToggleReopenWhenSafe = async (on: boolean) => {
    setReopenWhenSafe(on);
    await persistDomeFlag({ reopen_dome_when_safe: on });
  };

  // Manual close: fence + park + close via the tested ordering guard on the
  // server. Toast the outcome (the shutter status line reflects progress).
  const onCloseRoofNow = async () => {
    if (roofBusy || !domeConnected) return;
    setRoofBusy(true);
    try {
      await closeDome();
      useStore.getState().enqueueToast({
        level: "info",
        title: "Closing the roof — parking the mount first.",
      });
      // refresh the shutter status shortly after the close is commanded.
      setTimeout(() => {
        getDomeState().then(setDome).catch(() => {});
      }, 1500);
    } catch (e) {
      const msg =
        e instanceof ApiError ? e.message || "Could not close the roof." : "Could not close the roof.";
      useStore.getState().enqueueToast({ level: "error", title: msg });
    } finally {
      setRoofBusy(false);
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
        <div className="flex items-end">
          <button
            type="button"
            className="btn btn-accent min-h-[44px] sm:min-h-0 inline-flex items-center gap-2"
            onClick={saveCone}
            disabled={busy || !canOverride || !dirty || !avoidance}
          >
            <Icon name="check" size={15} />
            {busy ? "Saving…" : "Save angle"}
          </button>
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

        {/* honest-disabled (§11.8) when no dome is connected: dim + lock +
            aria-disabled + title — NOT the native disabled attribute. */}
        <div
          aria-disabled={!domeConnected}
          title={domeConnected ? undefined : "Connect a dome/roof device first"}
          className={
            domeConnected ? "mt-3" : "mt-3 opacity-50 pointer-events-none select-none"
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
            <Toggle
              checked={closeOnUnsafe}
              onChange={onToggleCloseOnUnsafe}
              disabled={busy}
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
              disabled={busy || !closeOnUnsafe}
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
              disabled={busy}
              label="Close roof at end-of-night"
              showState
            />
          </div>

          <div className="flex items-center justify-between gap-3 pt-3 border-t border-line">
            <span className="text-[11px] text-dim">
              Park the mount, then close the roof now.
            </span>
            <button
              type="button"
              className="btn min-h-[44px] sm:min-h-0 inline-flex items-center gap-2"
              onClick={onCloseRoofNow}
              aria-disabled={roofBusy || !domeConnected}
              disabled={roofBusy}
            >
              <Icon name="lock" size={15} />
              {roofBusy ? "Closing…" : "Close roof now"}
            </button>
          </div>
        </div>

        {!domeConnected && (
          <div className="flex items-center gap-3 border border-line2 bg-raise/40 px-3 py-2 text-xs mt-3">
            <Icon name="lock" size={14} className="text-dim shrink-0" />
            <span className="text-dim">
              No dome/roof device is connected. Connect one to enable auto-close
              and the manual close. The settings above still save for when a roof
              is added.
            </span>
          </div>
        )}
      </div>

      {err && (
        <p className="text-xs text-bad inline-flex items-center gap-1.5 mt-3">
          <Icon name="alert" size={13} className="shrink-0" />
          {err}
        </p>
      )}
      {savedAt && !busy && !err && (
        <p className="text-[11px] text-good inline-flex items-center gap-1.5 mt-3">
          <Icon name="check" size={13} /> Saved
        </p>
      )}
    </Panel>
  );
}
