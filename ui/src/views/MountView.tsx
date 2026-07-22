import { useEffect, useState } from "react";
import { api } from "../api";
import { useStore, useStatus } from "../store";
import { Panel, Stat, Toggle, IconButton, SegmentedControl } from "../components/ui";
import { confirmDialog } from "../components/ConfirmDialog";
import SlewPad from "../components/SlewPad";
import { Icon } from "../components/icons";
import { useCanControlMount } from "../lib/caps";
import ReadOnlyBadge from "../components/ReadOnlyBadge";
import type { CatalogEntry, PreflightAlt } from "../types";

/** Severity glyph for an altitude cell — shape, not colour-only (spec §5 / critique3 #7). */
function AltGlyph({ alt }: { alt: number }) {
  if (alt < 0) return <span className="text-bad" aria-label="below horizon" title="below the visible horizon">⚠</span>;
  if (alt < 20) return <span className="text-warn" aria-label="low on the horizon" title="low on the horizon">↓</span>;
  return null;
}

const TRACKING_RATE_OPTIONS: { value: "sidereal" | "lunar" | "solar"; label: string }[] = [
  { value: "sidereal", label: "Sidereal" },
  { value: "lunar", label: "Lunar" },
  { value: "solar", label: "Solar" },
];

export default function MountView() {
  const status = useStatus();
  const showToast = useStore((s) => s.showToast);
  const openFraming = useStore((s) => s.openFraming);
  const canMount = useCanControlMount(); // viewer => pointing visible, controls read-only
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<CatalogEntry[]>([]);
  const [center, setCenter] = useState(true);

  const m = status?.mount;

  // UX-16: in-flight guard — disables mutating controls during the POST round-trip
  // so a slow action (esp. Solve & Sync) can't be double-fired and doesn't read dead.
  const [busy, setBusy] = useState(false);
  const act = async (fn: () => Promise<unknown>) => {
    if (busy) return;
    setBusy(true);
    try { await fn(); } catch (e) { showToast("error", (e as Error).message); }
    finally { setBusy(false); }
  };

  useEffect(() => {
    const t = setTimeout(async () => {
      try { setResults(await api.get<CatalogEntry[]>(`/api/catalog?q=${encodeURIComponent(query)}`)); }
      catch { /* server not up yet */ }
    }, 250);
    return () => clearTimeout(t);
  }, [query]);

  // GOTO re-queries LIVE altitude at the tap (never trusts the stale catalog row,
  // critique2 #6) and guards by verdict (spec §5):
  //   below  -> single-OK dialog, NO slew, NO hold (critique3 #12)
  //   low    -> OK/Cancel "slew anyway", threads force=true so the server guard
  //             doesn't re-block an accepted low slew
  //   unknown-> default site / fetch issue: OK/Cancel "slew anyway"
  const doGoto = async (r: CatalogEntry) => {
    if (!canMount) return; // viewer: GOTO is read-only (buttons are disabled too)
    let pf: PreflightAlt | null = null;
    try {
      pf = await api.get<PreflightAlt>(
        `/api/sequence/preflight?ra_hours=${r.ra_hours}&dec_deg=${r.dec_deg}`,
      );
    } catch {
      // preflight fetch failed — treat as unknown (the server horizon guard is the net)
      pf = null;
    }

    if (!pf || pf.verdict === "unknown") {
      const ok = await confirmDialog({
        title: "Location not set",
        body: "Altitude can't be checked until you set your location in Settings. Slew anyway?",
        tone: "warn",
        mode: "confirm",
        confirmLabel: "Slew anyway",
      });
      if (!ok) return;
    } else if (pf.verdict === "below") {
      await confirmDialog({
        title: "Below the visible horizon",
        body: pf.alt != null
          ? `${r.id} is at ${pf.alt}° — below the horizon, so it isn't visible now.`
          : `${r.id} is below the horizon, so it isn't visible now.`,
        tone: "danger",
        mode: "ok", // single dismiss, no slew, NO hold
      });
      return;
    } else if (pf.verdict === "low") {
      const ok = await confirmDialog({
        title: "Low on the horizon",
        body: pf.alt != null
          ? `${r.id} is only ${pf.alt}° up — expect heavy atmosphere and possible obstructions. Slew anyway?`
          : `${r.id} is low on the horizon — expect heavy atmosphere and possible obstructions. Slew anyway?`,
        tone: "warn",
        mode: "confirm",
        confirmLabel: "Slew anyway",
      });
      if (!ok) return;
    }

    await act(() => api.post("/api/mount/goto", {
      ra_hours: r.ra_hours, dec_deg: r.dec_deg, center,
      force: pf?.verdict === "low",
    }));
  };

  return (
    <div className="grid gap-4 md:grid-cols-[minmax(290px,340px)_1fr]">
      <div className="flex flex-col gap-4">
        <Panel title="Pointing" right={!canMount && <ReadOnlyBadge />}>
          <div className="grid grid-cols-2 gap-x-4 gap-y-3">
            <Stat label="RA (J2000)" value={m?.ra_str ?? "—"} />
            <Stat label="Dec (J2000)" value={m?.dec_str ?? "—"} />
            <Stat label="Altitude" value={m ? `${m.alt}°` : "—"}
              tone={m && m.alt < 20 ? "warn" : undefined} />
            <Stat label="Azimuth" value={m ? `${m.az}°` : "—"} />
            <Stat label="State"
              value={m ? (m.parked ? "PARKED" : m.slewing ? "SLEWING" : m.tracking ? "TRACKING" : "IDLE") : "—"}
              tone={m?.parked ? undefined : m?.slewing ? "warn" : m?.tracking ? "good" : undefined} />
          </div>
          <div className="mt-4 border-t border-line pt-3 flex flex-col gap-3">
            <div className="flex items-center gap-3">
              <Toggle checked={!!m?.tracking} disabled={!canMount || !m}
                onChange={(v) => act(() => api.post(`/api/mount/tracking?on=${v}`))} label="Tracking" />
              <span className="label">tracking</span>
              <div className="flex-1" />
              {m?.parked ? (
                <button className="btn tap min-h-[44px]" disabled={!canMount} onClick={() => act(() => api.post("/api/mount/unpark"))}>Unpark</button>
              ) : (
                <button className="btn tap min-h-[44px]" disabled={!canMount} onClick={() => act(() => api.post("/api/mount/park"))}>Park</button>
              )}
            </div>
            {/* Tracking rate (2026-07-21): only mounts that support it advertise
                can_set_tracking_rate; disabled for viewers like every motion control. */}
            {m?.can_set_tracking_rate && (
              <div className="flex items-center gap-3">
                <span className="label shrink-0">rate</span>
                <div className="flex-1" />
                <SegmentedControl<"sidereal" | "lunar" | "solar">
                  options={TRACKING_RATE_OPTIONS}
                  value={m?.tracking_rate}
                  disabled={!canMount || !m}
                  ariaLabel="Tracking rate"
                  onChange={(v) => act(() => api.post(`/api/mount/tracking_rate?rate=${v}`))}
                />
              </div>
            )}
          </div>
        </Panel>

        <Panel title="Slew Pad" right={!canMount && <ReadOnlyBadge />}>
          {/* 3B predictable fixed-rate slew + tap-to-pulse pad (replaces the old
              slow/med/fast 3-chip pad). Owns rate selector, STOP bar, reverse
              toggles, alt-guard, NINA mode. SlewPad itself hard-guards on the cap
              (it can't post moves for a viewer); the disabled inputs here are the
              visible read-only affordance. */}
          <SlewPad />
          <div className="flex items-center justify-center gap-2 mt-4 border-t border-line pt-3">
            <button className="btn tap min-h-[44px]" disabled={!canMount || busy} onClick={() => act(() => api.post("/api/mount/solve_sync"))}>
              {busy ? "Solving…" : <><Icon name="align" size={14} className="inline -mt-0.5 mr-1" />Solve &amp; Sync</>}
            </button>
          </div>
          <p className="text-[12px] text-dim text-center mt-2">
            plate-solves current frame, syncs mount model
          </p>
        </Panel>
      </div>

      <Panel title="Target Catalog"
        right={
          <label className="flex items-center gap-2">
            <span className="label">center after slew</span>
            <Toggle checked={center} onChange={setCenter} label="Center after slew" />
          </label>
        }>
        <input className="field mb-3" placeholder="Search — M42, Andromeda, nebula, galaxy…"
          value={query} onChange={(e) => setQuery(e.target.value)} />
        <div className="overflow-y-auto max-h-[58vh] -mx-1 px-1">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-left">
                {["ID", "Name", "Type", "Mag", "Alt", ""].map((h) => (
                  <th key={h} className="label pb-2 pr-3 font-medium">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {results.map((r) => (
                <tr key={r.id} className="border-t border-line/60 hover:bg-raise/80 transition-colors">
                  <td className="mono py-2 pr-3 text-accent whitespace-nowrap">{r.id}</td>
                  <td className="pr-3">{r.name}</td>
                  <td className="pr-3 text-dim">{r.type}</td>
                  <td className="mono pr-3">{r.mag.toFixed(1)}</td>
                  <td className={`mono pr-3 whitespace-nowrap ${r.alt < 20 ? "text-warn" : r.alt > 40 ? "text-good" : ""}`}>
                    <span className="inline-flex items-center gap-1">
                      {r.alt.toFixed(0)}°<AltGlyph alt={r.alt} />
                    </span>
                  </td>
                  <td className="text-right">
                    <div className="inline-flex items-center gap-1.5 justify-end">
                      {/* Frame this object in the Atlas (telescope/frame glyph, NOT
                          the Align icon — spec §6 / C3-A10). Works offline; no mount
                          needed, so it is never disabled. */}
                      <IconButton
                        icon="frame"
                        label={`Frame ${r.id} in the Sky Atlas`}
                        onClick={() => openFraming(r)}
                      />
                      <button className="btn tap min-h-[44px] !px-3" disabled={!canMount || !m}
                        onClick={() => doGoto(r)}>
                        GOTO
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {results.length === 0 && <p className="text-dim text-xs py-4">no matches</p>}
        </div>
      </Panel>
    </div>
  );
}
