// WeatherPanel.tsx — Settings → Connect weather config (weather spec §2/§10).
// SitePanel idiom: drafts seeded from a signature of exactly the fields this
// form edits (never config.version); bounded inputs validated client-side
// (422-safe); the Astrospheric key is WRITE-ONLY — the masked config only ever
// carries astrospheric_configured, never the value. Save = POST
// /api/config/weather with the 409 reload-and-retoast idiom.
import { useEffect, useState, type JSX } from "react";
import { ApiError } from "../../api";
import { useConfig, useStore } from "../../store";
import { useCan } from "../../lib/caps";
import { saveWeatherConfig } from "../../api/weather";
import { Field, Panel, Toggle } from "../ui";
import { Icon } from "../icons";

// Blank input must read as "not entered", never as 0 (SitePanel toNum rule).
const toNum = (raw: string): number => (raw.trim() === "" ? NaN : Number(raw));

export default function WeatherPanel(): JSX.Element {
  const config = useConfig();
  const showToast = useStore((s) => s.showToast);
  const loadConfig = useStore((s) => s.loadConfig);
  const canEdit = useCan("config.site_optics");

  const [enabled, setEnabled] = useState(false);
  const [threshold, setThreshold] = useState("50");
  const [sustain, setSustain] = useState("30");
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);

  const w = config?.weather;
  const keyConfigured = !!w?.astrospheric_configured;
  const sig = w
    ? JSON.stringify([
        w.enabled,
        w.cloud_threshold_pct,
        w.sustain_minutes,
        w.astrospheric_configured ?? false,
      ])
    : null;
  useEffect(() => {
    if (!w) return;
    setEnabled(!!w.enabled);
    setThreshold(String(w.cloud_threshold_pct));
    setSustain(String(w.sustain_minutes));
    setKey(""); // write-only: never seeded from the masked config
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sig]);

  const validate = (): string | null => {
    const t = toNum(threshold);
    if (!Number.isFinite(t) || t < 0 || t > 100) return "Cloud threshold must be 0-100 %";
    const s = toNum(sustain);
    if (!Number.isFinite(s) || s < 15 || s > 240) return "Sustain must be 15-240 minutes";
    return null;
  };

  const save = async (clearKey: boolean) => {
    if (busy) return;
    const err = validate();
    if (err) {
      showToast("error", err);
      return;
    }
    setBusy(true);
    try {
      await saveWeatherConfig(
        {
          enabled,
          cloud_threshold_pct: Math.round(toNum(threshold)),
          sustain_minutes: Math.round(toNum(sustain)),
          astrospheric_api_key: key.trim() === "" ? null : key.trim(),
        },
        config?.version ?? null,
        clearKey,
      );
      await loadConfig();
      showToast("success", clearKey ? "Astrospheric key cleared" : "Weather settings saved");
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        await loadConfig();
        showToast("error", "Config changed elsewhere — reloaded, re-apply your edit");
      } else if (e instanceof ApiError && e.status === 403) {
        showToast("error", "config.site_optics required to change weather settings");
      } else {
        showToast("error", e instanceof Error ? e.message : "Could not save weather settings");
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <Panel title="Weather">
      <div className="flex flex-col gap-3">
        <p className="text-[12px] text-dim">
          Cloud forecast, radar and the high-cloud night warning. One shared
          threshold drives both the warning and the auto-resume hold. Enabling
          this makes the home server fetch forecasts for the configured site.
        </p>

        <label className="flex items-center justify-between gap-2 text-sm">
          <span className="text-dim">weather enabled</span>
          <Toggle checked={enabled} disabled={!canEdit || busy} onChange={setEnabled} />
        </label>

        <div className="grid grid-cols-2 gap-3">
          <Field label="Cloud threshold (%)">
            <input
              className="field"
              inputMode="numeric"
              value={threshold}
              disabled={!canEdit}
              onChange={(e) => setThreshold(e.target.value)}
              placeholder="50"
              aria-label="Cloud threshold percent (0-100)"
            />
          </Field>
          <Field label="Sustained for (min)">
            <input
              className="field"
              inputMode="numeric"
              value={sustain}
              disabled={!canEdit}
              onChange={(e) => setSustain(e.target.value)}
              placeholder="30"
              aria-label="Sustain minutes (15-240)"
            />
          </Field>
        </div>

        <Field label={`Astrospheric API key — ${keyConfigured ? "set" : "not set"}`}>
          <div className="flex items-center gap-2">
            <input
              className="field"
              type="password"
              value={key}
              disabled={!canEdit}
              onChange={(e) => setKey(e.target.value)}
              placeholder={keyConfigured ? "(unchanged)" : "optional — Pro membership"}
              aria-label="Astrospheric API key (write-only)"
            />
            {keyConfigured && (
              <button
                type="button"
                className="btn !py-1 text-xs"
                disabled={!canEdit || busy}
                onClick={() => void save(true)}
              >
                Clear key
              </button>
            )}
          </div>
        </Field>
        <p className="text-[11px] text-dim">
          Optional, North-America-only seeing/transparency. Polled every 6 h
          (5 credits per call on a 100/day Pro budget) — never faster.
        </p>

        {!canEdit && (
          <span className="text-[11px] text-dim inline-flex items-center gap-1">
            <Icon name="lock" size={11} />
            config.site_optics needed to change weather settings
          </span>
        )}

        <div className="flex justify-end">
          <button
            type="button"
            className="btn btn-accent"
            disabled={!canEdit || busy}
            onClick={() => void save(false)}
          >
            Save weather settings
          </button>
        </div>
      </div>
    </Panel>
  );
}
