// SafetyLimitsPanel.tsx — the safety limits that decide when a night stops.
//
// Every field here was config-file-and-API-only until now. They are not obscure
// tuning knobs: min_alt_deg is the floor that stops the mount driving the OTA
// into the pier, and on_unsafe decides whether a rain trip pauses your night or
// parks and warms the camera. Leaving them unreachable meant the only way to
// change them was to stop the server and edit JSON.
//
// RBAC: the whole block is gated on config.safety, which only `admin` holds
// (auth/capabilities.py: operator's set has no config.* at all). A non-admin
// sees the real values, read-only, with the reason named — never a 403 on tap.
//
// SAVE contract: the server's set_safety REPLACES SafetyConfig wholesale, so we
// echo the full current block with our edits applied (the SafetyPanel/auth-panel
// precedent). The two solar fields are deliberately NOT edited here — they need
// config.solar_override and have their own panel — but they ride along in the
// echo, unchanged, which the server permits.

import { useEffect, useState, type JSX } from "react";
import type { SafetyConfig } from "../../types";
import { setSafetyConfig } from "../../api/backends";
import { ApiError } from "../../api";
import { useConfig, useStore } from "../../store";
import { accessPhrase, useCan } from "../../lib/caps";
import { Panel, Field, Toggle } from "../ui";
import { Icon } from "../icons";

/** The preset table, mirroring server `config.SAFETY_PRESETS`. Picking one fills
 *  the advanced fields below; touching any of them afterwards flips the preset to
 *  "custom", because a preset label that no longer describes the values is worse
 *  than no label. Kept in the UI because the server stores `preset` but never
 *  applies it — the values themselves are the truth either way. */
const PRESETS: Record<string, Partial<SafetyConfig>> = {
  backyard: {
    on_unsafe: "pause",
    unsafe_consecutive: 3,
    resume_when_safe: true,
    resume_safe_consecutive: 3,
    max_pause_min: 120,
  },
  remote: {
    on_unsafe: "abort_park_warm",
    unsafe_consecutive: 2,
    resume_when_safe: false,
    resume_safe_consecutive: 3,
    max_pause_min: 0,
  },
};

const PRESET_BLURB: Record<string, string> = {
  backyard:
    "You are nearby. A cloud or rain trip pauses and waits, and picks back up when it clears.",
  remote:
    "Nobody is there. A trip ends the night: park the mount and warm the camera rather than wait.",
  custom: "Your own combination of the settings below.",
};

const ON_UNSAFE: { value: SafetyConfig["on_unsafe"]; label: string; blurb: string }[] = [
  { value: "warn", label: "Warn only", blurb: "Keep shooting, just say so. For testing a new monitor." },
  { value: "pause", label: "Pause", blurb: "Stop starting new frames, hold, and resume when it clears." },
  { value: "park", label: "Park", blurb: "Park the mount. The camera stays cold, so you can restart quickly." },
  { value: "abort_park_warm", label: "Park and warm", blurb: "End the night: park, then warm the camera at a safe ramp." },
];

const TWILIGHT: { value: number; label: string; blurb: string }[] = [
  { value: -6, label: "Civil (−6°)", blurb: "Earliest start. Bright sky; only for the Moon and planets." },
  { value: -12, label: "Nautical (−12°)", blurb: "The usual compromise — a longer night, some sky glow early." },
  { value: -18, label: "Astronomical (−18°)", blurb: "Truly dark sky. The shortest window, and the cleanest data." },
];

