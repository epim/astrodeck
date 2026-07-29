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

import { useEffect, useState, type JSX } from "react";
import type { Optics } from "../../types";
import { api, ApiError } from "../../api";
import { useConfig, useStore } from "../../store";
import { accessPhrase, useCan } from "../../lib/caps";
import { Panel, Field, Toggle } from "../ui";
import { Icon } from "../icons";

export default function OpticsPanel(): JSX.Element {
  const config = useConfig();
  const optics = config?.optics;
  const computed = config?.optics_computed;
  const canEdit = useCan("config.site_optics");

  const [draft, setDraft] = useState<Optics | null>(null);
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
        computed?.image_scale_arcsec_px ? (
          <span className="mono text-[11px] text-dim">
            {computed.image_scale_arcsec_px.toFixed(2)}″/px
          </span>
        ) : undefined
      }
    >
      <p className="text-[11px] text-dim leading-relaxed max-w-xl mb-1">
        Focal length sets the image scale every plate solve, framing overlay and
        guiding readout depends on. Get it wrong and solves fail with no obvious
        reason.
      </p>

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
        </Field>
      </div>

      {canEdit && (
        <div className="flex items-center gap-3 pt-4 border-t border-line mt-4">
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
