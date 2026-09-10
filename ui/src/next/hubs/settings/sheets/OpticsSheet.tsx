// OpticsSheet.tsx - Settings > RIG > Optics (plan section C.5).
//
// THE BANNER COMES FIRST, AND IT IS THE POINT. The ACTIVE PROFILE's optics block
// beats the global block this sheet edits, and it is swapped WHOLE - a profile
// that only meant to change a focal length also replaces the pixel size nobody
// thought they were touching. With no tell, that is how a `polar_align:"sim"`
// pin kept the polar aligner simulated for twelve days on a real rig. So the
// banner lists all seven keys with running-vs-panel values, the save button
// repeats the warning at the moment the false belief would form, and the FoV
// preview says out loud when what the rig frames differs from what these numbers
// give.
//
// THE PREVIEW AND THE TRUTH ARE TWO DIFFERENT NUMBERS, on purpose:
//   - `config.optics_computed` is what the rig will actually frame, and it is
//     what the live line and the preview rectangle read while nothing is dirty;
//   - `next/lib/fov`'s atan formula runs on the DRAFT while the user drags, so a
//     dial has a visible consequence before it is saved.
// On save the readout snaps back to the server's answer. See `opticsModel.ts`.
//
// APERTURE AND REDUCER ARE RIG FIELDS NOW (D-SET-1). `Optics.aperture_mm`/
// `reducer` join the same draft the focal length lives in and save on the same
// `PUT /api/optics` press. They used to be phone-local (`astrodeck-next-optics-
// aux`); the mount effect below moves that key to the rig ONCE, through
// `next/lib/storageMigration.ts`'s `migrateKey` - never merging, because the
// rig's own value always wins a conflict. `USE THE REDUCED FOCAL LENGTH` is
// still the one press that turns a reducer into the number the rig reads:
// `reducer` itself is recorded and never multiplied into `focal_length_mm`,
// on the rig exactly as it was on the phone.

import { useEffect, useMemo, useState, type CSSProperties, type JSX } from "react";
import {
  ActionButton,
  BannerCard,
  Card,
  Dial,
  Field,
  Label,
  ListRow,
  LockNote,
  Mono,
  ReadoutGrid,
  ReadoutTile,
  Sheet,
  Switch,
  TextInput,
} from "../../../ui";
import { NxIcon } from "../../../icons";
import { nav } from "../../../router";
import { useLock } from "../../../lib/gateHook";
import { migrateKey, migrationDone } from "../../../lib/storageMigration";
import { fmtDuration } from "../../../lib/format";
import { samplingArcsecPerPx } from "../../../lib/fov";
import { api, ApiError } from "../../../../api";
import {
  clearProfileOverrides,
  getPackStatus,
  listDrivers,
  setProfileProviders,
  setProvidersConfig,
} from "../../../../api/backends";
import { useConfig, usePreview, usePrincipal, useProviders, useStore } from "../../../../store";
import { accessPhrase, useCan } from "../../../../lib/caps";
import { eligibleTaskDrivers } from "../../../../lib/equipment";
import {
  entryOf,
  isProfileOverride,
  opticsKey,
  opticsOverridden,
  opticsOverrideProfile,
  providerKey,
  showValue,
  valueOf,
  type OpticsKey,
} from "../../../../lib/effective";
import { globalProvidersBody, providerWriteNote, providerWriteTarget } from "../../../../lib/providerWrite";
import { LayerChip } from "../../../../components/OverrideNote";
import { WcsStampEditor } from "../tuning/files";
import { packStatusLabel } from "../../../../components/settings/skyAtlasMeta";
import type { DriverInfo, Optics, PackStatus } from "../../../../types";
import {
  APERTURE_STOPS,
  FOCAL_STOPS,
  OPTICS_AUX_KEY,
  OPTICS_MIGRATION_TOAST,
  PIXEL_STOPS,
  REDUCER_STOPS,
  computedFov,
  dialStops,
  draftFov,
  fRatioFrom,
  focalInvalid,
  fovCaption,
  fovDisagrees,
  liveLine,
  parseLegacyOpticsAux,
  reducedFocalMm,
  resolveDraft,
  solveCalibration,
  type LegacyOpticsAux,
} from "./opticsModel";

const PARA: CSSProperties = {
  margin: "6px 0 0",
  fontSize: 11.5,
  lineHeight: 1.45,
  color: "var(--text-faint)",
};

