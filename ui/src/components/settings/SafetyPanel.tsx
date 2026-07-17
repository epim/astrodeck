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
import { setSafetyConfig } from "../../api/backends";
import { ApiError } from "../../api";
import { useConfig, useStore } from "../../store";
import { accessPhrase, useCan } from "../../lib/caps";
import { confirmDialog } from "../ConfirmDialog";
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

  if (!safety) {
    return (
      <Panel title="Sun avoidance">
        <p className="text-xs text-dim">Loading safety configuration…</p>
      </Panel>
    );
  }

  const dirty =
    avoidance !== seedAvoidance || Math.abs(coneDeg - seedCone) > 1e-9;

  // Persist: echo the FULL current safety block with our two edits applied. The
  // server replaces SafetyConfig wholesale, so anything we omit would be lost.
  const persist = async (next: { avoidance: boolean; cone: number }) => {
    if (busy) return;
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
    } catch (e) {
      // 403 = the override cap was lost mid-session (e.g. signed out). Keep the
      // draft so the user sees what they tried; never silently revert.
      const msg =
        e instanceof ApiError
          ? e.status === 403
            ? "Sun avoidance can only be changed by an admin (config.solar_override)."
            : e.message || "Could not save."
          : "Could not save.";
      setErr(msg);
    } finally {
      setBusy(false);
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
      await persist({ avoidance: false, cone: coneDeg });
    } else {
      // Re-arming protection — always safe, no confirm.
      setAvoidance(true);
      await persist({ avoidance: true, cone: coneDeg });
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
                avoidance
                  ? "text-good border-good/50"
                  : "text-bad border-bad/60"
              }`}
            >
              {avoidance ? "Armed" : "Disarmed"}
            </span>
          </div>
          <p className="text-[11px] text-dim max-w-md">
            {avoidance
              ? "On — the mount refuses to point within the exclusion cone of the Sun."
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
