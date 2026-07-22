// SitePanel.tsx — Settings → "Observing Site" (site-location-privacy spec §5).
// The ONE place the observing site is edited: name + latitude (magnitude 0-90 +
// N/S) + longitude (magnitude 0-180 + E/W) + elevation, converted to the signed
// storage convention at the boundary via lib/site.ts. Reads config.site
// (useConfig); coordinate fields show "Hidden" for principals lacking
// view.site_precise. Writes are config.site_optics-gated (read-only otherwise,
// same Gated idiom as DriversPanel). horizon_min_deg is NEVER sent from a manual
// edit — the Safety panel owns it, and omission preserves the stored value. A
// saved location may CARRY a horizon_min_deg, applied through PUT /api/site
// only when the principal holds config.safety (§4). The saved-locations row
// renders only for config.site_optics holders (the routes 403 otherwise).
import { useEffect, useState, type JSX } from "react";
import type { SavedLocation, Site } from "../../types";
import {
  deleteLocation,
  getMountGps,
  listLocations,
  saveLocation,
  saveSite,
  updateLocation,
} from "../../api/site";
import { ApiError } from "../../api";
import { useConfig, useStore } from "../../store";
import { useCan, useCanViewSitePrecise } from "../../lib/caps";
import {
  activeSiteName,
  activeSiteSource,
  formatCoord,
  fromSigned,
  locationEquals,
  toSigned,
  validateElevation,
  validateLat,
  validateLon,
  type SiteDraft,
} from "../../lib/site";
import { confirmDialog } from "../ConfirmDialog";
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
  const canSafety = useCan("config.safety");
  const canSeePrecise = useCanViewSitePrecise();

  // Draft fields (strings for text inputs; hemispheres as selects).
  const [name, setName] = useState("");
  const [latMag, setLatMag] = useState("");
  const [latHemi, setLatHemi] = useState<"N" | "S">("N");
  const [lonMag, setLonMag] = useState("");
  const [lonHemi, setLonHemi] = useState<"E" | "W">("E");
  const [elev, setElev] = useState("");
  const [busy, setBusy] = useState(false);

  // Saved-locations row state.
  const [locations, setLocations] = useState<SavedLocation[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [baseline, setBaseline] = useState<SavedLocation | null>(null);
  // horizon carried by the applied location, sent on the next save when the
  // principal holds config.safety (§4/§5). Null = nothing to carry.
  const [appliedHorizon, setAppliedHorizon] = useState<number | null>(null);
  // R3-SITE-02: "Load selected preset" fills the form but does NOT activate it
  // (§ two-step flow, deliberate — see applyLocation). That's easy to miss, so
  // this flags "just loaded, not yet Set" for the transient banner + promoted
  // Set-site button below. It rides the EXISTING dirty-state baseline rather
  // than duplicating equality logic: the banner's actual visibility condition
  // is `justLoaded && !dirty` (declared near `dirty`, below) — editing the form
  // away from the loaded values makes `dirty` true and hides the banner without
  // any extra bookkeeping here.
  const [justLoaded, setJustLoaded] = useState(false);
  // inline "Save current…" name prompt (ConfirmDialog-pattern, but a text field
  // — the modal has no text input).
  const [savingName, setSavingName] = useState<string | null>(null);

  // Seed drafts from the PERSISTED site fields only (SafetyPanel idiom):
  // config.version is a global counter bumped by EVERY config mutation
  // (drivers, optics, safety, ...), so keying the reseed on it would reset
  // in-flight edits whenever anything unrelated changed. Instead the effect
  // keys on a signature of exactly the fields this form seeds from — it
  // reseeds when the SITE actually changed (initial load + after our own
  // successful save) and leaves drafts alone on unrelated config bumps.
  // Stripped fields (no view.site_precise) serialize as null, so a cap change
  // that adds/removes them also reseeds correctly.
  const site = config?.site;
  const siteSig = site
    ? JSON.stringify([
        site.name ?? null,
        site.latitude ?? null,
        site.longitude ?? null,
        site.elevation_m ?? null,
        site.is_default,
      ])
    : null;
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
    // a fresh config re-seed invalidates the applied-location horizon carry —
    // the value has either just been persisted (our own save) or belongs to a
    // site we didn't apply it to (someone else's edit).
    setAppliedHorizon(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [siteSig]);

  const refreshLocations = async () => {
    setLocations(await listLocations());
  };

  // Only holders can read the library (the route 403s otherwise).
  useEffect(() => {
    if (!canEdit) return;
    void refreshLocations().catch(() => {
      /* non-fatal; the row just shows an empty list */
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [canEdit]);

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

  // The converted (signed) draft — comparison basis for locationEquals and the
  // payload basis for "Save current…".
  const draft = (): SiteDraft => ({
    name: name.trim(),
    latitude: toSigned(toNum(latMag), latHemi),
    longitude: toSigned(toNum(lonMag), lonHemi),
    elevation_m: toNum(elev),
  });

  const buildSite = (): Site => ({
    // carry is_default + horizon_min_deg through from config (server flips
    // is_default off and preserves the stored horizon when the body omits it).
    ...(config?.site ?? { is_default: true, horizon_min_deg: 15 }),
    name: name.trim() || "My Observatory",
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
    // §4/§5: include the applied location's horizon ONLY when it carries one AND
    // the principal holds config.safety; otherwise omit (server preserves stored).
    const horizon =
      canSafety && appliedHorizon !== null ? appliedHorizon : undefined;
    setBusy(true);
    try {
      await saveSite(buildSite(), config?.version ?? null, horizon);
      await loadConfig();
      setJustLoaded(false); // R3-SITE-02: Set site pressed — the loaded preset is now active
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

  // ---- saved-locations actions --------------------------------------------

  // Loads a preset's coordinates into the form. ONLY called from the explicit
  // "Load selected preset" action (R2-SIT-01: merely highlighting an <option>
  // must never mutate the form — three review rounds flagged the old
  // auto-apply-on-select behavior as concealing which action actually did
  // something).
  const applyLocation = (loc: SavedLocation) => {
    setName(loc.name);
    const la = fromSigned(loc.latitude, "lat");
    const lo = fromSigned(loc.longitude, "lon");
    setLatMag(formatCoord(la.magnitude));
    setLatHemi(la.hemisphere as "N" | "S");
    setLonMag(formatCoord(lo.magnitude));
    setLonHemi(lo.hemisphere as "E" | "W");
    setElev(String(loc.elevation_m));
    setSelectedId(loc.id);
    setBaseline(loc);
    setAppliedHorizon(loc.horizon_min_deg);
    setJustLoaded(true);
  };

  // The dropdown's onChange: tracks WHICH preset is picked, nothing more. The
  // form only changes when "Load selected preset" is pressed.
  const onPickLocation = (id: string) => {
    setSelectedId(id || null);
  };

  const submitSaveCurrent = async (locName: string) => {
    const err = validate();
    if (err) {
      showToast("error", err);
      return;
    }
    const d = draft();
    const input = {
      name: locName.trim(),
      latitude: d.latitude,
      longitude: d.longitude,
      elevation_m: d.elevation_m,
      // Save the CURRENT stored horizon (§5), not a panel-edited one.
      horizon_min_deg: config?.site?.horizon_min_deg ?? null,
    };
    try {
      const loc = await saveLocation(input);
      await refreshLocations();
      setSelectedId(loc.id);
      setBaseline(loc);
      // sync the Site-name draft to the saved location's stored name so the
      // form compares clean against the new baseline (the prompt name and the
      // Site-name field routinely differ — e.g. blank field + default name).
      setName(loc.name);
      setSavingName(null);
      showToast("success", `Saved location "${loc.name}"`);
    } catch (e) {
      if (e instanceof ApiError && e.code === "name_collision") {
        const ok = await confirmDialog({
          title: `A location named "${locName}" already exists`,
          body: "Overwrite it with the current coordinates?",
          tone: "warn",
          confirmLabel: "Overwrite",
        });
        if (ok) {
          // The 409 detail carries the existing location's id — the server's
          // authoritative overwrite target. Never re-derive it client-side by
          // name (JS toLowerCase() != the server's casefold()), and never
          // no-op silently after the user confirmed an overwrite.
          if (!e.id) {
            showToast(
              "error",
              "Couldn't identify the existing location — refresh and retry",
            );
            return;
          }
          try {
            const loc = await updateLocation(e.id, input);
            await refreshLocations();
            setSelectedId(loc.id);
            setBaseline(loc);
            // same baseline sync as the create path above.
            setName(loc.name);
            setSavingName(null);
            showToast("success", "Location updated");
          } catch (e2) {
            showToast("error", e2 instanceof Error ? e2.message : "Update failed");
          }
        }
      } else if (e instanceof ApiError && e.code === "library_full") {
        showToast("error", "Location library is full (50) — delete one first.");
      } else {
        showToast("error", e instanceof Error ? e.message : "Save failed");
      }
    }
  };

  const deleteSelected = () =>
    void (async () => {
      const loc = locations.find((l) => l.id === selectedId);
      if (!loc) return;
      const ok = await confirmDialog({
        title: `Delete saved location "${loc.name}"?`,
        tone: "danger",
        confirmLabel: "Delete",
      });
      if (!ok) return;
      try {
        await deleteLocation(loc.id);
        await refreshLocations();
        setSelectedId(null);
        setBaseline(null);
        setJustLoaded(false); // baseline just went away — nothing left to be "loaded"
        showToast("success", "Location deleted");
      } catch (e) {
        showToast("error", e instanceof Error ? e.message : "Delete failed");
      }
    })();

  // All four seeded fields (name/lat/lon/elevation) are strippable for
  // principals lacking view.site_precise — gate every placeholder, not just
  // the coordinates, so the "Hidden" affordance is consistent.
  const coordPlaceholder = canSeePrecise ? "0.000000" : "Hidden";
  const namePlaceholder = canSeePrecise ? "My Observatory" : "Hidden";
  const elevPlaceholder = canSeePrecise ? "0" : "Hidden";
  const dirty = baseline ? !locationEquals(draft(), baseline) : false;
  // R3-SITE-02: the "Loaded into form — not active yet" banner + promoted
  // Set-site button. Only while the loaded values are still exactly what's in
  // the form — editing away (dirty) or pressing Set site (justLoaded cleared)
  // both hide it, per the dirty-state baseline this panel already tracks.
  const loadedNotActive = justLoaded && !dirty;
  const sortedLocations = [...locations].sort((a, b) =>
    a.name.localeCompare(b.name, undefined, { sensitivity: "base" }),
  );

  // Persistent identifier (R2-SIT-02): the PERSISTED site, independent of
  // whatever the form below is mid-editing or a picked-but-not-loaded preset.
  const activeName = activeSiteName(config?.site);
  const activeSource = activeSiteSource(config?.site, locations);

  return (
    <Panel title="Observing Site">
      <div className="flex flex-col gap-3">
        <p className="text-[11px] text-dim border-b border-line pb-2">
          Active site: <span className="text-ink">{activeName}</span> · {activeSource}
        </p>

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
            placeholder={namePlaceholder}
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
            placeholder={elevPlaceholder}
            aria-label="Elevation in metres"
          />
        </Field>

        {canEdit && (
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              className={`btn btn-accent ${loadedNotActive ? "btn-promoted" : ""}`}
              disabled={busy}
              onClick={() => void onSave()}
            >
              Set site
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

        {/* --------------------------------------------- saved locations row */}
        {canEdit && (
          <div className="border-t border-line pt-3 flex flex-col gap-2">
            <Field label="Saved locations">
              <div className="flex flex-wrap items-center gap-2">
                <select
                  className="field !w-auto"
                  value={selectedId ?? ""}
                  disabled={busy}
                  onChange={(e) => onPickLocation(e.target.value)}
                  aria-label="Saved locations"
                >
                  <option value="">—</option>
                  {sortedLocations.map((l) => (
                    <option key={l.id} value={l.id}>
                      {l.name}
                    </option>
                  ))}
                </select>
                <button
                  type="button"
                  className="btn"
                  disabled={busy || !selectedId}
                  onClick={() => {
                    const loc = locations.find((l) => l.id === selectedId);
                    if (loc) applyLocation(loc);
                  }}
                >
                  Load selected preset
                </button>
                <button
                  type="button"
                  className="btn"
                  disabled={busy}
                  onClick={() => setSavingName(name.trim() || "New location")}
                >
                  Save as location preset…
                </button>
                <button
                  type="button"
                  className="btn btn-danger"
                  disabled={busy || !selectedId}
                  onClick={deleteSelected}
                >
                  Delete
                </button>
              </div>
            </Field>

            {loadedNotActive && baseline && (
              <p className="text-[11px] text-accent inline-flex items-center gap-1.5">
                <Icon name="info" size={11} />
                Loaded &quot;{baseline.name}&quot; into the form — not active yet. Press{" "}
                <span className="text-ink font-medium">Set site</span> above to activate it.
              </p>
            )}

            {dirty && baseline && (
              <p className="text-[11px] text-dim">
                Modified — differs from &quot;{baseline.name}&quot;
              </p>
            )}

            {savingName !== null && (
              <div className="flex flex-wrap items-center gap-2">
                <input
                  className="field !w-auto"
                  value={savingName}
                  disabled={busy}
                  autoFocus
                  onChange={(e) => setSavingName(e.target.value)}
                  aria-label="New saved-location name"
                  placeholder="Location name"
                />
                <button
                  type="button"
                  className="btn btn-accent"
                  disabled={busy || savingName.trim() === ""}
                  onClick={() => void submitSaveCurrent(savingName)}
                >
                  Save
                </button>
                <button
                  type="button"
                  className="btn"
                  disabled={busy}
                  onClick={() => setSavingName(null)}
                >
                  Cancel
                </button>
              </div>
            )}
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
