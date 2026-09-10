// sites.tsx - the SITES sheet (T-SKY-4, plan A.14). Shared between the Sky
// and Settings hubs (ARCHITECTURE.md section 5: sheet names are global; this
// component is registered ONCE and both hubs point at it).
//
// Every SitePanel.tsx feature (phone geolocation, mount GPS, manual entry,
// saved-location CRUD, the hemisphere read-back, the view.site_precise
// privacy rule) lands here in the design's shape - a missing one is a lost
// feature, not a simplification.
import { useEffect, useState, type JSX } from "react";
import { nav } from "../../../router";
import type { SheetProps } from "../../sheets";
import {
  ActionButton, Card, EmptyCard, Field, Label, Mono, Pill, Segmented, Sheet,
  TextInput, honestPress, lockedAttrs, lockedClass,
} from "../../../ui";
import { NxIcon } from "../../../icons";
import { useLock } from "../../../lib/gateHook";
import { useCan, useCanViewSitePrecise, accessPhrase } from "../../../../lib/caps";
import {
  fromSigned, toSigned, validateElevation, validateLat, validateLon,
} from "../../../../lib/site";
import { api, ApiError } from "../../../../api";
import {
  applyLocation, deleteLocation, getMountGps, listLocations, saveLocation,
  updateLocation, type LocationInput,
} from "../../../../api/site";
import { useConfig, useStore } from "../../../../store";
import { confirmDialog } from "../../../../components/ConfirmDialog";
import type { SavedLocation } from "../../../../types";
import {
  applyToastText, coordLine, distanceTone, fmtDist, fmtFixLine, horizonIsOpen,
  horizonSummaryLine, isActiveLocation, nearestName, sortByDistance,
  type GeoFix,
} from "./sitesModel";

// Number("") is 0, which passes Number.isFinite and would silently read a
// blank field as coordinate 0. Every text-field conversion routes through
// this so a blank stays NaN and fails validateLat/validateLon/validateElevation.
const toNum = (raw: string): number => (raw.trim() === "" ? NaN : Number(raw));

type FixState = "idle" | "asking" | "ok" | "denied" | "error" | "insecure";

interface EditDraft {
  mode: "new" | "edit";
  id?: string;
  name: string;
  latMag: string;
  latHemi: "N" | "S";
  lonMag: string;
  lonHemi: "E" | "W";
  elev: string;
}

const blankDraft = (): EditDraft => ({
  mode: "new", name: "", latMag: "", latHemi: "N", lonMag: "", lonHemi: "E", elev: "",
});

const draftFromLocation = (loc: SavedLocation): EditDraft => {
  const la = fromSigned(loc.latitude, "lat");
  const lo = fromSigned(loc.longitude, "lon");
  return {
    mode: "edit", id: loc.id, name: loc.name,
    latMag: la.magnitude.toFixed(6), latHemi: la.hemisphere as "N" | "S",
    lonMag: lo.magnitude.toFixed(6), lonHemi: lo.hemisphere as "E" | "W",
    elev: String(loc.elevation_m),
  };
};

