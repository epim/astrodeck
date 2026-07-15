// SitePanel.tsx — Settings → "Observing Site" (site-location-privacy spec §5).
// The ONE place the observing site is edited: name + latitude (magnitude 0-90 +
// N/S) + longitude (magnitude 0-180 + E/W) + elevation, converted to the signed
// storage convention at the boundary via lib/site.ts. Reads config.site
// (useConfig); coordinate fields show "Hidden" for principals lacking
// view.site_precise. Writes are config.site_optics-gated (read-only otherwise,
// same Gated idiom as DriversPanel). horizon_min_deg is NEVER sent from a manual
// edit — the Safety panel owns it, and omission preserves the stored value.
import { useEffect, useState, type JSX } from "react";
import type { Site } from "../../types";
import { getMountGps, saveSite } from "../../api/site";
import { ApiError } from "../../api";
import { useConfig, useStore } from "../../store";
import { useCan, useCanViewSitePrecise } from "../../lib/caps";
import {
  formatCoord,
  fromSigned,
  toSigned,
  validateElevation,
  validateLat,
  validateLon,
} from "../../lib/site";
import { Field, Panel } from "../ui";
import { Icon } from "../icons";

// Number("") is 0, which is Number.isFinite and would silently pass the lib
// validators as coordinate 0 — a blank field must never be read as "0", it
// must be read as "not entered". Route every text-field conversion through
// this instead of a bare Number(...) so blank/whitespace-only input becomes
// NaN and fails validateLat/validateLon/validateElevation's finite check.
const toNum = (raw: string): number => (raw.trim() === "" ? NaN : Number(raw));

