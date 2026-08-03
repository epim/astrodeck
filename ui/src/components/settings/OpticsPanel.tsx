// OpticsPanel.tsx — the imaging train: focal length, the scope's name for the
// FITS TELESCOP card, and whether pixel size and sensor dimensions come from the
// camera or are pinned by hand.
//
// Focal length was reachable only from an inline field in the Atlas sidebar —
// findable if you already knew, invisible otherwise. telescope_name and
// auto_from_camera had no UI at all, so a rig whose camera reports the wrong
// pixel size could not be corrected without editing the config file, and every
// delivered FITS shipped without a TELESCOP card.
//
// PUT /api/optics carries an optimistic-concurrency version token: a 409 means
// somebody else saved first, and we surface that rather than clobbering them.
//
// #129 — the same bug class as the Tasks panel, never reported here only
// because nobody noticed. Every field below is bound to `config.optics`, the
// GLOBAL block. When the active profile carries an `optics` block the server
// swaps the WHOLE object (`profiles.resolve_optics`), and that swapped object
// is what reaches plate-solving's FOV hint, the FITS TELESCOP card, the HFR
// arcsec readout and native TPPA. So this panel could show a 530 mm scope while
// every solve in the session assumed 250 mm.
//
// The fix is disclosure, not rebinding: the inputs still edit the global layer,
// because a profile carrying its own optics is the INTENDED way to run two
// telescopes and making this panel read-only under one would strand that user.
// What changes is that the panel now says which layer is in force, prints the
// running value wherever it differs from the box under it, and warns — on the
// save button, where the false belief would otherwise be formed — that saving
// will not change what the rig runs until the override is cleared.

import { useEffect, useState, type JSX } from "react";
import type { Optics } from "../../types";
import { api, ApiError } from "../../api";
import { clearProfileOverrides } from "../../api/backends";
import { useConfig, useStore } from "../../store";
import { accessPhrase, useCan } from "../../lib/caps";
import {
  entryOf,
  opticsKey,
  opticsOverridden,
  opticsOverrideProfile,
  showValue,
  type OpticsKey,
} from "../../lib/effective";
import { Panel, Field, Toggle } from "../ui";
import { Icon } from "../icons";
import { LayerChip, RunningNote, useClearOverride } from "../OverrideNote";

/** Per-field units for the provenance sentences. `showValue` handles the
 *  empty/boolean cases before these ever run, so each one only has to add the
 *  unit — a bare "250" in a sentence about focal length is not an answer. */
const FMT: Record<OpticsKey, (v: unknown) => string> = {
  focal_length_mm: (v) => `${v} mm`,
  pixel_size_um: (v) => `${v} µm`,
  sensor_width_px: (v) => `${v} px`,
  sensor_height_px: (v) => `${v} px`,
  auto_from_camera: (v) => (v ? "on" : "off"),
  guide_focal_length_mm: (v) => `${v} mm`,
  telescope_name: (v) => `“${v}”`,
};

/** The override banner is the answer to "what is my rig actually using", so it
 *  lists ALL SEVEN fields rather than the handful the user was looking at —
 *  the whole point of the whole-block swap is that it reaches fields nobody
 *  thought they were changing. Ordered as the form reads, not as the model
 *  declares. */
const BANNER_KEYS: [OpticsKey, string][] = [
  ["focal_length_mm", "Focal length"],
  ["telescope_name", "Telescope name"],
  ["auto_from_camera", "Sensor from camera"],
  ["pixel_size_um", "Pixel size"],
  ["sensor_width_px", "Sensor width"],
  ["sensor_height_px", "Sensor height"],
  ["guide_focal_length_mm", "Guide scope focal length"],
];