export function SitesSheet(_props: SheetProps): JSX.Element {
  const config = useConfig();
  const loadConfig = useStore((s) => s.loadConfig);
  const enqueueToast = useStore((s) => s.enqueueToast);
  const canEdit = useCan("config.site_optics");
  const canSafety = useCan("config.safety");
  const canSeePrecise = useCanViewSitePrecise();
  const { lockedReason: editLocked, onExplain: explainEdit } = useLock({ cap: "config.site_optics" });
  const { lockedReason: horizonLocked, onExplain: explainHorizon } = useLock({ cap: "config.safety" });

  const [locations, setLocations] = useState<SavedLocation[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState<EditDraft | null>(null);

  // ---------------------------------------------------------------- geo fix
  const [fix, setFix] = useState<GeoFix | null>(null);
  const [fixElevM, setFixElevM] = useState<number | null>(null);
  const [fixState, setFixState] = useState<FixState>("idle");

  useEffect(() => {
    if (typeof window === "undefined" || !window.isSecureContext || !("geolocation" in navigator)) {
      setFixState("insecure");
      return;
    }
    setFixState("asking");
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        setFix({ lat: pos.coords.latitude, lon: pos.coords.longitude, accuracyM: pos.coords.accuracy });
        setFixElevM(typeof pos.coords.altitude === "number" ? pos.coords.altitude : null);
        setFixState("ok");
      },
      (e: GeolocationPositionError) => { setFixState(e.code === 1 ? "denied" : "error"); },
      { enableHighAccuracy: true, timeout: 10000, maximumAge: 60000 },
    );
    // Once per sheet open (B.13) - not re-asked on a param/config change.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ------------------------------------------------------------- locations
  const refreshLocations = async () => {
    try {
      setLocations(await listLocations());
    } catch {
      setLocations([]);
    }
  };
  useEffect(() => {
    if (!canEdit) return;
    void refreshLocations();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [canEdit]);

  const sorted = locations ? sortByDistance(locations, fix) : [];

  // ------------------------------------------------------------------ apply
  const pick = async (loc: SavedLocation) => {
    if (busy) return;
    const carryReason = !horizonIsOpen(loc.horizon_points) && !canSafety
      ? `needs ${accessPhrase("config.safety")} to also apply its drawn horizon`
      : null;
    const reason = editLocked ?? carryReason;
    if (reason) { explainEdit(reason); return; }
    setBusy(true);
    try {
      await applyLocation(loc.id);
      await loadConfig();
      enqueueToast({ level: "success", title: applyToastText(loc.name, !horizonIsOpen(loc.horizon_points)) });
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        await loadConfig();
        enqueueToast({ level: "error", title: "Someone else changed the settings - reopen this sheet and pick again." });
      } else if (e instanceof ApiError) {
        enqueueToast({ level: "error", title: e.message || "Could not apply that site." });
      } else {
        enqueueToast({ level: "error", title: e instanceof Error ? e.message : "Could not apply that site." });
      }
    } finally {
      setBusy(false);
    }
  };

  // --------------------------------------------------------- editing card
  const openNew = () => {
    if (editLocked) { explainEdit(editLocked); return; }
    setEditing(blankDraft());
  };
  const openEdit = (loc: SavedLocation) => {
    if (editLocked) { explainEdit(editLocked); return; }
    setEditing(draftFromLocation(loc));
  };

  const fillFromPhone = () => {
    if (!editing) return;
    if (fixState !== "ok" || !fix) {
      explainEdit(
        fixState === "insecure"
          ? "Location needs a secure connection - set one up in Connection."
          : fixState === "denied"
            ? "Location permission denied - enter coordinates by hand."
            : "No phone location yet - enter coordinates by hand.",
      );
      return;
    }
    const la = fromSigned(fix.lat, "lat");
    const lo = fromSigned(fix.lon, "lon");
    setEditing({
      ...editing,
      latMag: la.magnitude.toFixed(6), latHemi: la.hemisphere as "N" | "S",
      lonMag: lo.magnitude.toFixed(6), lonHemi: lo.hemisphere as "E" | "W",
      elev: fixElevM != null ? String(Math.round(fixElevM)) : editing.elev,
    });
  };

  const [mountBusy, setMountBusy] = useState(false);
  const fillFromMount = async () => {
    if (!editing || mountBusy) return;
    setMountBusy(true);
    try {
      const g = await getMountGps();
      if (!g.available) {
        enqueueToast({ level: "info", title: g.detail ?? "Mount GPS unavailable" });
        return;
      }
      const la = fromSigned(g.latitude as number, "lat");
      const lo = fromSigned(g.longitude as number, "lon");
      setEditing((cur) => cur && ({
        ...cur,
        latMag: la.magnitude.toFixed(6), latHemi: la.hemisphere as "N" | "S",
        lonMag: lo.magnitude.toFixed(6), lonHemi: lo.hemisphere as "E" | "W",
        elev: typeof g.elevation_m === "number" ? String(Math.round(g.elevation_m)) : cur.elev,
      }));
    } catch (e) {
      enqueueToast({ level: "error", title: e instanceof Error ? e.message : "Could not reach the mount." });
    } finally {
      setMountBusy(false);
    }
  };

  // ------------------------------------------------------- hemisphere read-back
  const [hintAt, setHintAt] = useState<{ key: string; place: string | null; sunAlt: number | null } | null>(null);
  const latSigned = editing ? toSigned(toNum(editing.latMag), editing.latHemi) : NaN;
  const lonSigned = editing ? toSigned(toNum(editing.lonMag), editing.lonHemi) : NaN;
  const coordsEntered = !!editing
    && validateLat(toNum(editing.latMag)) === null
    && validateLon(toNum(editing.lonMag)) === null;
  const coordKey = `${latSigned},${lonSigned}`;
  const fresh = hintAt && hintAt.key === coordKey ? hintAt : null;

  useEffect(() => {
    if (!editing || !canSeePrecise || !coordsEntered) { setHintAt(null); return; }
    let dead = false;
    const timer = setTimeout(() => {
      api.get<{ place_hint?: string; sun_alt_deg?: number }>(
        `/api/site/sky?lat=${latSigned}&lon=${lonSigned}`,
      ).then((s) => {
        if (dead) return;
        setHintAt({
          key: `${latSigned},${lonSigned}`,
          place: s.place_hint ?? null,
          sunAlt: typeof s.sun_alt_deg === "number" ? s.sun_alt_deg : null,
        });
      }).catch(() => { if (!dead) setHintAt(null); });
    }, 350);
    return () => { dead = true; clearTimeout(timer); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editing?.latMag, editing?.latHemi, editing?.lonMag, editing?.lonHemi, canSeePrecise, coordsEntered]);

  // ------------------------------------------------------------------- save
  const saveEditing = async () => {
    if (!editing || busy) return;
    const err = validateLat(toNum(editing.latMag)) || validateLon(toNum(editing.lonMag))
      || validateElevation(toNum(editing.elev));
    if (err) { enqueueToast({ level: "error", title: err }); return; }
    const source = editing.mode === "edit" ? (locations ?? []).find((l) => l.id === editing.id) ?? null : null;
    const body: LocationInput = {
      name: editing.name.trim() || "New site",
      latitude: toSigned(toNum(editing.latMag), editing.latHemi),
      longitude: toSigned(toNum(editing.lonMag), editing.lonHemi),
      elevation_m: toNum(editing.elev),
      // A coordinate/name edit never touches the drawn horizon - that is the
      // Horizon editor's job (A.15). A brand-new location starts with none.
      horizon_min_deg: source ? source.horizon_min_deg : null,
      horizon_points: source ? source.horizon_points ?? null : undefined,
    };
    setBusy(true);
    try {
      if (editing.mode === "new") await saveLocation(body);
      else await updateLocation(editing.id as string, body);
      await refreshLocations();
      setEditing(null);
      enqueueToast({ level: "success", title: `Saved "${body.name}".` });
    } catch (e) {
      if (e instanceof ApiError && e.code === "name_collision") {
        enqueueToast({ level: "error", title: "A site with that name already exists - pick another name." });
      } else if (e instanceof ApiError && e.code === "library_full") {
        enqueueToast({ level: "error", title: "The site library is full - delete one first." });
      } else if (e instanceof ApiError && e.status === 404) {
        enqueueToast({ level: "error", title: "That saved location is gone - refresh the list." });
      } else {
        enqueueToast({ level: "error", title: e instanceof Error ? e.message : "Could not save that site." });
      }
    } finally {
      setBusy(false);
    }
  };

  const deleteEditing = async () => {
    if (!editing || editing.mode !== "edit" || !editing.id || busy) return;
    const name = editing.name.trim() || "this site";
    const ok = await confirmDialog({
      title: `Delete "${name}"?`,
      body: "This removes it from the saved list. It does not change whichever site is active right now.",
      confirmLabel: "DELETE", cancelLabel: "KEEP", tone: "danger",
    });
    if (!ok) return;
    setBusy(true);
    try {
      await deleteLocation(editing.id);
      await refreshLocations();
      setEditing(null);
      enqueueToast({ level: "success", title: `Deleted "${name}".` });
    } catch (e) {
      enqueueToast({ level: "error", title: e instanceof Error ? e.message : "Could not delete that site." });
    } finally {
      setBusy(false);
    }
  };

  // --------------------------------------------------------------- render
  const fixLine = (): string => {
    if (fixState === "insecure") return "Location needs a secure connection - set one up in Connection.";
    if (fixState === "asking" || fixState === "idle") return "asking the phone…";
    if (fixState === "denied") return "Location permission denied - distances are not shown. Enter coordinates by hand below.";
    if (fixState === "error") return "Couldn't get your location - distances are not shown. Enter coordinates by hand below.";
    return fix ? fmtFixLine(fix, nearestName(locations ?? [], fix)) : "asking the phone…";
  };

  return (
    <Sheet
      title="SITES"
      sub="one horizon profile per site · sorted by distance from the phone"
      backLabel="BACK"
      onBack={() => nav.back()}
      right={<Pill tone="accent" onClick={() => nav.back()} ariaLabel="Done, back to Sky">DONE</Pill>}
      data-testid="sites-sheet"
    >
      <Card data-testid="phone-gps">
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <NxIcon name="gps" size={18} />
          <div style={{ minWidth: 0 }}>
            <Label>PHONE GPS</Label>
            <Mono size={11}>{fixLine()}</Mono>
          </div>
        </div>
      </Card>

      {!canEdit && (
        <EmptyCard
          title="SITE LIBRARY HIDDEN"
          hint={`Saved sites need ${accessPhrase("config.site_optics")} to view.`}
        />
      )}

      {canEdit && locations != null && sorted.length === 0 && (
        <EmptyCard title="NO SAVED SITES YET" hint="Add the spot you are standing at with + NEW SITE HERE, below." />
      )}

      {canEdit && sorted.map((loc) => {
        const active = isActiveLocation(loc, config?.site ?? null);
        const carryReason = !horizonIsOpen(loc.horizon_points) && !canSafety
          ? `needs ${accessPhrase("config.safety")} to also apply its drawn horizon`
          : null;
        const pickReason = editLocked ?? carryReason;
        const open = horizonIsOpen(loc.horizon_points);
        return (
          <Card key={loc.id} data-testid={`site-row-${loc.id}`}>
            <div style={{ display: "flex", alignItems: "flex-start", gap: 10 }}>
              <button
                type="button"
                role="radio"
                aria-checked={active}
                aria-label={`Select ${loc.name} as the active site`}
                data-testid={`site-radio-${loc.id}`}
                className={lockedClass(pickReason, "")}
                style={{
                  width: 22, height: 22, borderRadius: "50%", flexShrink: 0, marginTop: 2,
                  border: `2px solid ${active ? "var(--accent)" : "var(--line-bright)"}`,
                  background: active ? "var(--accent)" : "transparent", cursor: "pointer",
                }}
                onClick={honestPress(pickReason, explainEdit, () => void pick(loc))}
                {...lockedAttrs(pickReason)}
              />
              <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", gap: 2 }}>
                <span className="nx-row-title">{loc.name}</span>
                <Mono size={10} tone={loc.distanceM != null ? distanceTone(loc.distanceM) : "dim"}>
                  {canSeePrecise
                    ? (loc.distanceM != null ? `${fmtDist(loc.distanceM)} · ` : "")
                    : ""}
                  {coordLine(loc, canSeePrecise)}
                </Mono>
                <Mono size={10} tone={open ? "warn" : "dim"}>{horizonSummaryLine(loc.horizon_points)}</Mono>
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 6, flexShrink: 0 }}>
                <ActionButton kind="ghost" size="md" lockedReason={horizonLocked} onExplain={explainHorizon}
                  onPress={() => nav.sheet("horizon", { site: loc.id })}
                  data-testid={`edit-horizon-${loc.id}`}>
                  EDIT HORIZON
                </ActionButton>
                <ActionButton kind="ghost" size="md" lockedReason={editLocked} onExplain={explainEdit}
                  onPress={() => openEdit(loc)} data-testid={`edit-site-${loc.id}`}>
                  EDIT
                </ActionButton>
              </div>
            </div>
          </Card>
        );
      })}

      {canEdit && (
        <ActionButton kind="secondary" size="lg" full lockedReason={editLocked} onExplain={explainEdit}
          onPress={openNew} data-testid="new-site-here">
          + NEW SITE HERE
        </ActionButton>
      )}

      <Mono size={10.5} className="nx-field-hint">
        Spots a few metres apart share one GPS fix, so the list is by distance and the app remembers
        which one you picked here last time. Picking a site keeps you here so you can edit it; the
        finder, the dome and the reach list all use the selected site&apos;s horizon.
      </Mono>

      {editing && (
        <Card tone="accent" data-testid="site-edit-card">
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            <Label>{editing.mode === "new" ? "NEW SITE" : "EDIT SITE"}</Label>
            <Field label="Name" htmlFor="site-name">
              <TextInput id="site-name" ariaLabel="Site name" value={editing.name}
                onChange={(v) => setEditing({ ...editing, name: v })}
                placeholder="e.g. Back lawn, Upper deck, Club field" />
            </Field>
            <Field label="Latitude" htmlFor="site-lat">
              <div style={{ display: "flex", gap: 8 }}>
                <TextInput id="site-lat" mono ariaLabel="Latitude magnitude, 0 to 90" value={editing.latMag}
                  onChange={(v) => setEditing({ ...editing, latMag: v })} placeholder="0.000000" />
                <Segmented label="Latitude hemisphere" value={editing.latHemi}
                  onChange={(v) => setEditing({ ...editing, latHemi: v })}
                  options={[{ value: "N" as const, label: "N" }, { value: "S" as const, label: "S" }]} />
              </div>
            </Field>
            <Field label="Longitude" htmlFor="site-lon">
              <div style={{ display: "flex", gap: 8 }}>
                <TextInput id="site-lon" mono ariaLabel="Longitude magnitude, 0 to 180" value={editing.lonMag}
                  onChange={(v) => setEditing({ ...editing, lonMag: v })} placeholder="0.000000" />
                <Segmented label="Longitude hemisphere" value={editing.lonHemi}
                  onChange={(v) => setEditing({ ...editing, lonHemi: v })}
                  options={[{ value: "E" as const, label: "E" }, { value: "W" as const, label: "W" }]} />
              </div>
            </Field>
            {canSeePrecise && (
              <div>
                <Label size={10}>THESE COORDINATES POINT AT</Label>
                {fresh?.place ? (
                  <Mono size={11}>{fresh.place}</Mono>
                ) : (
                  <Mono size={11} tone="dim">
                    {!coordsEntered
                      ? "Enter a latitude and longitude to check the hemisphere."
                      : fresh
                        ? "Nothing recognisable at this point - open ocean, or a hemisphere is wrong."
                        : "checking…"}
                  </Mono>
                )}
                {fresh?.place && fresh.sunAlt != null && (
                  <Mono size={10} tone="dim">
                    The Sun is {fresh.sunAlt.toFixed(0)}° above the horizon there right now
                    {fresh.sunAlt < -18 ? " (astronomical dark)" : fresh.sunAlt < 0 ? " (twilight)" : " (daylight)"}.
                    If that doesn&apos;t match the sky you are standing under, the hemisphere is wrong.
                  </Mono>
                )}
              </div>
            )}
            <Field label="Elevation (m)" htmlFor="site-elev">
              <TextInput id="site-elev" mono ariaLabel="Elevation in metres" value={editing.elev}
                onChange={(v) => setEditing({ ...editing, elev: v })} placeholder="0" />
            </Field>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              <ActionButton kind="ghost" size="md" onPress={fillFromPhone}
                lockedReason={fixState !== "ok" ? (
                  fixState === "insecure" ? "Location needs a secure connection - set one up in Connection."
                    : fixState === "denied" ? "Location permission denied - enter coordinates by hand."
                      : "No phone location yet - enter coordinates by hand."
                ) : null}
                onExplain={(r) => enqueueToast({ level: "warning", title: r })}
                data-testid="fill-from-phone">
                FILL FROM THIS PHONE
              </ActionButton>
              <ActionButton kind="ghost" size="md" busy={mountBusy} onPress={() => void fillFromMount()}
                data-testid="fill-from-mount">
                FILL FROM THE MOUNT
              </ActionButton>
            </div>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              <ActionButton kind="ghost" size="md" onPress={() => setEditing(null)}>CANCEL</ActionButton>
              <ActionButton kind="primary" size="md" busy={busy} onPress={() => void saveEditing()}
                data-testid="save-site">
                {editing.mode === "new" ? "SAVE SITE" : "SAVE"}
              </ActionButton>
              {editing.mode === "edit" && (
                <ActionButton kind="danger" size="md" busy={busy} onPress={() => void deleteEditing()}
                  data-testid="delete-site">
                  DELETE
                </ActionButton>
              )}
            </div>
          </div>
        </Card>
      )}
    </Sheet>
  );
}