export default function SitePanel(): JSX.Element {
  const config = useConfig();
  const showToast = useStore((s) => s.showToast);
  const loadConfig = useStore((s) => s.loadConfig);
  const canEdit = useCan("config.site_optics");
  const canSeePrecise = useCanViewSitePrecise();

  // Draft fields (strings for text inputs; hemispheres as selects).
  const [name, setName] = useState("");
  const [latMag, setLatMag] = useState("");
  const [latHemi, setLatHemi] = useState<"N" | "S">("N");
  const [lonMag, setLonMag] = useState("");
  const [lonHemi, setLonHemi] = useState<"E" | "W">("E");
  const [elev, setElev] = useState("");
  const [busy, setBusy] = useState(false);

  // Seed drafts from config.site whenever the version changes (initial load +
  // after a save that bumps version) — not on every render, so in-flight edits
  // survive an unrelated config event.
  useEffect(() => {
    const s = config?.site;
    if (!s) return;
    setName(s.name ?? "");
    if (typeof s.latitude === "number") {
      const { magnitude, hemisphere } = fromSigned(s.latitude, "lat");
      setLatMag(formatCoord(magnitude));
      setLatHemi(hemisphere as "N" | "S");
    }
    if (typeof s.longitude === "number") {
      const { magnitude, hemisphere } = fromSigned(s.longitude, "lon");
      setLonMag(formatCoord(magnitude));
      setLonHemi(hemisphere as "E" | "W");
    }
    if (typeof s.elevation_m === "number") setElev(String(s.elevation_m));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [config?.version]);

  // A generic action runner (DriversPanel idiom): busy + optional success toast,
  // 403 -> capability message. Save has its own handler (409 is special).
  const run = async (fn: () => Promise<unknown>, okMsg?: string) => {
    if (busy) return;
    setBusy(true);
    try {
      await fn();
      if (okMsg) showToast("success", okMsg);
    } catch (e) {
      const msg =
        e instanceof ApiError
          ? e.status === 403
            ? "config.site_optics required to change the site"
            : e.message
          : e instanceof Error
            ? e.message
            : "operation failed";
      showToast("error", msg);
    } finally {
      setBusy(false);
    }
  };

  const validate = (): string | null =>
    validateLat(toNum(latMag)) ||
    validateLon(toNum(lonMag)) ||
    validateElevation(toNum(elev));

  const buildSite = (): Site => ({
    // carry is_default + horizon_min_deg through from config (server flips
    // is_default off and preserves the stored horizon when the body omits it).
    ...(config?.site ?? { is_default: true, horizon_min_deg: 15 }),
    name: name.trim() || "My Backyard",
    latitude: toSigned(toNum(latMag), latHemi),
    longitude: toSigned(toNum(lonMag), lonHemi),
    elevation_m: toNum(elev),
  });

  const onSave = async () => {
    if (busy) return;
    const err = validate();
    if (err) {
      showToast("error", err);
      return;
    }
    setBusy(true);
    try {
      await saveSite(buildSite(), config?.version ?? null);
      await loadConfig();
      showToast("success", "Site saved");
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        await loadConfig();
        showToast(
          "error",
          "Config changed elsewhere — reloaded, re-apply your edit",
        );
      } else if (e instanceof ApiError && e.status === 403) {
        showToast("error", "config.site_optics required to save the site");
      } else {
        showToast("error", e instanceof Error ? e.message : "Could not save site");
      }
    } finally {
      setBusy(false);
    }
  };

  // Browser geolocation is only reachable over HTTPS or localhost; the LAN UI is
  // plain http, so gate the button on a secure context (spec §5).
  const geoAvailable =
    typeof window !== "undefined" &&
    window.isSecureContext &&
    "geolocation" in navigator;

  const useMyLocation = () => {
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        const la = fromSigned(pos.coords.latitude, "lat");
        const lo = fromSigned(pos.coords.longitude, "lon");
        setLatMag(formatCoord(la.magnitude));
        setLatHemi(la.hemisphere as "N" | "S");
        setLonMag(formatCoord(lo.magnitude));
        setLonHemi(lo.hemisphere as "E" | "W");
        if (typeof pos.coords.altitude === "number")
          setElev(String(Math.round(pos.coords.altitude)));
        showToast("success", "Filled from browser location — review and save");
      },
      (e) => showToast("error", e.message || "Couldn't get browser location"),
      { enableHighAccuracy: true, timeout: 10000 },
    );
  };

  const useMountGps = () =>
    run(async () => {
      const g = await getMountGps();
      if (!g.available) {
        showToast("info", g.detail ?? "Mount GPS unavailable");
        return;
      }
      const la = fromSigned(g.latitude as number, "lat");
      const lo = fromSigned(g.longitude as number, "lon");
      setLatMag(formatCoord(la.magnitude));
      setLatHemi(la.hemisphere as "N" | "S");
      setLonMag(formatCoord(lo.magnitude));
      setLonHemi(lo.hemisphere as "E" | "W");
      if (typeof g.elevation_m === "number")
        setElev(String(Math.round(g.elevation_m)));
      showToast("success", "Filled from mount GPS — review and save");
    });

  const coordPlaceholder = canSeePrecise ? "0.000000" : "Hidden";

  return (
    <Panel title="Observing Site">
      <div className="flex flex-col gap-3">
        {config?.site?.is_default && (
          <p className="text-[12px] text-warn inline-flex items-start gap-1.5">
            <Icon name="alert" size={14} className="shrink-0 mt-0.5" />
            <span>
              Using default location (0, 0) — sequencing windows and Atlas
              visibility are wrong until set.
            </span>
          </p>
        )}

        <Field label="Site name">
          <input
            className="field"
            value={name}
            disabled={!canEdit}
            onChange={(e) => setName(e.target.value)}
            placeholder="My Backyard"
          />
        </Field>

        <Field label="Latitude">
          <div className="flex items-center gap-2">
            <input
              className="field"
              inputMode="decimal"
              value={latMag}
              disabled={!canEdit}
              onChange={(e) => setLatMag(e.target.value)}
              placeholder={coordPlaceholder}
              aria-label="Latitude magnitude (0–90)"
            />
            <select
              className="field !w-auto"
              value={latHemi}
              disabled={!canEdit}
              onChange={(e) => setLatHemi(e.target.value as "N" | "S")}
              aria-label="Latitude hemisphere"
            >
              <option value="N">N</option>
              <option value="S">S</option>
            </select>
          </div>
        </Field>

        <Field label="Longitude">
          <div className="flex items-center gap-2">
            <input
              className="field"
              inputMode="decimal"
              value={lonMag}
              disabled={!canEdit}
              onChange={(e) => setLonMag(e.target.value)}
              placeholder={coordPlaceholder}
              aria-label="Longitude magnitude (0–180)"
            />
            <select
              className="field !w-auto"
              value={lonHemi}
              disabled={!canEdit}
              onChange={(e) => setLonHemi(e.target.value as "E" | "W")}
              aria-label="Longitude hemisphere"
            >
              <option value="E">E</option>
              <option value="W">W</option>
            </select>
          </div>
        </Field>

        <Field label="Elevation (m)">
          <input
            className="field"
            inputMode="numeric"
            value={elev}
            disabled={!canEdit}
            onChange={(e) => setElev(e.target.value)}
            placeholder="0"
            aria-label="Elevation in metres"
          />
        </Field>

        {canEdit && (
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              className="btn btn-accent"
              disabled={busy}
              onClick={() => void onSave()}
            >
              Save site
            </button>
            {geoAvailable ? (
              <button
                type="button"
                className="btn"
                disabled={busy}
                onClick={useMyLocation}
              >
                Use my location
              </button>
            ) : (
              <p className="text-[11px] text-dim self-center">
                Browser location needs HTTPS or localhost — enter manually or use
                mount GPS.
              </p>
            )}
            <button
              type="button"
              className="btn"
              disabled={busy}
              onClick={() => void useMountGps()}
            >
              Use mount GPS
            </button>
          </div>
        )}

        {!canEdit && (
          <p className="text-[11px] text-dim inline-flex items-center gap-1.5">
            <Icon name="lock" size={11} />
            Read-only — changing the site needs config.site_optics access.
          </p>
        )}
      </div>
    </Panel>
  );
}