/** px per degree in the preview, from the prototype: a 300 px box spans 5.2
 *  degrees, so the M31 ellipse and the sensor rectangle share one scale and the
 *  comparison is a real one rather than a decorative one. */
const PX_PER_DEG = 300 / 5.2;
const M31_W_DEG = 3.2;
const M31_H_DEG = 1.0;

type TileId = "focal" | "aperture" | "reducer" | "pixel";

/** The nine keys the banner enumerates, ordered as the form reads. Ordered and
 *  complete because the whole point of a whole-block swap is that it reaches
 *  fields nobody thought they were changing - aperture and reducer included,
 *  now that they are part of the same `Optics` block a profile overrides
 *  whole (D-SET-1). */
const BANNER_KEYS: [OpticsKey, string][] = [
  ["focal_length_mm", "Focal length"],
  ["telescope_name", "Telescope name"],
  ["auto_from_camera", "Sensor from camera"],
  ["pixel_size_um", "Pixel size"],
  ["sensor_width_px", "Sensor width"],
  ["sensor_height_px", "Sensor height"],
  ["guide_focal_length_mm", "Guide scope focal length"],
  ["aperture_mm", "Aperture"],
  ["reducer", "Reducer"],
];

const FMT: Record<OpticsKey, (v: unknown) => string> = {
  focal_length_mm: (v) => `${v} mm`,
  pixel_size_um: (v) => `${v} µm`,
  sensor_width_px: (v) => `${v} px`,
  sensor_height_px: (v) => `${v} px`,
  auto_from_camera: (v) => (v ? "on" : "off"),
  guide_focal_length_mm: (v) => `${v} mm`,
  telescope_name: (v) => `"${v}"`,
  aperture_mm: (v) => `${v} mm`,
  reducer: (v) => `${v}x`,
};

/** The label for a stored solve provider the rig no longer offers - a driver
 *  that went unreachable, or the legacy "backend" alias. A choice that vanishes
 *  from the control that owns it reads as "this rig cannot do that", so the
 *  literal string the config is carrying stays listed. */
function stickySolverLabel(value: string): string {
  return value === "backend" ? "Backend (legacy)" : value;
}

