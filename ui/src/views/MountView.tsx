import { useEffect, useState } from "react";
import { api } from "../api";
import { useStore, useStatus } from "../store";
import { Panel, Stat, Toggle } from "../components/ui";
import type { CatalogEntry } from "../types";

const RATES = [0.05, 0.5, 2.0];

export default function MountView() {
  const status = useStatus();
  const showToast = useStore((s) => s.showToast);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<CatalogEntry[]>([]);
  const [rateIdx, setRateIdx] = useState(1);
  const [center, setCenter] = useState(true);

  const m = status?.mount;

  const act = async (fn: () => Promise<unknown>) => {
    try { await fn(); } catch (e) { showToast("error", (e as Error).message); }
  };

  useEffect(() => {
    const t = setTimeout(async () => {
      try { setResults(await api.get<CatalogEntry[]>(`/api/catalog?q=${encodeURIComponent(query)}`)); }
      catch { /* server not up yet */ }
    }, 250);
    return () => clearTimeout(t);
  }, [query]);

  const nudge = (axis: "ra" | "dec", dir: number) => ({
    onPointerDown: () => act(() => api.post("/api/mount/move", { axis, rate_deg_s: dir * RATES[rateIdx] })),
    onPointerUp: () => act(() => api.post("/api/mount/move", { axis, rate_deg_s: 0 })),
    onPointerLeave: () => act(() => api.post("/api/mount/move", { axis, rate_deg_s: 0 })),
  });

  return (
    <div className="grid gap-4 lg:grid-cols-[340px_1fr]">
      <div className="flex flex-col gap-4">
        <Panel title="Pointing">
          <div className="grid grid-cols-2 gap-x-4 gap-y-3">
            <Stat label="RA" value={m?.ra_str ?? "—"} />
            <Stat label="Dec" value={m?.dec_str ?? "—"} />
            <Stat label="Altitude" value={m ? `${m.alt}°` : "—"}
              tone={m && m.alt < 20 ? "warn" : undefined} />
            <Stat label="Azimuth" value={m ? `${m.az}°` : "—"} />
            <Stat label="State"
              value={m ? (m.parked ? "PARKED" : m.slewing ? "SLEWING" : m.tracking ? "TRACKING" : "IDLE") : "—"}
              tone={m?.parked ? undefined : m?.slewing ? "warn" : m?.tracking ? "good" : undefined} />
          </div>
          <div className="flex items-center gap-3 mt-4 border-t border-line pt-3">
            <Toggle checked={!!m?.tracking} disabled={!m}
              onChange={(v) => act(() => api.post(`/api/mount/tracking?on=${v}`))} />
            <span className="text-xs text-dim">sidereal tracking</span>
            <div className="flex-1" />
            {m?.parked ? (
              <button className="btn" onClick={() => act(() => api.post("/api/mount/unpark"))}>Unpark</button>
            ) : (
              <button className="btn" onClick={() => act(() => api.post("/api/mount/park"))}>Park</button>
            )}
          </div>
        </Panel>

        <Panel title="Slew Pad">
          <div className="grid grid-cols-3 gap-2 w-44 mx-auto select-none">
            <span />
            <button className="btn !text-base" {...nudge("dec", 1)}>▲</button>
            <span />
            <button className="btn !text-base" {...nudge("ra", -1)}>◀</button>
            <button className="btn btn-danger !text-[10px]"
              onClick={() => act(() => api.post("/api/mount/stop"))}>STOP</button>
            <button className="btn !text-base" {...nudge("ra", 1)}>▶</button>
            <span />
            <button className="btn !text-base" {...nudge("dec", -1)}>▼</button>
            <span />
          </div>
          <div className="flex justify-center gap-1.5 mt-3">
            {["slow", "med", "fast"].map((r, i) => (
              <button key={r} className={`btn !py-1 !px-3 !text-[10px] ${i === rateIdx ? "btn-accent" : ""}`}
                onClick={() => setRateIdx(i)}>{r}</button>
            ))}
          </div>
          <div className="flex items-center justify-center gap-2 mt-4 border-t border-line pt-3">
            <button className="btn" onClick={() => act(() => api.post("/api/mount/solve_sync"))}>
              ✛ Solve & Sync
            </button>
          </div>
          <p className="text-[10px] text-dim text-center mt-2">
            plate-solves current frame, syncs mount model
          </p>
        </Panel>
      </div>

      <Panel title="Target Catalog"
        right={
          <label className="flex items-center gap-2">
            <span className="label">center after slew</span>
            <Toggle checked={center} onChange={setCenter} />
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
                  <td className={`mono pr-3 ${r.alt < 20 ? "text-warn" : r.alt > 40 ? "text-good" : ""}`}>
                    {r.alt.toFixed(0)}°
                  </td>
                  <td className="text-right">
                    <button className="btn !py-1 !px-3 !text-[10px]" disabled={!m}
                      onClick={() => act(() => api.post("/api/mount/goto", {
                        ra_hours: r.ra_hours, dec_deg: r.dec_deg, center,
                      }))}>
                      GOTO
                    </button>
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