export default function SafetyLimitsPanel(): JSX.Element {
  const config = useConfig();
  const safety = config?.safety;
  const canEdit = useCan("config.safety");

  const [draft, setDraft] = useState<SafetyConfig | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [savedAt, setSavedAt] = useState<number | null>(null);

  // Re-seed whenever a fresh config lands (a save broadcasts over the WS).
  useEffect(() => {
    if (safety) setDraft({ ...safety });
    setErr(null);
    setSavedAt(null);
  }, [safety]);

  if (!safety || !draft) {
    return (
      <Panel title="Safety limits">
        <p className="text-xs text-dim">Loading safety configuration…</p>
      </Panel>
    );
  }

  // Any edit to a preset-owned field means the preset no longer describes the
  // values, so the label becomes "custom" rather than lying.
  const patch = (p: Partial<SafetyConfig>) =>
    setDraft((d) => (d ? { ...d, ...p, preset: "custom" } : d));
  // Fields the presets do not own (floors, twilight, polling) keep the label.
  const patchKeepingPreset = (p: Partial<SafetyConfig>) =>
    setDraft((d) => (d ? { ...d, ...p } : d));

  const applyPreset = (name: SafetyConfig["preset"]) =>
    setDraft((d) => (d ? { ...d, ...(PRESETS[name] ?? {}), preset: name } : d));

  const dirty = JSON.stringify(draft) !== JSON.stringify(safety);

  const save = async () => {
    if (busy || !dirty) return;
    setErr(null);
    setBusy(true);
    try {
      await setSafetyConfig(draft);
      await useStore.getState().loadConfig();
      setSavedAt(Date.now());
    } catch (e) {
      setErr(
        e instanceof ApiError
          ? e.status === 403
            ? "Safety limits can only be changed by an admin (config.safety)."
            : e.message || "Could not save."
          : "Could not save.",
      );
    } finally {
      setBusy(false);
    }
  };

  const floorOn = (draft.min_alt_deg ?? 0) > 0;
  const ro = !canEdit;

  return (
    <Panel
      title="Safety limits"
      right={
        <span className="inline-flex items-center gap-1.5 text-[11px] text-dim">
          <Icon name="shield" size={14} />
          {draft.preset === "custom" ? "Custom" : `${draft.preset} preset`}
        </span>
      }
    >
      <p className="text-[11px] text-dim leading-relaxed max-w-xl mb-4">
        What the rig does when conditions turn, and how low it is willing to
        point. These apply to every plan — a plan can be stricter, never looser.
      </p>

      {/* --------------------------------------------------------- master */}
      <div className="flex items-start justify-between gap-3 py-3 border-t border-line">
        <div className="min-w-0">
          <div className="text-sm text-ink">Safety monitoring</div>
          <p className="text-[11px] text-dim max-w-md">
            {draft.enabled
              ? "On — a connected safety monitor can stop the run."
              : "OFF — nothing here can stop a run. Rain, cloud and the altitude floor are all ignored."}
          </p>
        </div>
        <Toggle
          checked={draft.enabled}
          onChange={(v) => patchKeepingPreset({ enabled: v })}
          disabled={busy || ro}
          label="Safety monitoring"
          showState
        />
      </div>

      {!draft.enabled && (
        <div className="flex items-start gap-3 border border-bad/60 bg-bad/10 px-3 py-2 text-xs mb-1">
          <Icon name="alert" size={14} className="text-bad shrink-0 mt-0.5" />
          <span className="text-ink">
            <span className="mono tracking-[0.12em] uppercase text-bad">
              Safety off
            </span>{" "}
            — the settings below are stored but not enforced. An unattended run
            has nothing watching it.
          </span>
        </div>
      )}

      {/* ---------------------------------------------------------- preset */}
      <div className="pt-4 border-t border-line mt-1">
        <Field
          label="Preset"
          hint="A starting point for the response settings below. Changing any of them switches this to Custom."
        >
          <select
            className="field"
            value={draft.preset}
            aria-label="Safety preset"
            disabled={busy || ro}
            onChange={(e) => applyPreset(e.target.value as SafetyConfig["preset"])}
          >
            <option value="backyard">Backyard — you are nearby</option>
            <option value="remote">Remote — nobody is there</option>
            <option value="custom">Custom</option>
          </select>
        </Field>
        <p className="text-[11px] text-dim mt-1.5 max-w-xl">
          {PRESET_BLURB[draft.preset] ?? PRESET_BLURB.custom}
        </p>
      </div>

      {/* --------------------------------------------------- altitude floor */}
      <div className="pt-4 border-t border-line mt-4">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="text-sm text-ink">Altitude floor</div>
            <p className="text-[11px] text-dim max-w-xl">
              The lowest the <span className="text-ink">mount</span> is allowed
              to point. This is the pier-collision guard, not a "is my target
              worth shooting" rule — for that, set a per-target minimum altitude
              in the plan, or the site horizon under Observing Site.
            </p>
          </div>
          <Toggle
            checked={floorOn}
            onChange={(v) => patchKeepingPreset({ min_alt_deg: v ? 10 : 0 })}
            disabled={busy || ro}
            label="Altitude floor"
            showState
          />
        </div>
        {floorOn && (
          <div className="grid gap-3 sm:grid-cols-2 mt-3">
            <Field
              label="Floor (deg above horizon)"
              hint="A run stops rather than let the mount go below this. 10–20° suits most piers; a fork on a wedge may need more."
            >
              <input
                className="field"
                type="number"
                min={0}
                max={89}
                step={1}
                value={draft.min_alt_deg}
                disabled={busy || ro}
                onChange={(e) =>
                  patchKeepingPreset({
                    min_alt_deg: Math.min(89, Math.max(0, Number(e.target.value) || 0)),
                  })
                }
              />
            </Field>
          </div>
        )}
      </div>

      {/* -------------------------------------------------------- twilight */}
      <div className="pt-4 border-t border-line mt-4">
        <Field
          label="Night starts at"
          hint="How far the Sun must be below the horizon before the sequencer will start light frames, and when it stops them at dawn."
        >
          <select
            className="field"
            value={String(draft.twilight_deg)}
            aria-label="Twilight threshold"
            disabled={busy || ro}
            onChange={(e) =>
              patchKeepingPreset({ twilight_deg: Number(e.target.value) })
            }
          >
            {TWILIGHT.map((t) => (
              <option key={t.value} value={t.value}>
                {t.label}
              </option>
            ))}
          </select>
        </Field>
        <p className="text-[11px] text-dim mt-1.5 max-w-xl">
          {TWILIGHT.find((t) => t.value === draft.twilight_deg)?.blurb ??
            `Custom threshold: ${draft.twilight_deg}° below the horizon.`}
        </p>
      </div>

      {/* -------------------------------------------- response when unsafe */}
      <div className="pt-4 border-t border-line mt-4">
        <h3 className="text-sm text-ink mb-2">When conditions turn unsafe</h3>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field
            label="Do this"
            hint="The response to a confirmed unsafe reading. Park and warm ends the night; pause waits it out."
          >
            <select
              className="field"
              value={draft.on_unsafe}
              aria-label="Action when unsafe"
              disabled={busy || ro}
              onChange={(e) =>
                patch({ on_unsafe: e.target.value as SafetyConfig["on_unsafe"] })
              }
            >
              {ON_UNSAFE.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </Field>
          <Field
            label="After this many readings"
            hint="Consecutive unsafe readings required before acting. Higher rides out a single spurious reading; lower reacts sooner to real rain."
          >
            <input
              className="field"
              type="number"
              min={1}
              max={20}
              step={1}
              value={draft.unsafe_consecutive}
              disabled={busy || ro}
              onChange={(e) =>
                patch({
                  unsafe_consecutive: Math.min(20, Math.max(1, Number(e.target.value) || 1)),
                })
              }
            />
          </Field>
        </div>
        <p className="text-[11px] text-dim mt-1.5 max-w-xl">
          {ON_UNSAFE.find((o) => o.value === draft.on_unsafe)?.blurb}
        </p>

        <div className="flex items-start justify-between gap-3 py-3 mt-2 border-t border-line">
          <div className="min-w-0">
            <div className="text-sm text-ink">Resume when it clears</div>
            <p className="text-[11px] text-dim max-w-md">
              {draft.resume_when_safe
                ? "The run picks up again once readings are safe for long enough."
                : "The run stays stopped even if conditions recover. You restart it yourself."}
            </p>
          </div>
          <Toggle
            checked={draft.resume_when_safe}
            onChange={(v) => patch({ resume_when_safe: v })}
            disabled={busy || ro}
            label="Resume when safe"
            showState
          />
        </div>

        {draft.resume_when_safe && (
          <div className="grid gap-3 sm:grid-cols-2">
            <Field
              label="Safe readings needed"
              hint="Consecutive safe readings before resuming. Guards against resuming into a gap between two rain cells."
            >
              <input
                className="field"
                type="number"
                min={1}
                max={20}
                step={1}
                value={draft.resume_safe_consecutive}
                disabled={busy || ro}
                onChange={(e) =>
                  patch({
                    resume_safe_consecutive: Math.min(20, Math.max(1, Number(e.target.value) || 1)),
                  })
                }
              />
            </Field>
            <Field
              label="Give up after (minutes)"
              hint="How long to hold paused before escalating to a park. 0 waits indefinitely — only sane if somebody is there."
            >
              <input
                className="field"
                type="number"
                min={0}
                max={1440}
                step={5}
                value={draft.max_pause_min}
                disabled={busy || ro}
                onChange={(e) =>
                  patch({
                    max_pause_min: Math.min(1440, Math.max(0, Number(e.target.value) || 0)),
                  })
                }
              />
            </Field>
          </div>
        )}
        {draft.resume_when_safe && draft.max_pause_min === 0 && (
          <p className="text-[11px] text-warn mt-1.5 max-w-xl inline-flex items-start gap-1.5">
            <Icon name="alert" size={13} className="shrink-0 mt-0.5" />
            Set to 0 — a paused run will wait forever with the gear out under an
            open sky. Give it a limit if the rig is unattended.
          </p>
        )}
      </div>

      {/* ------------------------------------------------------ no-go wedges */}
      <div className="pt-4 border-t border-line mt-4">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="text-sm text-ink">Obstructions</div>
            <p className="text-[11px] text-dim max-w-xl">
              Azimuth wedges the mount must stay above — a pier, a wall, a
              chimney, the neighbour's tree. Edges are hard: one degree outside
              the wedge the floor is gone. Azimuth is measured clockwise from
              north, so 180 is due south.
            </p>
          </div>
          {canEdit && (
            <button
              type="button"
              className="btn min-h-11 !px-3 text-[11px] shrink-0"
              onClick={() =>
                patchKeepingPreset({
                  nogo_box: [...(draft.nogo_box ?? []),
                             { az_min: 170, az_max: 190, alt_max: 30 }],
                })
              }
              disabled={busy}
            >
              Add
            </button>
          )}
        </div>

        {(draft.nogo_box ?? []).length === 0 ? (
          <p className="text-[11px] text-dim mt-2">
            None — only the altitude floor and the site horizon apply.
          </p>
        ) : (
          <div className="mt-3 flex flex-col gap-2">
            <div className="grid grid-cols-[1fr_1fr_1fr_2.75rem] gap-2 label !text-[9px]">
              <span>az from</span>
              <span>az to</span>
              <span>min alt</span>
              <span />
            </div>
            {(draft.nogo_box ?? []).map((box, i) => {
              const setBox = (p: Partial<typeof box>) =>
                patchKeepingPreset({
                  nogo_box: (draft.nogo_box ?? []).map((b, j) =>
                    j === i ? { ...b, ...p } : b),
                });
              return (
                <div key={i} className="grid grid-cols-[1fr_1fr_1fr_2.75rem] gap-2 items-center">
                  <input
                    className="field" type="number" min={0} max={360} step={1}
                    aria-label={`Obstruction ${i + 1} azimuth from`}
                    value={box.az_min} disabled={busy || ro}
                    onChange={(e) => setBox({ az_min: Number(e.target.value) || 0 })}
                  />
                  <input
                    className="field" type="number" min={0} max={360} step={1}
                    aria-label={`Obstruction ${i + 1} azimuth to`}
                    value={box.az_max} disabled={busy || ro}
                    onChange={(e) => setBox({ az_max: Number(e.target.value) || 0 })}
                  />
                  <input
                    className="field" type="number" min={0} max={89} step={1}
                    aria-label={`Obstruction ${i + 1} minimum altitude`}
                    value={box.alt_max} disabled={busy || ro}
                    onChange={(e) => setBox({ alt_max: Number(e.target.value) || 0 })}
                  />
                  {canEdit && (
                    <button
                      type="button"
                      className="btn min-h-11 !px-0 text-[11px]"
                      aria-label={`Remove obstruction ${i + 1}`}
                      disabled={busy}
                      onClick={() =>
                        patchKeepingPreset({
                          nogo_box: (draft.nogo_box ?? []).filter((_, j) => j !== i),
                        })
                      }
                    >
                      ✕
                    </button>
                  )}
                </div>
              );
            })}
            <p className="text-[11px] text-dim">
              A wedge may wrap through north: 350 → 10 is the 20° span across due
              north. Setting both azimuths the same covers the whole sky.
            </p>
          </div>
        )}
      </div>

      {/* -------------------------------------------------------- advanced */}
      <details className="pt-4 border-t border-line mt-4">
        <summary className="text-sm text-ink cursor-pointer select-none min-h-11 flex items-center">
          Advanced
        </summary>
        <div className="mt-2 flex flex-col">
          <div className="flex items-start justify-between gap-3 py-3">
            <div className="min-w-0">
              <div className="text-sm text-ink">Check before every frame</div>
              <p className="text-[11px] text-dim max-w-md">
                {draft.poll_each_frame
                  ? "The safety monitor is read before each exposure starts."
                  : "OFF — safety is only read on the background poll, so an unsafe condition can be up to one exposure late."}
              </p>
            </div>
            <Toggle
              checked={draft.poll_each_frame}
              onChange={(v) => patchKeepingPreset({ poll_each_frame: v })}
              disabled={busy || ro}
              label="Check before every frame"
              showState
            />
          </div>
          <div className="flex items-start justify-between gap-3 py-3 border-t border-line">
            <div className="min-w-0">
              <div className="text-sm text-ink">Enforce mount pier limits</div>
              <p className="text-[11px] text-dim max-w-md">
                Honour the pier-side limits the mount reports. Has no effect on a
                mount that does not report a pier side.
              </p>
            </div>
            <Toggle
              checked={draft.enforce_pier_limits}
              onChange={(v) => patchKeepingPreset({ enforce_pier_limits: v })}
              disabled={busy || ro}
              label="Enforce mount pier limits"
              showState
            />
          </div>
        </div>
      </details>

      {/* ------------------------------------------------------------ save */}
      {canEdit && (
        <div className="flex items-center gap-3 pt-4 border-t border-line mt-4">
          <button
            type="button"
            className="btn btn-accent min-h-[44px] sm:min-h-0 inline-flex items-center gap-2"
            onClick={save}
            disabled={busy || !dirty}
          >
            <Icon name="check" size={15} />
            {busy ? "Saving…" : "Save safety limits"}
          </button>
          {dirty && !busy && (
            <span className="text-[11px] text-warn">Unsaved changes</span>
          )}
          {savedAt && !dirty && !busy && !err && (
            <span className="text-[11px] text-good inline-flex items-center gap-1.5">
              <Icon name="check" size={13} /> Saved
            </span>
          )}
        </div>
      )}

      {!canEdit && (
        <div className="flex items-center gap-3 border border-line2 bg-raise/40 px-3 py-2 text-xs mt-4">
          <Icon name="lock" size={14} className="text-dim shrink-0" />
          <span className="text-dim">
            Changing safety limits needs {accessPhrase("config.safety")}{" "}
            (config.safety) — an admin. The current settings are shown for
            reference.
          </span>
        </div>
      )}

      {err && (
        <p className="text-xs text-bad inline-flex items-center gap-1.5 mt-3">
          <Icon name="alert" size={13} className="shrink-0" />
          {err}
        </p>
      )}
    </Panel>
  );
}
