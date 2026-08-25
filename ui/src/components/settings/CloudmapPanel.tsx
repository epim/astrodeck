// CloudmapPanel.tsx — Settings > Connect: the switch for the GOES cloud model.
//
// WHY THIS FILE EXISTS. The model shipped complete in stage 6a -- 4,357 lines,
// three routes, seven test files -- and the viewer shipped in 6b, and between
// them there was no way for any operator to turn it on. `enabled` defaults to
// false, and the only writer was the generic config POST. The dome panel on
// Monitor even told people to come to "Settings > enable the GOES cloud
// model", which was a real screen with no such control on it: a promise
// nothing kept. This rig only has the model running because somebody set the
// block through the API by hand.
//
// WeatherPanel idiom throughout: drafts seeded from a signature of exactly the
// fields this form edits (never config.version), bounded inputs validated
// client-side so a typo is a sentence rather than a 422, wholesale-replace
// save, 409 reload-and-retoast.
import { useEffect, useState, type JSX } from "react";

import { ApiError } from "../../api";
import { setCloudmapConfig } from "../../api/cloudmap";
import { useCan } from "../../lib/caps";
import { useConfig, useStore } from "../../store";
import { Field, Panel, Toggle } from "../ui";
import { Icon } from "../icons";

const toNum = (raw: string): number => (raw.trim() === "" ? NaN : Number(raw));

/** Server bounds, restated so the operator gets a sentence instead of a 422.
 *  These MUST match config.py CloudmapConfig -- poll_minutes ge=5 le=60,
 *  half_px ge=16 le=400. Below five cannot produce fresher data than the
 *  satellite publishes, which is why the server refuses rather than clamps. */
const POLL_MIN = 5, POLL_MAX = 60;
const HALF_MIN = 16, HALF_MAX = 400;

/** Roughly what one poll costs: two ABI products, a 201-cell window each.
 *  Scales with the square of the window, so the estimate moves when half_px
 *  does -- the default 100 is the 4.4 MB the docs quote. */
function megabytesPerHour(halfPx: number, pollMinutes: number): number {
  const cells = (2 * halfPx + 1) ** 2;
  const perCycleMb = 4.4 * (cells / (2 * 100 + 1) ** 2);
  return (perCycleMb * 60) / pollMinutes;
}

const PLATFORM_NOTE: Record<string, string> = {
  auto: "picks the nearer satellite from the site longitude",
  G18: "GOES-West — pinned. Correct west of about 106 W",
  G19: "GOES-East — pinned. Correct east of about 106 W",
};

