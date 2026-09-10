// coords.tsx - the COORDINATES sheet (T-SKY-4, plan A.16). A manual RA/Dec
// entry point for a target the catalogue does not carry - the one answer
// that can never be missing (`server/astrodeck/catalog/objects.py`'s own
// `parse_coordinates` docstring). Nothing here re-implements that parser:
// both fields travel to the server as one query string and the server's
// answer - a row shaped exactly like a catalogue hit, or its own notes[] on
// a miss - is rendered verbatim.
import { useEffect, useState, type JSX } from "react";
import { nav } from "../../../router";
import type { SheetProps } from "../../sheets";
import { ActionButton, Field, Mono, Sheet, TextInput } from "../../../ui";
import { useLock } from "../../../lib/gateHook";
import { explainLock } from "../../../shell/explain";
import { raDecFromAltAz, raHmsStr, decDmsStr } from "../finder/equatorial";
import { usePlanning } from "../../../lib/planning";
import { useMount, useStore } from "../../../../store";
import { api } from "../../../../api";
import type { CatalogEntry } from "../../../../types";

const DEBOUNCE_MS = 300;

export function CoordsSheet({ params }: SheetProps): JSX.Element {
  const enqueueToast = useStore((s) => s.enqueueToast);
  const mount = useMount();

  const [ra, setRa] = useState("");
  const [dec, setDec] = useState("");
  const [resolved, setResolved] = useState<CatalogEntry | null>(null);
  const [notes, setNotes] = useState<string[]>([]);
  const [checking, setChecking] = useState(false);

  // -------------------------------------------------------------- read-back
  useEffect(() => {
    const raQ = ra.trim();
    const decQ = dec.trim();
    if (!raQ || !decQ) { setResolved(null); setNotes([]); setChecking(false); return; }
    setChecking(true);
    let dead = false;
    const timer = setTimeout(() => {
      api.get<{ results: CatalogEntry[]; notes?: string[] }>(
        `/api/catalog?q=${encodeURIComponent(`${raQ} ${decQ}`)}&explain=1`,
      ).then((r) => {
        if (dead) return;
        setResolved(r.results?.[0] ?? null);
        setNotes(r.notes ?? []);
      }).catch(() => {
        if (dead) return;
        setResolved(null);
        setNotes([]);
      }).finally(() => { if (!dead) setChecking(false); });
    }, DEBOUNCE_MS);
    return () => { dead = true; clearTimeout(timer); };
  }, [ra, dec]);

  const readbackLine = (): string => {
    if (checking) return "checking…";
    if (!ra.trim() || !dec.trim()) return "";
    if (resolved) {
      const bits = ["Typed position"];
      if (typeof resolved.alt === "number") bits.push(`alt ${Math.round(resolved.alt)}°`);
      if (typeof resolved.az === "number") bits.push(`az ${Math.round(resolved.az)}°`);
      return bits.join(" · ");
    }
    if (notes.length > 0) return notes.join(" ");
    return "That is not a position this server can read - try 22h 57m 54s +62° 37′ 06″.";
  };

  // -------------------------------------------------------------- USE FINDER
  // The reticle's live az/alt lives only inside the finder's own model
  // (F: "not persisted"), so the one honest way a SEPARATE sheet mount can
  // carry it here is through the route that opened this sheet - mirroring
  // the horizon editor's `params.az` convention (A.15). If the call site
  // that opens #/sky/coords is not yet passing az/alt (T-SKY-2/3's job),
  // USE FINDER renders honest-locked rather than silently doing nothing.
  const finderAz = params.az != null && Number.isFinite(Number(params.az)) ? Number(params.az) : null;
  const finderAlt = params.alt != null && Number.isFinite(Number(params.alt)) ? Number(params.alt) : null;
  const finderLocked = finderAz == null || finderAlt == null
    ? "Open coordinates from the finder to carry its aim here."
    : null;

  const useFinder = () => {
    if (finderLocked || finderAz == null || finderAlt == null) {
      enqueueToast({ level: "warning", title: finderLocked ?? "No finder position available." });
      return;
    }
    const site = useStore.getState().site;
    if (!site || typeof site.latitude !== "number" || typeof site.longitude !== "number") {
      enqueueToast({ level: "warning", title: "No site set - open Settings to set one first." });
      return;
    }
    const { ra_hours, dec_deg } = raDecFromAltAz(finderAlt, finderAz, site.latitude, site.longitude, Date.now() / 1000);
    setRa(raHmsStr(ra_hours));
    setDec(decDmsStr(dec_deg));
    if (finderAlt < 0) enqueueToast({ level: "warning", title: "The finder is aimed below the horizon." });
  };

  // --------------------------------------------------------------- USE MOUNT
  const { lockedReason: mountLocked, onExplain: explainMount } = useLock({ needsRole: "telescope" });
  const useMountPosition = () => {
    if (mountLocked) { explainMount(mountLocked); return; }
    if (!mount) { enqueueToast({ level: "warning", title: "No mount position reported yet." }); return; }
    setRa(raHmsStr(mount.ra_hours));
    setDec(decDmsStr(mount.dec_deg));
  };

  // ------------------------------------------------------- IMAGE THIS POSITION
  const { lockedReason: captureLocked, onExplain: explainCapture } = useLock({
    cap: "control.capture", needsRole: "camera",
  });
  const imageThisPosition = () => {
    if (captureLocked) { explainCapture(captureLocked); return; }
    if (!resolved) {
      enqueueToast({ level: "warning", title: "Type a position the server recognises first." });
      return;
    }
    // B.2 trap 2: a coordinates row's `name` is the literal "Typed position";
    // `id` ("22.9650h +62.617°") is the one that actually names THIS spot.
    nav.go(
      `/sky/quick?ra=${resolved.ra_hours}&dec=${resolved.dec_deg}&name=${encodeURIComponent(resolved.id)}`,
    );
  };

  // ------------------------------------------------------------------ + PLAN
  //
  // The pool is the RIG's shortlist now (D-FU-1), not this phone's, so a typed
  // position added here is on the list a tablet opens too. Writing it needs
  // `control.capture`; on an engine with no planning block `lockedReason` is
  // null and the phone remembers, exactly as it did before.
  const { pool, putPool, lockedReason: poolLocked } = usePlanning();
  const addToPlan = () => {
    if (poolLocked) { explainLock(poolLocked); return; }
    if (!resolved) {
      enqueueToast({ level: "warning", title: "Type a position the server recognises first." });
      return;
    }
    if (!pool.includes(resolved.id)) putPool([...pool, resolved.id]);
    enqueueToast({ level: "success", title: `Added ${resolved.id} to tonight's pool.` });
  };

  return (
    <Sheet
      title="COORDINATES"
      sub="J2000 · kept as typed in the flow and the report"
      backLabel="SKY"
      onBack={() => nav.back()}
      data-testid="coords-sheet"
      footer={
        <div style={{ display: "flex", gap: 8, width: "100%" }}>
          <ActionButton kind="primary" size="lg" full lockedReason={captureLocked} onExplain={explainCapture}
            onPress={imageThisPosition} data-testid="image-this-position">
            IMAGE THIS POSITION
          </ActionButton>
          <ActionButton kind="secondary" size="lg" onPress={addToPlan} data-testid="plus-plan"
            lockedReason={poolLocked} onExplain={explainLock}>
            + PLAN
          </ActionButton>
        </div>
      }
    >
      <Field label="RA" htmlFor="coords-ra">
        <TextInput id="coords-ra" mono ariaLabel="Right ascension" value={ra}
          onChange={setRa} placeholder="22h 57m 54s" data-testid="coords-ra-input" />
      </Field>
      <Field label="Dec" htmlFor="coords-dec">
        <TextInput id="coords-dec" mono ariaLabel="Declination" value={dec}
          onChange={setDec} placeholder="+62° 37′ 06″" data-testid="coords-dec-input" />
      </Field>

      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <ActionButton kind="ghost" size="md" lockedReason={finderLocked}
          onExplain={(r) => enqueueToast({ level: "warning", title: r })}
          onPress={useFinder} data-testid="use-finder">
          USE FINDER
        </ActionButton>
        <ActionButton kind="ghost" size="md" lockedReason={mountLocked} onExplain={explainMount}
          onPress={useMountPosition} data-testid="use-mount">
          USE MOUNT
        </ActionButton>
      </div>

      <div data-testid="coords-readback">
        <Mono size={11}>{readbackLine()}</Mono>
      </div>
    </Sheet>
  );
}