export default function OpticsPanel(): JSX.Element {
  const config = useConfig();
  const optics = config?.optics;
  const computed = config?.optics_computed;
  const canEdit = useCan("config.site_optics");
  const overridden = opticsOverridden(config);
  const overrideProfile = opticsOverrideProfile(config);
  const overrideProfileId = entryOf(config, opticsKey("focal_length_mm"))
    ?.profile_id;
  const { clear, clearing, error: clearErr } = useClearOverride();

  // Seeded on the FIRST render, not only from the effect below. The effect is
  // still what re-seeds on every later config change (our own save, another
  // client's, a profile activate), but starting at null meant the panel
  // rendered its "Loading optics…" placeholder for one frame on every mount
  // even when the config was already in the store — and made the panel
  // untestable without a client render loop, which is how it went this long
  // without one.
  const [draft, setDraft] = useState<Optics | null>(() =>
    optics ? ({ ...optics } as Optics) : null,
  );
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [savedAt, setSavedAt] = useState<number | null>(null);

  const seed = JSON.stringify(optics ?? null);
  useEffect(() => {
    setDraft(optics ? (JSON.parse(seed) as Optics) : null);
    setErr(null);
    setSavedAt(null);
  }, [seed, optics]);

  if (!optics || !draft) {
    return (
      <Panel title="Imaging train">
        <p className="text-xs text-dim">Loading optics…</p>
      </Panel>
    );
  }

  const patch = (p: Partial<Optics>) => setDraft((d) => (d ? { ...d, ...p } : d));
  const dirty = JSON.stringify(draft) !== seed;
  const ro = !canEdit;
  const focalInvalid = !(draft.focal_length_mm > 0 && draft.focal_length_mm <= 20000);

  const save = async () => {
    if (busy || !dirty || focalInvalid) return;
    setErr(null);
    setBusy(true);
    try {
      await api.put("/api/optics", {
        optics: draft,
        version: config?.version ?? null,
      });
      await useStore.getState().loadConfig();
      setSavedAt(Date.now());
    } catch (e) {
      setErr(
        e instanceof ApiError
          ? e.status === 409
            ? "Someone else changed the optics while you were editing. Reload to see their values, then re-apply yours."
            : e.status === 403
              ? `Changing optics needs ${accessPhrase("config.site_optics")}.`
              : e.message || "Could not save."
          : "Could not save.",
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <Panel
      title="Imaging train"
      right={
        <span className="inline-flex items-center gap-2">
          {overridden && (
            <LayerChip entry={entryOf(config, opticsKey("focal_length_mm"))} />
          )}
          {computed?.image_scale_arcsec_px ? (
            <span className="mono text-[11px] text-dim">
              {computed.image_scale_arcsec_px.toFixed(2)}″/px
            </span>
          ) : null}
        </span>
      }
    >
      <p className="text-[11px] text-dim leading-relaxed max-w-xl mb-1">
        Focal length sets the image scale every plate solve, framing overlay and
        guiding readout depends on. Get it wrong and solves fail with no obvious
        reason.
      </p>

      {/* ------------------------------------------- the profile-override banner
          Stated ONCE, at the top, because the override is one fact about the
          whole panel rather than seven facts about seven fields: a profile
          optics block is swapped WHOLE, so a profile that only meant to change
          a focal length also reverts the pixel size to ITS default. Enumerating
          the differing keys is the part that carries information — "overridden"
          alone would leave the user to diff two numbers they cannot both see. */}
      {overridden && (
        <div className="border border-warn/60 border-dashed bg-raise px-3 py-2.5 mt-3 text-[11px] leading-snug">
          <div className="flex items-start gap-2">
            <Icon name="alert" size={13} className="text-warn shrink-0 mt-0.5" />
            <div className="min-w-0">
              <p className="text-ink">
                The rig is using the optics from equipment profile{" "}
                <span className="text-warn">“{overrideProfile ?? "(unnamed)"}”</span>
                , not the values in this panel. A profile's optics block replaces
                every field at once, including the ones it never set.
              </p>
              <ul className="mt-2 flex flex-col gap-0.5">
                {BANNER_KEYS.map(([key, label]) => {
                  const e = entryOf(config, opticsKey(key));
                  if (!e) return null;
                  const same = Object.is(e.value, e.config);
                  return (
                    <li key={key} className="text-dim">
                      {label}:{" "}
                      <span className="mono text-ink">
                        {showValue(e.value, FMT[key])}
                      </span>
                      {same ? (
                        " — same as this panel"
                      ) : (
                        <>
                          {" — this panel shows "}
                          <span className="mono">{showValue(e.config, FMT[key])}</span>
                        </>
                      )}
                    </li>
                  );
                })}
              </ul>
              {canEdit && overrideProfileId && (
                <button
                  type="button"
                  className="btn !py-1 !px-2 text-[11px] inline-flex items-center gap-1.5 mt-2.5"
                  disabled={clearing}
                  onClick={() =>
                    void clear(() =>
                      clearProfileOverrides(overrideProfileId, { optics: true }),
                    )
                  }
                >
                  <Icon name="x" size={11} />
                  {clearing
                    ? "Clearing…"
                    : "Drop the profile's optics and use these values"}
                </button>
              )}
              {clearErr && <p className="text-bad mt-1.5">{clearErr}</p>}
            </div>
          </div>
        </div>
      )}

      <div className="grid gap-3 sm:grid-cols-2 pt-3 border-t border-line mt-3">
        <Field
          label="Focal length (mm)"
          hint="The imaging train's effective focal length, including any reducer or extender. A 0.8× reducer on a 530 mm scope is 424, not 530."
        >
          <input
            className={`field ${focalInvalid ? "border-bad" : ""}`}
            type="number"
            min={1}
            max={20000}
            step={1}
            value={draft.focal_length_mm}
            disabled={busy || ro}
            aria-invalid={focalInvalid || undefined}
            onChange={(e) => patch({ focal_length_mm: Number(e.target.value) || 0 })}
          />
          {/* Per-field, and only where it says something the box does not: the
              running value when it differs, or the camera's value when the box
              reads 0. Seven copies of the banner's paragraph would be skipped. */}
          <RunningNote
            entry={entryOf(config, opticsKey("focal_length_mm"))}
            format={FMT.focal_length_mm}
          />
        </Field>
        <Field
          label="Telescope name"
          hint="Written to the FITS TELESCOP card, which stackers group by. Name the OPTICAL TUBE, not the mount — a wrong string here splits one target across two groups."
        >
          <input
            className="field"
            type="text"
            maxLength={64}
            placeholder="e.g. Askar FRA400"
            value={draft.telescope_name}
            disabled={busy || ro}
            onChange={(e) => patch({ telescope_name: e.target.value })}
          />
          <RunningNote
            entry={entryOf(config, opticsKey("telescope_name"))}
            format={FMT.telescope_name}
          />
        </Field>
      </div>

      {focalInvalid && (
        <p className="text-[11px] text-bad mt-2">
          Focal length must be between 1 and 20000 mm.
        </p>
      )}

      {/* ------------------------------------------------------- sensor source */}
      <div className="flex items-start justify-between gap-3 py-3 border-t border-line mt-4">
        <div className="min-w-0">
          <div className="text-sm text-ink">Take sensor details from the camera</div>
          <p className="text-[11px] text-dim max-w-md">
            {draft.auto_from_camera
              ? "Pixel size and sensor dimensions come from the connected camera. Right for almost every rig."
              : "Pinned by hand below. Use this when the driver reports the wrong pixel size, or to keep a scale while the camera is unplugged."}
          </p>
          {/* This toggle also decides whether the three sensor boxes below are
              even RENDERED, and it is bound to the global layer — so under a
              profile override the panel can hide the very fields whose running
              values differ. The banner above enumerates all five regardless,
              which is why it is unconditional. */}
          <RunningNote
            entry={entryOf(config, opticsKey("auto_from_camera"))}
            format={FMT.auto_from_camera}
          />
        </div>
        <Toggle
          checked={draft.auto_from_camera}
          onChange={(v) => patch({ auto_from_camera: v })}
          disabled={busy || ro}
          label="Take sensor details from the camera"
          showState
        />
      </div>

      {!draft.auto_from_camera && (
        <div className="grid gap-3 sm:grid-cols-3">
          <Field
            label="Pixel size (µm)"
            hint="Physical pixel pitch at bin 1. 0 falls back to the camera's own value."
          >
            <input
              className="field"
              type="number"
              min={0}
              max={50}
              step={0.01}
              value={draft.pixel_size_um}
              disabled={busy || ro}
              onChange={(e) => patch({ pixel_size_um: Number(e.target.value) || 0 })}
            />
            <RunningNote
              entry={entryOf(config, opticsKey("pixel_size_um"))}
              format={FMT.pixel_size_um}
            />
          </Field>
          <Field label="Sensor width (px)" hint="0 falls back to the camera.">
            <input
              className="field"
              type="number"
              min={0}
              step={1}
              value={draft.sensor_width_px}
              disabled={busy || ro}
              onChange={(e) => patch({ sensor_width_px: Number(e.target.value) || 0 })}
            />
            <RunningNote
              entry={entryOf(config, opticsKey("sensor_width_px"))}
              format={FMT.sensor_width_px}
            />
          </Field>
          <Field label="Sensor height (px)" hint="0 falls back to the camera.">
            <input
              className="field"
              type="number"
              min={0}
              step={1}
              value={draft.sensor_height_px}
              disabled={busy || ro}
              onChange={(e) => patch({ sensor_height_px: Number(e.target.value) || 0 })}
            />
            <RunningNote
              entry={entryOf(config, opticsKey("sensor_height_px"))}
              format={FMT.sensor_height_px}
            />
          </Field>
        </div>
      )}

      {/* -------------------------------------------------------- guide scope */}
      <div className="grid gap-3 sm:grid-cols-2 pt-3 border-t border-line mt-4">
        <Field
          label="Guide scope focal length (mm)"
          hint="The GUIDE train, not the imaging one. Without it, guiding RMS is reported in pixels at an assumed 1″/px rather than in real arcseconds. Leave blank if you don't guide."
        >
          <input
            className="field"
            type="number"
            min={0}
            max={20000}
            step={1}
            placeholder="not set"
            value={draft.guide_focal_length_mm ?? ""}
            disabled={busy || ro}
            onChange={(e) => {
              const raw = e.target.value.trim();
              patch({
                guide_focal_length_mm: raw === "" ? null : Number(raw) || null,
              });
            }}
          />
          <RunningNote
            entry={entryOf(config, opticsKey("guide_focal_length_mm"))}
            format={FMT.guide_focal_length_mm}
          />
        </Field>
      </div>

      {canEdit && (
        <div className="flex flex-col gap-2 pt-4 border-t border-line mt-4">
          <div className="flex items-center gap-3">
            <button
              type="button"
              className="btn btn-accent min-h-[44px] sm:min-h-0 inline-flex items-center gap-2"
              onClick={save}
              disabled={busy || !dirty || focalInvalid}
            >
              <Icon name="check" size={15} />
              {busy ? "Saving…" : "Save optics"}
            </button>
            {dirty && !busy && !focalInvalid && (
              <span className="text-[11px] text-warn">Unsaved changes</span>
            )}
            {savedAt && !dirty && !busy && !err && (
              <span className="text-[11px] text-good inline-flex items-center gap-1.5">
                <Icon name="check" size={13} /> Saved
              </span>
            )}
          </div>
          {overridden && (
            // Said HERE, on the button, because this is the moment the false
            // belief gets formed: a green "Saved" beside numbers the rig will
            // never read is exactly the confident-and-wrong signal that let a
            // simulated aligner run for twelve days. The values still persist —
            // they take effect the moment the profile stops overriding them.
            <p className="text-[11px] text-warn leading-snug max-w-xl">
              Saving stores these values globally, and profile “
              {overrideProfile ?? "(unnamed)"}” will keep overriding them — the
              rig will go on using the profile's optics until you drop them
              above.
            </p>
          )}
        </div>
      )}

      {!canEdit && (
        <div className="flex items-center gap-3 border border-line2 bg-raise/40 px-3 py-2 text-xs mt-4">
          <Icon name="lock" size={14} className="text-dim shrink-0" />
          <span className="text-dim">
            Changing optics needs {accessPhrase("config.site_optics")}. The
            current values are shown for reference.
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
