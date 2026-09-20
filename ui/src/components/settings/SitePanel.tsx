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
import { useEffect, useRef, useState, type JSX } from "react";
import type { SavedLocation, Site } from "../../types";
import {
  deleteLocation,
  getMountGps,
  listLocations,
  saveLocation,
  saveSite,
  updateLocation,
} from "../../api/site";
import { api, ApiError } from "../../api";
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
import { Segmented } from "../Segmented";
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
  // which action owns `busy` right now ("geo", "gps", "preset", "delete"), so
  // the button that was pressed is the one that says it is working.
  const [busyWhat, setBusyWhat] = useState<string | null>(null);

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
  //
  // `what` names the action that is in flight so the button that started it can
  // say so. One shared `busy` flag dims every button in the panel, which reads
  // as "the panel is thinking" rather than "your press landed" — and browser
  // geolocation can take the full 10 s timeout before anything at all happens.
  const run = async (fn: () => Promise<unknown>, okMsg?: string, what?: string) => {
    if (busy) return;
    setBusy(true);
    setBusyWhat(what ?? null);
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
      setBusyWhat(null);
    }
  };

  const validate = (): string | null =>
    validateLat(toNum(latMag)) ||
    validateLon(toNum(lonMag)) ||
    validateElevation(toNum(elev));

  // --------------------------------------------- "…which is where?" read-back
  // UX review #12 (the S4 pattern: the system knows and shows something else).
  // GET /api/site/sky already computes the ONE string that catches a flipped
  // sign in half a second — place_hint, e.g. "N hemisphere · W longitude ·
  // ~N. America" — and nothing rendered it. This reads it for the values
  // CURRENTLY IN THE FORM, not for the saved site: a US longitude typed as
  // 110.3 with the hemisphere left at its E default reads "~Asia" while the
  // user is still looking at the field, instead of silently reaching SITELONG /
  // OBJCTALT / AIRMASS in every delivered FITS. sun_alt_deg comes free in the
  // same response and is the second, independent check (the Sun lands on the
  // wrong continent when the sign is wrong).
  const latSigned = toSigned(toNum(latMag), latHemi);
  const lonSigned = toSigned(toNum(lonMag), lonHemi);
  const coordsEntered =
    validateLat(toNum(latMag)) === null && validateLon(toNum(lonMag)) === null;
  // The answer is STAMPED WITH THE COORDINATES IT DESCRIBES. Without that, the
  // 350ms debounce plus the round trip left the previous point's sentence on
  // screen — so correcting a hemisphere and pressing Set site immediately read
  // back the hemisphere you had just corrected away from, and the confirmation
  // toast named it too. A hint whose key doesn't match the fields is not a hint
  // about these fields; it is the pending state.
  const coordKey = `${latSigned},${lonSigned}`;
  const [hintAt, setHintAt] = useState<{
    key: string; place: string | null; sunAlt: number | null;
  } | null>(null);
  const fresh = hintAt && hintAt.key === coordKey ? hintAt : null;
  const hint = fresh?.place ?? null;
  const hintSunAlt = fresh?.sunAlt ?? null;
  useEffect(() => {
    if (!canSeePrecise || !coordsEntered) {
      setHintAt(null);
      return;
    }
    let dead = false;
    // debounced: this would otherwise fire once per keystroke while typing a
    // coordinate. 350ms still lands well inside "half a second".
    const timer = setTimeout(() => {
      api
        .get<{ place_hint?: string; sun_alt_deg?: number }>(
          `/api/site/sky?lat=${latSigned}&lon=${lonSigned}`,
        )
        .then((s) => {
          if (dead) return;
          setHintAt({
            key: `${latSigned},${lonSigned}`,
            place: s.place_hint ?? null,
            sunAlt: typeof s.sun_alt_deg === "number" ? s.sun_alt_deg : null,
          });
        })
        .catch(() => {
          if (dead) return;
          setHintAt(null);
        });
    }, 350);
    return () => {
      dead = true;
      clearTimeout(timer);
    };
  }, [latSigned, lonSigned, coordsEntered, canSeePrecise]);

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
    // Describe WHAT WAS SENT, not what the read-back happened to be showing.
    // `hint` is the answer for one particular pair of coordinates; pressing Set
    // site inside the 350ms debounce meant the toast confirmed the point you
    // had just corrected away from — the one message whose whole job is to make
    // a flipped sign catchable. The hemispheres are known here without asking
    // anyone, so fall back to them when the server's fuller sentence (which
    // also names the region) isn't in hand for these exact coordinates.
    const body = buildSite();
    const where =
      hintAt && hintAt.key === `${body.latitude},${body.longitude}` && hintAt.place
        ? hintAt.place
        : `${latHemi} hemisphere · ${lonHemi} longitude`;
    setBusy(true);
    try {
      await saveSite(body, config?.version ?? null, horizon);
      // The ONE post-save refresh — and it must stay this call, not a local
      // setState: loadConfig() refreshes BOTH the persisted `config` this panel
      // seeds from AND the `site` slice every other consumer reads (the
      // first-run wizard's location step, PreflightStrip, Atlas, Monitor,
      // Sequence, the Tonight altitude limit). Until it did, a real save left
      // all of them on the pre-save site until a rig connected.
      await loadConfig();
      setJustLoaded(false); // R3-SITE-02: Set site pressed — the loaded preset is now active
      // #12: never a bare cheerful "Site saved" — say WHERE it saved to, so a
      // wrong hemisphere is still catchable one second after the press.
      showToast("success", `Site saved — ${where}`);
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

  // What the form holds RIGHT NOW, readable from inside a callback that was
  // created up to ten seconds ago (a state variable captured at request time
  // would be the stale value we are trying not to trust).
  const formRef = useRef("");
  const formSig = `${latMag}|${latHemi}|${lonMag}|${lonHemi}|${elev}`;
  useEffect(() => { formRef.current = formSig; }, [formSig]);

  // A GPS fix can take the full 10s timeout. Two things were wrong with firing
  // it bare: the button looked untouched the whole time (so it invited a second
  // press), and a fix that arrived late overwrote whatever had been typed or
  // loaded from a preset in the meantime — silently, and with no way to tell
  // afterwards which set of coordinates you were looking at.
  const useMyLocation = () =>
    run(
      () =>
        new Promise<void>((resolve, reject) => {
          const askedAt = formRef.current;
          navigator.geolocation.getCurrentPosition(
            (pos) => {
              if (formRef.current !== askedAt) {
                showToast(
                  "info",
                  "Browser location arrived after you changed the coordinates — kept yours",
                );
                resolve();
                return;
              }
              const la = fromSigned(pos.coords.latitude, "lat");
              const lo = fromSigned(pos.coords.longitude, "lon");
              setLatMag(formatCoord(la.magnitude));
              setLatHemi(la.hemisphere as "N" | "S");
              setLonMag(formatCoord(lo.magnitude));
              setLonHemi(lo.hemisphere as "E" | "W");
              if (typeof pos.coords.altitude === "number")
                setElev(String(Math.round(pos.coords.altitude)));
              showToast("success", "Filled from browser location — review and save");
              resolve();
            },
            (e) => reject(new Error(e.message || "Couldn't get browser location")),
            { enableHighAccuracy: true, timeout: 10000 },
          );
        }),
      undefined,
      "geo",
    );

  const useMountGps = () =>
    run(async () => {
      const askedAt = formRef.current;
      const g = await getMountGps();
      if (!g.available) {
        showToast("info", g.detail ?? "Mount GPS unavailable");
        return;
      }
      // same late-answer guard as the browser fix above.
      if (formRef.current !== askedAt) {
        showToast("info", "Mount GPS arrived after you changed the coordinates — kept yours");
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
    }, undefined, "gps");

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

  const saveCurrentLocation = async (locName: string) => {
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

  // Both saved-location writes go through `run()` — their buttons already read
  // `busy`, but nothing ever set it, so the guard was decorative: a double-tap
  // on Save minted the preset twice (or accused you of duplicating the one your
  // own first tap had just made), and on Delete the second request 404'd as
  // "Delete failed" for a preset that had in fact been deleted. The confirm
  // dialogs run INSIDE `run` too, so the second tap can't queue a second one.
  const submitSaveCurrent = (locName: string) =>
    run(() => saveCurrentLocation(locName), undefined, "preset");

  const deleteSelected = () =>
    void run(async () => {
      const loc = locations.find((l) => l.id === selectedId);
      if (!loc) return;
      const ok = await confirmDialog({
        title: `Delete saved location "${loc.name}"?`,
        tone: "danger",
        confirmLabel: "Delete",
      });
      if (!ok) return;
      // named only now: "Deleting…" while the confirm dialog is still open
      // would be a button describing something the user has not agreed to yet.
      setBusyWhat("delete");
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
    });

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

        {canEdit && (
          <section aria-label="Fill observing location" className="rounded-xl border border-line2 bg-raise px-4 py-3 flex flex-col gap-3">
            <div>
              <h3 className="text-sm font-medium text-ink">Are you beside the telescope?</h3>
              <p className="text-xs text-dim mt-1 leading-relaxed">
                Choose Use my location to fill the coordinates from this device.
                If the telescope is somewhere else, use its mount GPS or a saved location.
                Check the fields below, then choose Set site.
              </p>
            </div>
            <div className="flex flex-wrap gap-2">
              {geoAvailable ? (
                <button
                  type="button"
                  className="btn btn-accent min-h-11"
                  disabled={busy}
                  aria-busy={busyWhat === "geo" || undefined}
                  onClick={() => void useMyLocation()}
                >
                  {busyWhat === "geo" ? "Locating…" : "Use my location"}
                </button>
              ) : (
                <p className="text-xs text-dim self-center">
                  This browser can't provide a location on this connection.
                  Enter the coordinates below or use mount GPS. Browser location
                  needs HTTPS or localhost and a browser that supports it.
                </p>
              )}
              <button
                type="button"
                className="btn min-h-11"
                disabled={busy}
                aria-busy={busyWhat === "gps" || undefined}
                onClick={() => void useMountGps()}
              >
                {busyWhat === "gps" ? "Asking the mount…" : "Use mount GPS"}
              </button>
            </div>
            {geoAvailable && <p className="text-xs text-dim">Your browser may ask for location permission. You can also enter the coordinates by hand.</p>}
          </section>
        )}

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

        {/* #12: the hemisphere was a chevron-less 40×40 <select> defaulting to
            N/E — on a tablet in the dark it read as decoration, and a US
            longitude typed as 110.3 saved as +110.3 EAST. A Segmented shows
            BOTH options and which one is armed, at the 44px touch minimum,
            with selection encoded by fill + weight (never hue alone). */}
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
            {/* Segmented collapses to 28px tall above the `sm` breakpoint,
                which would make this a SMALLER target than the 40×40 select it
                replaces. The hemisphere is a field-device decision made with
                gloves on — hold it at the 44px minimum on every width. */}
            <div className="[&_[role=radio]]:min-h-11 [&_[role=radio]]:min-w-11">
            <Segmented
              options={[
                { value: "N" as const, label: "N" },
                { value: "S" as const, label: "S" },
              ]}
              value={latHemi}
              onChange={setLatHemi}
              ariaLabel="Latitude hemisphere — north or south"
              disabled={!canEdit}
            />
            </div>
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
            <div className="[&_[role=radio]]:min-h-11 [&_[role=radio]]:min-w-11">
            <Segmented
              options={[
                { value: "E" as const, label: "E" },
                { value: "W" as const, label: "W" },
              ]}
              value={lonHemi}
              onChange={setLonHemi}
              ariaLabel="Longitude hemisphere — east or west"
              disabled={!canEdit}
            />
            </div>
          </div>
        </Field>

        {/* the read-back. Rendered for the DRAFT values, so it contradicts a
            wrong hemisphere while the field still has focus. */}
        {canSeePrecise && (
          <div className="border border-line2 bg-raise/40 px-3 py-2 flex items-start gap-2">
            <Icon
              name="atlas"
              size={14}
              className="text-dim shrink-0 mt-[3px]"
            />
            <div className="min-w-0">
              <div className="label">These coordinates point at</div>
              {hint ? (
                <p className="text-[13px] text-ink leading-snug">{hint}</p>
              ) : (
                <p className="text-[12px] text-dim leading-snug">
                  {!coordsEntered
                    ? "Enter a latitude and longitude to check the hemisphere."
                    : fresh
                      ? "Nothing recognisable at this point — open ocean, or a hemisphere is wrong."
                      : "checking…"}
                </p>
              )}
              {hint && hintSunAlt !== null && (
                <p className="text-[11px] text-dim leading-snug mt-0.5">
                  The Sun is {hintSunAlt.toFixed(0)}° above the horizon there
                  right now
                  {hintSunAlt < -18
                    ? " (astronomical dark)"
                    : hintSunAlt < 0
                      ? " (twilight)"
                      : " (daylight)"}
                  . If that doesn&apos;t match the sky you are standing under,
                  the hemisphere is wrong.
                </p>
              )}
            </div>
          </div>
        )}

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
                  aria-busy={busyWhat === "delete" || undefined}
                  onClick={deleteSelected}
                >
                  {busyWhat === "delete" ? "Deleting…" : "Delete"}
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
                  aria-busy={busyWhat === "preset" || undefined}
                  onClick={() => void submitSaveCurrent(savingName)}
                >
                  {busyWhat === "preset" ? "Saving…" : "Save"}
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