export function OpticsSheet(): JSX.Element {
  const config = useConfig();
  const preview = usePreview();
  const providers = useProviders();
  const optics = config?.optics;
  const computed = config?.optics_computed;
  const canEdit = useCan("config.site_optics");
  const canBackend = useCan("config.backend");
  const editLock = useLock({ cap: "config.site_optics" });
  const backendLock = useLock({ cap: "config.backend" });
  const principal = usePrincipal();

  // D-SET-1: an engine old enough to answer with no `aperture_mm` at all (not
  // even 0) does not carry the field yet. `typeof` rather than trusting the
  // static type, because the type says "always a number" and an older rig on
  // the wire can still disagree with it.
  const apertureFieldSupported = typeof optics?.aperture_mm === "number";

  // A role without `config.site_optics` cannot run the migration below, and
  // must not lose the phone's own copy while it waits for someone who can -
  // so it keeps reading `astrodeck-next-optics-aux` until this key's flag is
  // set by a session that could write it. `useMemo` rather than a render-time
  // call: the read only needs to happen again when the role itself changes.
  const legacyAux: LegacyOpticsAux | null = useMemo(() => {
    if (canEdit) return null;
    try {
      const raw = localStorage.getItem(OPTICS_AUX_KEY);
      return raw ? parseLegacyOpticsAux(raw) : null;
    } catch {
      return null;
    }
  }, [canEdit]);

  // The one-time migration (D-FU-1's contract, D-SET-1's last key). Runs once
  // per phone: `migrationDone` short-circuits every render after the first
  // that ever resolves it, and `migrateKey` itself is idempotent even if this
  // effect somehow re-fired. `serverHasValue` is the rig's own answer - a
  // non-zero aperture means someone already set one here - so a conflict never
  // merges, it only ever discards the phone's copy in favour of the rig's.
  useEffect(() => {
    if (!optics || !apertureFieldSupported) return;
    if (migrationDone(OPTICS_AUX_KEY)) return;
    let alive = true;
    void migrateKey<LegacyOpticsAux>({
      key: OPTICS_AUX_KEY,
      cap: "config.site_optics",
      principal,
      supported: true,
      serverHasValue: optics.aperture_mm > 0,
      read: parseLegacyOpticsAux,
      put: async (legacy) => {
        await api.put("/api/optics", {
          optics: { ...optics, aperture_mm: legacy.apertureMm ?? 0, reducer: legacy.reducer },
          version: config?.version ?? null,
        });
      },
      conflictToast: OPTICS_MIGRATION_TOAST,
      onToast: (m) => useStore.getState().enqueueToast({ level: "info", title: m }),
    }).then((outcome) => {
      if (alive && (outcome === "migrated" || outcome === "server-wins")) {
        void useStore.getState().loadConfig();
      }
    });
    return () => { alive = false; };
    // `optics` itself is a fresh object every config reload (see the seed
    // effect below); the aperture value and the principal are the only two
    // inputs the decision actually depends on, and `migrationDone` above is
    // the real guard against a second run.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [optics?.aperture_mm, apertureFieldSupported, principal]);

  const [tile, setTile] = useState<TileId>("focal");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [savedAt, setSavedAt] = useState<number | null>(null);
  const [clearing, setClearing] = useState(false);
  const [pack, setPack] = useState<PackStatus | null>(null);
  const [drivers, setDrivers] = useState<DriverInfo[]>([]);

  // Fetched only for a principal who could WRITE the row: a viewer's read-only
  // render must not put a request on the wire it can never act on, and the
  // solver row degrades to the resolved label plus its own stored value.
  useEffect(() => {
    if (!canBackend) return;
    let alive = true;
    void listDrivers()
      .then((r) => { if (alive) setDrivers(r.drivers ?? []); })
      .catch(() => { /* the row degrades to auto + the resolved label */ });
    return () => { alive = false; };
  }, [canBackend]);

  // Seeded on the FIRST render, and re-seeded keyed on the SERIALISED optics
  // and nothing else. `optics` is a fresh object on every config reload, so a
  // dep on the object itself threw away a focal length the user was halfway
  // through typing every time any unrelated config write landed
  // (OpticsPanel.tsx's own comment, and SitePanel keys the same way).
  const seed = JSON.stringify(optics ?? null);
  const [draft, setDraft] = useState<Optics | null>(() => (optics ? { ...optics } : null));
  useEffect(() => {
    const fresh = JSON.parse(seed) as Optics | null;
    setDraft(fresh ? { ...fresh } : null);
    setErr(null);
    setSavedAt(null);
  }, [seed]);

  // The pack status is what the row's sub says, and reading it needs the same
  // capability that owns the pack. A non-holder issues nothing rather than
  // eating a 403 to fill in a caption.
  useEffect(() => {
    if (!canEdit) return;
    let live = true;
    void (async () => {
      try {
        const s = await getPackStatus();
        if (live) setPack(s);
      } catch {
        /* the row keeps its "checking" caption rather than claiming a state */
      }
    })();
    return () => { live = false; };
  }, [canEdit]);

  const overridden = opticsOverridden(config);
  const overrideProfile = opticsOverrideProfile(config);
  const overrideProfileId = entryOf(config, opticsKey("focal_length_mm"))?.profile_id ?? null;

  const view = useMemo(
    () => (draft ? resolveDraft(draft, computed) : { flMm: 0, pxUm: 0, wPx: 0, hPx: 0 }),
    [draft, computed],
  );

  // The number shown and dragged: the phone's own legacy copy for a locked
  // role that still has one, the draft (the rig's own field) otherwise. Only
  // the DISPLAY follows the legacy copy - a locked role cannot press SAVE, so
  // there is nothing here for it to write.
  const effectiveApertureMm = legacyAux ? legacyAux.apertureMm : (draft?.aperture_mm ?? null);
  const effectiveReducer = legacyAux ? legacyAux.reducer : (draft?.reducer ?? 1);

  const localFov = useMemo(() => draftFov(view, effectiveReducer), [view, effectiveReducer]);
  const serverFov = computedFov(computed);
  const dirty = draft ? JSON.stringify(draft) !== seed : false;

  // While the user is dragging, the preview must follow the drag. Once it is
  // saved, the preview must show what the rig will frame - which is not always
  // the same number, and when it is not, the note under the caption says so.
  const shownFov = !dirty && serverFov ? serverFov : localFov;
  const disagrees = !dirty && fovDisagrees(serverFov, localFov);

  if (!optics || !draft) {
    return (
      <Sheet
        title="OPTICS · FIELD OF VIEW"
        icon={<NxIcon name="optics" size={18} />}
        sub="waiting for the rig's configuration"
        onBack={() => nav.back()}
        backLabel="SETTINGS"
        data-testid="sheet-optics"
      >
        <Card>
          <Mono size={11} tone="dim">
            The rig has not sent its optics yet. Nothing is missing - this sheet fills in as soon
            as the first configuration arrives.
          </Mono>
        </Card>
      </Sheet>
    );
  }

  const patch = (p: Partial<Optics>) => setDraft((d) => (d ? { ...d, ...p } : d));

  const invalid = focalInvalid(draft.focal_length_mm);
  const samp = view.pxUm > 0 && view.flMm > 0 ? samplingArcsecPerPx(view.pxUm, view.flMm) : 0;
  const solved = solveCalibration(preview?.field, view.pxUm);
  const nowS = Date.now() / 1000;

  const save = async () => {
    if (busy || !dirty || invalid || !canEdit) return;
    setErr(null);
    setBusy(true);
    try {
      await api.put("/api/optics", { optics: draft, version: config?.version ?? null });
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

  const dropOverride = async () => {
    if (!overrideProfileId || clearing) return;
    setClearing(true);
    setErr(null);
    try {
      await clearProfileOverrides(overrideProfileId, { optics: true });
      await useStore.getState().loadConfig();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "could not clear the profile override");
    } finally {
      setClearing(false);
    }
  };

  // ------------------------------------------------------------ solve provider
  //
  // DRIVER-DERIVED, not a hard-coded vocabulary (review #23). A configured NINA
  // or ASIAIR whose probe offers `solve` is a real plate solver on this rig, and
  // a fixed four-row list could not name it - so the rows come from the same
  // `eligibleTaskDrivers` rule the autofocus and polar rows use ("enabled,
  // reachable, and its probe actually offers that task"). The control is a
  // select rather than a segmented group because the list is now unbounded:
  // `.nx-seg` is `overflow: hidden`, so a fifth driver on a phone would be
  // clipped out of reach rather than wrapped.
  const solveEntry = entryOf(config, providerKey("solve"));
  const solveValue = valueOf<string>(config, providerKey("solve"), config?.providers?.solve ?? "auto");
  const solveTarget = providerWriteTarget(solveEntry);
  const solveNote = providerWriteNote(solveTarget);
  const solveEligible = eligibleTaskDrivers("solve", drivers);
  const solveSticky = solveValue !== "auto" && !solveEligible.some((d) => d.id === solveValue);

  const saveSolve = async (v: string) => {
    if (busy || !canBackend) return;
    setBusy(true);
    setErr(null);
    try {
      if (solveTarget.layer === "profile" && solveTarget.profileId) {
        await setProfileProviders(solveTarget.profileId, { solve: v });
      } else {
        await setProvidersConfig(globalProvidersBody(config?.providers, "solve", v));
      }
      await useStore.getState().loadConfig();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Could not change the solver.");
    } finally {
      setBusy(false);
    }
  };

  // A `solve` pin written into a profile could be EDITED but never REMOVED
  // (review #24): the only unpin on this branch was polar's. Same call, same
  // copy, and honest-disabled rather than hidden for a non-holder - a pinned row
  // with no statement of who can unpin it reads as unremovable.
  const dropSolvePin = async () => {
    const id = solveEntry?.profile_id;
    if (!id || busy) return;
    setBusy(true);
    setErr(null);
    try {
      await clearProfileOverrides(id, { providers: ["solve"] });
      await useStore.getState().loadConfig();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "could not clear the profile pin");
    } finally {
      setBusy(false);
    }
  };

  // ------------------------------------------------------------------- the dial
  //
  // An old engine with no `aperture_mm` field at all cannot take either dial's
  // value, so neither tile is offered and a stale selection falls back to
  // FOCAL rather than driving a dial with nothing behind it.
  const effectiveTile: TileId =
    (tile === "aperture" || tile === "reducer") && !apertureFieldSupported ? "focal" : tile;
  const dialLock = effectiveTile === "pixel" && draft.auto_from_camera
    ? "sensor details come from the camera - turn that off below to pin the pixel size by hand"
    : editLock.lockedReason;

  let dialLabel = "FOCAL LENGTH";
  let dialOptions: { value: number; label: string }[] = [];
  let dialValue = 0;
  let onDial: (n: number) => void = () => {};
  if (effectiveTile === "focal") {
    dialValue = draft.focal_length_mm;
    dialOptions = dialStops(FOCAL_STOPS, dialValue, 0).map((v) => ({ value: v, label: `${v} mm` }));
    onDial = (v) => patch({ focal_length_mm: v });
  } else if (effectiveTile === "aperture") {
    dialLabel = "APERTURE";
    dialValue = effectiveApertureMm ?? 0;
    dialOptions = dialStops([0, ...APERTURE_STOPS], dialValue, 0).map((v) => ({
      value: v,
      label: v === 0 ? "not set" : `${v} mm`,
    }));
    onDial = (v) => patch({ aperture_mm: v });
  } else if (effectiveTile === "reducer") {
    dialLabel = "REDUCER";
    dialValue = effectiveReducer;
    dialOptions = dialStops(REDUCER_STOPS, dialValue, 2).map((v) => ({
      value: v,
      label: `${v.toFixed(2)}x`,
    }));
    onDial = (v) => patch({ reducer: v });
  } else {
    dialLabel = "PIXEL";
    dialValue = draft.pixel_size_um;
    dialOptions = dialStops(PIXEL_STOPS, dialValue, 2).map((v) => ({
      value: v,
      label: `${Number(v.toFixed(2))} µm`,
    }));
    onDial = (v) => patch({ pixel_size_um: v });
  }

  const boxW = Math.min(300, shownFov.wDeg * PX_PER_DEG);
  const boxH = Math.min(150, shownFov.hDeg * PX_PER_DEG);

  return (
    <Sheet
      title="OPTICS · FIELD OF VIEW"
      icon={<NxIcon name="optics" size={18} />}
      live={
        <span data-testid="optics-live">
          {liveLine(view.flMm, fRatioFrom(computed, view.flMm, effectiveApertureMm ?? 0), view.pxUm, shownFov)}
        </span>
      }
      right={overridden ? <LayerChip entry={entryOf(config, opticsKey("focal_length_mm"))} /> : undefined}
      onBack={() => nav.back()}
      backLabel="SETTINGS"
      data-testid="sheet-optics"
      footer={
        <>
          <ActionButton
            kind="primary"
            size="lg"
            full
            glyph={<NxIcon name="check" size={16} />}
            onPress={() => void save()}
            busy={busy}
            lockedReason={
              editLock.lockedReason ??
              (invalid ? "Focal length must be between 1 and 20000 mm." : null)
            }
            onExplain={editLock.onExplain}
            data-testid="optics-save"
          >
            {busy ? "SAVING" : dirty ? "SAVE OPTICS" : savedAt ? "SAVED" : "SAVE OPTICS"}
          </ActionButton>
        </>
      }
    >
      {/* --------------------------------------------- the override banner */}
      {overridden && (
        <BannerCard
          tone="warn"
          data-testid="optics-override"
          text={
            <span>
              The rig is using the optics from equipment profile &quot;{overrideProfile ?? "(unnamed)"}
              &quot;, not the values below. A profile&apos;s optics block replaces every field at
              once, including the ones it never set.
              <ul style={{ margin: "6px 0 0", paddingLeft: 16 }}>
                {BANNER_KEYS.map(([key, label]) => {
                  const e = entryOf(config, opticsKey(key));
                  if (!e) return null;
                  const same = Object.is(e.value, e.config);
                  return (
                    <li key={key} data-testid="optics-override-key">
                      {label}: {showValue(e.value, FMT[key])}
                      {same ? " - same as this sheet" : ` - this sheet shows ${showValue(e.config, FMT[key])}`}
                    </li>
                  );
                })}
              </ul>
            </span>
          }
          cta={
            overrideProfileId
              ? {
                  label: clearing ? "DROPPING" : "DROP THE PROFILE'S OPTICS",
                  onPress: () => {
                    if (backendLock.lockedReason) {
                      backendLock.onExplain(backendLock.lockedReason);
                      return;
                    }
                    void dropOverride();
                  },
                }
              : undefined
          }
        />
      )}

      {/* ----------------------------------------------------- FoV preview */}
      <Card data-testid="optics-fov">
        <div
          style={{
            position: "relative",
            width: 300,
            maxWidth: "100%",
            height: 150,
            margin: "0 auto",
            borderRadius: 10,
            overflow: "hidden",
            background: "radial-gradient(ellipse at 50% 50%, #101a33 0%, var(--bg) 75%)",
          }}
        >
          <div
            aria-hidden="true"
            style={{
              position: "absolute", left: "50%", top: "50%", transform: "translate(-50%,-50%)",
              width: M31_W_DEG * PX_PER_DEG, height: M31_H_DEG * PX_PER_DEG,
              borderRadius: "50%", border: "1px dashed rgba(155,81,224,.7)",
              background: "rgba(155,81,224,.12)",
            }}
          />
          <div
            data-testid="optics-fov-box"
            aria-hidden="true"
            style={{
              position: "absolute", left: "50%", top: "50%", transform: "translate(-50%,-50%)",
              width: boxW, height: boxH,
              border: "1.5px solid var(--accent)",
              boxShadow: "0 0 14px color-mix(in srgb, var(--accent) 30%, transparent)",
            }}
          />
          <div style={{ position: "absolute", left: 8, bottom: 6 }}>
            <Mono size={10} tone="dim">M31 for scale · 3.2° × 1.0°</Mono>
          </div>
        </div>
        <div style={{ marginTop: 8, textAlign: "center" }} data-testid="optics-caption">
          <Mono size={12}>{fovCaption(shownFov, view.pxUm, view.flMm)}</Mono>
        </div>
        {disagrees && (
          <p style={PARA} data-testid="optics-disagree">
            The rig frames {serverFov?.wDeg.toFixed(2)}° wide, not the {localFov.wDeg.toFixed(2)}°
            these values give. That gap is the profile override above, not a rounding difference.
          </p>
        )}
      </Card>

      {/* ------------------------------------------------- readouts + dial */}
      {/* `nx-readouts-wrap` (next.css) wraps the row inside the 420 px panel
          instead of clipping FOCAL LENGTH a character short. */}
      <ReadoutGrid cols={4} className="nx-readouts-wrap" data-testid="optics-tiles">
        <ReadoutTile
          label="FOCAL LENGTH"
          value={view.flMm > 0 ? `${Math.round(draft.focal_length_mm)} mm` : "not set"}
          sub={effectiveReducer === 1 ? "native" : "with reducer"}
          selected={effectiveTile === "focal"}
          onSelect={() => setTile("focal")}
          data-testid="optics-tile-focal"
        />
        {apertureFieldSupported && (
          <ReadoutTile
            label="APERTURE"
            value={effectiveApertureMm ? `${Math.round(effectiveApertureMm)} mm` : "not set"}
            sub={fRatioFrom(computed, view.flMm, effectiveApertureMm ?? 0)}
            selected={effectiveTile === "aperture"}
            onSelect={() => setTile("aperture")}
            data-testid="optics-tile-aperture"
          />
        )}
        {apertureFieldSupported && (
          <ReadoutTile
            label="REDUCER"
            value={`${effectiveReducer.toFixed(2)}x`}
            sub={effectiveReducer === 1 ? "none" : "recorded, not multiplied"}
            selected={effectiveTile === "reducer"}
            onSelect={() => setTile("reducer")}
            data-testid="optics-tile-reducer"
          />
        )}
        <ReadoutTile
          label="PIXEL"
          value={view.pxUm > 0 ? `${Number(view.pxUm.toFixed(2))} µm` : "not set"}
          sub={samp > 0 ? `${samp.toFixed(2)}″/px` : "needs a focal length"}
          selected={effectiveTile === "pixel"}
          onSelect={() => setTile("pixel")}
          data-testid="optics-tile-pixel"
        />
      </ReadoutGrid>

      <Dial<number>
        label={dialLabel}
        options={dialOptions}
        value={dialValue}
        onChange={onDial}
        lockedReason={dialLock}
        onExplain={editLock.onExplain}
        data-testid="optics-dial"
      />

      {!apertureFieldSupported && (
        <Card data-testid="optics-aperture-unsupported">
          <Mono size={11} tone="dim">
            This engine does not carry aperture and reducer yet. The rig frames from the focal
            length above either way.
          </Mono>
        </Card>
      )}

      {apertureFieldSupported && (
        <Card>
          <p style={{ ...PARA, margin: 0 }}>
            Aperture and reducer are saved with the rig&apos;s optics now. The rig still only ever
            frames from the focal length above - the reducer is recorded, never multiplied into it;
            USE THE REDUCED FOCAL LENGTH is the one press that turns it into that number.
          </p>
          {legacyAux && (
            <LockNote
              reason={`needs ${accessPhrase("config.site_optics")} to move this phone's aperture and reducer to the rig - showing this phone's own copy until then`}
              data-testid="optics-legacy-note"
            />
          )}
          {effectiveReducer !== 1 && (
            <div style={{ marginTop: 8 }}>
              <ActionButton
                kind="secondary"
                onPress={() => {
                  patch({
                    focal_length_mm: reducedFocalMm(draft.focal_length_mm, draft.reducer),
                    reducer: 1,
                  });
                  setTile("focal");
                }}
                lockedReason={editLock.lockedReason}
                onExplain={editLock.onExplain}
                data-testid="optics-use-reduced"
              >
                USE THE REDUCED FOCAL LENGTH
              </ActionButton>
            </div>
          )}
        </Card>
      )}

      {/* -------------------------------------------- measured by the solve */}
      <ListRow
        icon={<NxIcon name="star" size={16} />}
        title="MEASURED BY THE LAST SOLVE"
        sub={
          solved
            ? `solved ${fmtDuration(Math.max(0, nowS - solved.solvedAt))} ago · ${solved.arcsecPerPx.toFixed(2)}″/px`
            : "no plate solve yet - the focal length cannot be measured from the mount's own claim"
        }
        right={
          solved ? (
            <ActionButton
              kind="ghost"
              onPress={() => {
                patch({ focal_length_mm: Math.round(solved.focalMm) });
                setTile("focal");
              }}
              lockedReason={editLock.lockedReason}
              onExplain={editLock.onExplain}
              data-testid="optics-solve-use"
            >
              USE {Math.round(solved.focalMm)} MM
            </ActionButton>
          ) : undefined
        }
        data-testid="optics-solve-row"
      />

      {/* --------------------------------------------------- the rest of it */}
      <Card>
        <Field label="TELESCOPE NAME" hint="Written to the FITS TELESCOP card, which stackers group by. Name the OPTICAL TUBE, not the mount - a wrong string here splits one target across two groups.">
          <TextInput
            value={draft.telescope_name}
            onChange={(v) => patch({ telescope_name: v.slice(0, 64) })}
            placeholder="e.g. Askar FRA400"
            ariaLabel="Telescope name"
            lockedReason={editLock.lockedReason}
            data-testid="optics-telescope"
          />
        </Field>
      </Card>

      <Card>
        <Switch
          checked={draft.auto_from_camera}
          onChange={(v) => patch({ auto_from_camera: v })}
          label="TAKE SENSOR DETAILS FROM THE CAMERA"
          note={
            draft.auto_from_camera
              ? "Pixel size and sensor dimensions come from the connected camera. Right for almost every rig."
              : "Pinned by hand below. Use this when the driver reports the wrong pixel size, or to keep a scale while the camera is unplugged."
          }
          lockedReason={editLock.lockedReason}
          onExplain={editLock.onExplain}
          data-testid="optics-auto-from-camera"
        />
        {!draft.auto_from_camera && (
          <div style={{ display: "grid", gap: 8, gridTemplateColumns: "repeat(3, minmax(0,1fr))", marginTop: 10 }}>
            <Field label="PIXEL SIZE (µm)" hint="0 falls back to the camera.">
              <TextInput
                value={String(draft.pixel_size_um)}
                onChange={(v) => patch({ pixel_size_um: Number(v) || 0 })}
                ariaLabel="Pixel size in micrometres"
                mono
                lockedReason={editLock.lockedReason}
                data-testid="optics-pixel-size"
              />
            </Field>
            <Field label="SENSOR W (px)" hint="0 falls back to the camera.">
              <TextInput
                value={String(draft.sensor_width_px)}
                onChange={(v) => patch({ sensor_width_px: Number(v) || 0 })}
                ariaLabel="Sensor width in pixels"
                mono
                lockedReason={editLock.lockedReason}
              />
            </Field>
            <Field label="SENSOR H (px)" hint="0 falls back to the camera.">
              <TextInput
                value={String(draft.sensor_height_px)}
                onChange={(v) => patch({ sensor_height_px: Number(v) || 0 })}
                ariaLabel="Sensor height in pixels"
                mono
                lockedReason={editLock.lockedReason}
              />
            </Field>
          </div>
        )}
      </Card>

      <Card>
        <Field label="GUIDE SCOPE FOCAL LENGTH (mm)" hint="The GUIDE train, not the imaging one. Without it, guiding RMS is reported in pixels at an assumed 1″/px rather than in real arcseconds. Leave blank if you don't guide.">
          <TextInput
            value={draft.guide_focal_length_mm == null ? "" : String(draft.guide_focal_length_mm)}
            onChange={(v) => {
              const raw = v.trim();
              patch({ guide_focal_length_mm: raw === "" ? null : Number(raw) || null });
            }}
            placeholder="not set"
            ariaLabel="Guide scope focal length in millimetres"
            mono
            lockedReason={editLock.lockedReason}
            data-testid="optics-guide-fl"
          />
        </Field>
      </Card>

      {/* --------------------------------------------------------- solver */}
      <Card data-testid="optics-solver">
        <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "baseline" }}>
          <Label>SOLVER</Label>
          <LayerChip entry={solveEntry} />
        </div>
        <div style={{ marginTop: 6 }}>
          <select
            className="nx-input"
            style={{ maxWidth: 260, width: "100%" }}
            value={solveValue}
            aria-label="Plate-solve provider"
            aria-disabled={backendLock.lockedReason ? true : undefined}
            data-locked={backendLock.lockedReason ? "true" : undefined}
            title={backendLock.lockedReason ?? undefined}
            data-testid="optics-solver-pick"
            onChange={(e) => {
              if (backendLock.lockedReason) {
                backendLock.onExplain(backendLock.lockedReason);
                return;
              }
              void saveSolve(e.target.value);
            }}
          >
            <option value="auto">Auto (best available)</option>
            {solveEligible.map((d) => (
              <option key={d.id} value={d.id}>{d.label}</option>
            ))}
            {solveSticky && (
              // Sticky: a stored value no longer offered stays listed rather
              // than vanishing, because that is exactly when the user most
              // needs to see the literal string the config is carrying.
              <option value={solveValue}>{stickySolverLabel(solveValue)}</option>
            )}
          </select>
        </div>
        <p style={PARA}>
          Which engine works out where a frame points. Auto picks the best available; the rest are
          the drivers this rig has that offer plate solving.
        </p>
        {providers?.solve && (
          <p style={PARA} data-testid="optics-solver-resolved">
            {providers.solve.label}
            {providers.solve.reason ? ` - ${providers.solve.reason}` : ""}
          </p>
        )}
        {solveNote && <p style={PARA} data-testid="optics-solver-note">{solveNote}</p>}
        {isProfileOverride(solveEntry) && solveEntry?.profile_id && (
          <div style={{ marginTop: 6 }}>
            <ActionButton
              kind="ghost"
              busy={busy}
              lockedReason={canBackend ? null : backendLock.lockedReason}
              onExplain={backendLock.onExplain}
              onPress={() => void dropSolvePin()}
              data-testid="optics-solver-unpin"
            >
              CLEAR THE PROFILE PIN
            </ActionButton>
            <Mono size={10.5} tone="dim">
              Clearing it hands this row back to the global setting.
            </Mono>
          </div>
        )}
      </Card>

      {/* ------------------------------------------- plate-solve into the file */}
      <Card data-testid="optics-wcs">
        <Label>PLATE-SOLVE INTO THE FILE</Label>
        <WcsStampEditor />
      </Card>

      {/* ------------------------------------------------------ offline pack */}
      <ListRow
        icon={<NxIcon name="download" size={16} />}
        title="OFFLINE SKY PACK"
        sub={canEdit ? packStatusLabel(pack) : `Offline sky pack: needs ${accessPhrase("config.site_optics")} to read`}
        onPress={() => nav.sheet("skyPack")}
        chevron
        data-testid="optics-pack-row"
      />

      {!canEdit && (
        <Card data-testid="optics-ro-note">
          <Mono size={11} tone="dim">
            Changing optics needs {accessPhrase("config.site_optics")}. The current values are
            shown for reference.
          </Mono>
        </Card>
      )}

      {overridden && canEdit && (
        <p style={PARA} data-testid="optics-save-warning">
          Saving stores these values globally, and profile &quot;{overrideProfile ?? "(unnamed)"}
          &quot; will keep overriding them - the rig will go on using the profile&apos;s optics
          until you drop them above.
        </p>
      )}

      {invalid && (
        <p style={{ ...PARA, color: "var(--bad)" }} data-testid="optics-invalid">
          Focal length must be between 1 and 20000 mm.
        </p>
      )}

      {err && (
        <p style={{ ...PARA, color: "var(--bad)" }} data-testid="optics-error">{err}</p>
      )}
    </Sheet>
  );
}

export default OpticsSheet;