export default function CloudmapPanel(): JSX.Element {
  const config = useConfig();
  const showToast = useStore((s) => s.showToast);
  const loadConfig = useStore((s) => s.loadConfig);
  const canEdit = useCan("config.site_optics");

  const [enabled, setEnabled] = useState(false);
  const [platform, setPlatform] = useState<"auto" | "G18" | "G19">("auto");
  const [poll, setPoll] = useState("10");
  const [halfPx, setHalfPx] = useState("100");
  const [busy, setBusy] = useState(false);

  const c = config?.cloudmap;
  const sig = c ? JSON.stringify([c.enabled, c.platform, c.poll_minutes, c.half_px]) : null;
  useEffect(() => {
    if (!c) return;
    setEnabled(!!c.enabled);
    setPlatform(c.platform ?? "auto");
    setPoll(String(c.poll_minutes));
    setHalfPx(String(c.half_px));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sig]);

  // Numeric fields compare as PARSED values, not strings: "010" against a
  // stored 10 is not an edit (SitePanel/WeatherPanel precedent).
  const dirty = !!c && (
    enabled !== !!c.enabled ||
    platform !== (c.platform ?? "auto") ||
    toNum(poll) !== c.poll_minutes ||
    toNum(halfPx) !== c.half_px);

  const validate = (): string | null => {
    const p = toNum(poll);
    if (!Number.isFinite(p) || p < POLL_MIN || p > POLL_MAX) {
      return `Poll interval must be ${POLL_MIN}-${POLL_MAX} minutes — the satellite publishes every 5`;
    }
    const h = toNum(halfPx);
    if (!Number.isFinite(h) || h < HALF_MIN || h > HALF_MAX) {
      return `Window half-width must be ${HALF_MIN}-${HALF_MAX} cells`;
    }
    return null;
  };

  const save = async () => {
    if (busy) return;
    const err = validate();
    if (err) { showToast("error", err); return; }
    setBusy(true);
    try {
      await setCloudmapConfig({
        enabled,
        platform,
        poll_minutes: Math.round(toNum(poll)),
        half_px: Math.round(toNum(halfPx)),
      });
      await loadConfig();
      showToast("success", enabled ? "Cloud model on" : "Cloud model off");
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        await loadConfig();
        showToast("error", "Config changed elsewhere — reloaded, re-apply your edit");
      } else if (e instanceof ApiError && e.status === 403) {
        showToast("error", "config.site_optics required to change the cloud model");
      } else {
        showToast("error", e instanceof Error ? e.message : "Could not save cloud model settings");
      }
    } finally {
      setBusy(false);
    }
  };

  const mbh = megabytesPerHour(toNum(halfPx) || 100, toNum(poll) || 10);

  return (
    <Panel title="Cloud model">
      <div className="flex flex-col gap-3">
        <p className="text-[12px] text-dim">
          Where in the sky the cloud is, from the NOAA GOES cloud mask and
          cloud-top height. Drawn as the sky dome on Monitor. It is advisory
          only — nothing in the sequencer, the safety gate or auto-resume reads
          it, so switching it on cannot stop a run.
        </p>

        <label className="flex items-center justify-between gap-2 text-sm">
          <span className="label">Cloud model enabled</span>
          <Toggle checked={enabled} disabled={!canEdit || busy} onChange={setEnabled}
                  label="Cloud model enabled" />
        </label>

        {enabled && config?.site?.is_default && (
          <p className="text-[12px] text-warn inline-flex items-start gap-1.5">
            <Icon name="alert" size={14} className="shrink-0 mt-0.5" />
            <span>
              Needs a real observing site. Every ray is traced from it, so with
              the default site the dome has nothing to show.
            </span>
          </p>
        )}

        <Field label="Satellite">
          <select
            className="field"
            value={platform}
            disabled={!canEdit || busy}
            aria-label="GOES satellite"
            onChange={(e) => setPlatform(e.target.value as "auto" | "G18" | "G19")}
          >
            <option value="auto">Automatic</option>
            <option value="G18">GOES-West (G18)</option>
            <option value="G19">GOES-East (G19)</option>
          </select>
        </Field>
        <p className="text-[11px] text-dim">
          {PLATFORM_NOTE[platform]}. Both carry a CONUS sector only, so a site
          outside North America gets no reading from either — the dome says so
          rather than drawing clear sky.
        </p>

        <div className="grid grid-cols-2 gap-3">
          <Field label="Poll every (min)">
            <input
              className="field"
              inputMode="numeric"
              value={poll}
              disabled={!canEdit}
              onChange={(e) => setPoll(e.target.value)}
              placeholder="10"
              aria-label={`Poll interval in minutes (${POLL_MIN}-${POLL_MAX})`}
            />
          </Field>
          <Field label="Window half-width (cells)">
            <input
              className="field"
              inputMode="numeric"
              value={halfPx}
              disabled={!canEdit}
              onChange={(e) => setHalfPx(e.target.value)}
              placeholder="100"
              aria-label={`Window half-width in grid cells (${HALF_MIN}-${HALF_MAX})`}
            />
          </Field>
        </div>
        <p className="text-[11px] text-dim tabular-nums">
          About {mbh.toFixed(1)} MB an hour on this setting. The window is the
          box of satellite cells fetched around the site; 100 reaches 200 km
          either way, past the 151 km a 5-degree ray crosses.
        </p>

        {!canEdit && (
          <span className="text-[11px] text-dim inline-flex items-center gap-1">
            <Icon name="lock" size={11} />
            config.site_optics needed to change the cloud model
          </span>
        )}

        <div className="flex items-center justify-between gap-2">
          {dirty
            ? <p className="text-[11px] text-dim">Unsaved changes — Save to apply</p>
            : <span />}
          <button
            type="button"
            className="btn btn-accent"
            disabled={!canEdit || busy}
            onClick={() => void save()}
          >
            Save cloud model settings
          </button>
        </div>
      </div>
    </Panel>
  );
}
