import { useState } from "react";
import { api } from "../api";
import { useStore } from "../store";
import { Field, Led, Panel } from "../components/ui";
import type { AlpacaServer, NinaInstance } from "../types";

const ROLES = ["camera", "telescope", "focuser", "filterwheel", "switch"];

export default function ConnectView() {
  const status = useStore((s) => s.status);
  const showToast = useStore((s) => s.showToast);
  const [servers, setServers] = useState<AlpacaServer[] | null>(null);
  const [scanning, setScanning] = useState(false);
  const [busy, setBusy] = useState(false);
  const [phd2Host, setPhd2Host] = useState("127.0.0.1");
  const [manualHost, setManualHost] = useState("");
  const [manualPort, setManualPort] = useState("11111");
  const [ninaHost, setNinaHost] = useState("127.0.0.1");
  const [ninaPort, setNinaPort] = useState("1888");
  const [ninaFound, setNinaFound] = useState<NinaInstance[] | null>(null);
  const [ninaScanning, setNinaScanning] = useState(false);

  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    try { await fn(); } catch (e) { showToast("error", String((e as Error).message)); }
    setBusy(false);
  };

  const scan = async () => {
    setScanning(true);
    try {
      setServers(await api.get<AlpacaServer[]>("/api/discover"));
    } catch (e) { showToast("error", String((e as Error).message)); }
    setScanning(false);
  };

  const scanManual = () => run(async () => {
    // Route through the backend proxy (fixes browser CORS) — the server returns
    // the AlpacaServer shape directly and a differentiated error on failure
    // (unreachable vs. reached-but-not-Alpaca vs. timeout), surfaced via run().
    const port = Number(manualPort) || 11111;
    const srv = await api.get<AlpacaServer>(
      `/api/discover/alpaca?host=${encodeURIComponent(manualHost)}&port=${port}`);
    setServers([srv]);
  });

  const scanNina = async () => {
    setNinaScanning(true);
    try {
      const q = ninaHost ? `?host=${encodeURIComponent(ninaHost)}&port=${Number(ninaPort) || 1888}` : "";
      setNinaFound(await api.get<NinaInstance[]>(`/api/discover/nina${q}`));
    } catch (e) { showToast("error", String((e as Error).message)); }
    setNinaScanning(false);
  };

  const connectDevice = (srv: AlpacaServer, d: AlpacaServer["devices"][0]) => {
    const role = d.DeviceType.toLowerCase();
    if (!ROLES.includes(role)) {
      showToast("warning", `${d.DeviceType} is not a supported role yet`);
      return;
    }
    return run(() => api.post("/api/connect/alpaca", {
      role, host: srv.address, port: srv.port,
      dev_type: role, dev_num: d.DeviceNumber, name: d.DeviceName,
    }));
  };

  const devices = status?.connected ?? {};
  const mode = status?.mode ?? "none";
  const MODE_LABEL: Record<string, string> = {
    none: "offline", sim: "simulator", alpaca: "alpaca", nina: "nina bridge",
  };

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Panel title="Equipment Rig"
        right={
          <span className={`label ${mode === "none" ? "" : "text-accent"}`}>
            {MODE_LABEL[mode]}
          </span>
        }>
        <div className="flex flex-col gap-2.5">
          {ROLES.map((role) => {
            const d = devices[role];
            return (
              <div key={role} className="flex items-center gap-3 border border-line bg-bg/60 px-3 py-2.5">
                <Led on={!!d?.connected} />
                <span className="label w-24 shrink-0">{role.replace("_", " ")}</span>
                <span className="mono text-xs truncate flex-1 text-ink/90">
                  {d?.connected ? d.name : <span className="text-dim">— not connected —</span>}
                </span>
              </div>
            );
          })}
          {/* Guide camera: vendor-neutral. A dedicated guide_camera device only
              exists in sim; in NINA/Alpaca/PHD2 mode the guiding device IS the
              guider (PHD2 driving e.g. an ASI220). Treat "guiding present" as the
              backend-derived status.guide_camera OR a connected status.guider, so
              we never show "no guide camera" when guiding is actually wired. */}
          {(() => {
            const gc = status?.guide_camera;
            const guider = status?.guider;
            const connected = !!gc?.connected || !!guider;
            const name = gc?.name ?? guider?.name ?? "guide camera";
            return (
              <div className="flex items-center gap-3 border border-line bg-bg/60 px-3 py-2.5">
                <Led on={connected} />
                <span className="label w-24 shrink-0">guide cam</span>
                <span className="mono text-xs truncate flex-1 text-ink/90">
                  {connected ? name : <span className="text-dim">— not connected —</span>}
                </span>
              </div>
            );
          })()}
          <div className="flex items-center gap-3 border border-line bg-bg/60 px-3 py-2.5">
            <Led on={!!status?.guider} />
            <span className="label w-24 shrink-0">guider</span>
            <span className="mono text-xs flex-1 text-ink/90">
              {status?.guider ? status.guider.name ?? "guider" : <span className="text-dim">— not connected —</span>}
            </span>
          </div>
        </div>

        <div className="flex flex-wrap gap-2 mt-4">
          <button className="btn btn-accent" disabled={busy}
            onClick={() => run(() => api.post("/api/connect/sim"))}>
            ▶ Connect Simulator Rig
          </button>
          <button className="btn btn-danger" disabled={busy}
            onClick={() => run(() => api.post("/api/disconnect"))}>
            Disconnect All
          </button>
        </div>
      </Panel>

      <Panel title="Alpaca Network Discovery"
        right={
          <button className="btn !py-1" onClick={scan} disabled={scanning}>
            {scanning ? "Scanning…" : "⟳ Scan"}
          </button>
        }>
        <p className="text-xs text-dim mb-3 leading-relaxed">
          Finds ASCOM Alpaca devices on your network — ZWO, Pegasus Astro, QHY
          and any vendor with Alpaca or ASCOM Remote. Click a device to assign it.
        </p>
        {servers === null && <p className="text-dim text-xs">no scan yet</p>}
        {servers?.length === 0 && <p className="text-warn text-xs">no Alpaca servers found</p>}
        {servers?.map((srv) => (
          <div key={`${srv.address}:${srv.port}`} className="mb-3">
            <h3 className="mono text-xs text-accent mb-1.5">{srv.address}:{srv.port}</h3>
            <div className="flex flex-col gap-1.5">
              {srv.devices.map((d, i) => (
                <button key={i} disabled={busy}
                  onClick={() => connectDevice(srv, d)}
                  className="btn !normal-case !tracking-normal !font-sans text-left flex justify-between items-center">
                  <span>{d.DeviceName}</span>
                  <span className="label">{d.DeviceType} #{d.DeviceNumber}</span>
                </button>
              ))}
              {srv.devices.length === 0 && <p className="text-dim text-xs">server has no configured devices</p>}
            </div>
          </div>
        ))}

        <div className="grid grid-cols-[1fr_90px_auto] gap-2 mt-4 items-end">
          <Field label="Alpaca host (manual)">
            <input className="field" placeholder="192.168.1.50" value={manualHost}
              onChange={(e) => setManualHost(e.target.value)} />
          </Field>
          <Field label="Port">
            <input className="field" value={manualPort}
              onChange={(e) => setManualPort(e.target.value)} />
          </Field>
          <button className="btn" disabled={!manualHost || busy} onClick={scanManual}>Query</button>
        </div>

        <div className="grid grid-cols-[1fr_auto] gap-2 mt-4 items-end border-t border-line pt-4">
          <Field label="PHD2 guiding host">
            <input className="field" value={phd2Host} onChange={(e) => setPhd2Host(e.target.value)} />
          </Field>
          <button className="btn" disabled={busy}
            onClick={() => run(() => api.post("/api/connect/phd2", { host: phd2Host, port: 4400 }))}>
            Connect PHD2
          </button>
        </div>
      </Panel>

      <Panel title="NINA Bridge — Transition Mode" className="lg:col-span-2"
        right={
          <div className="flex items-center gap-3">
            {mode === "nina" && <span className="label text-accent">● bridged</span>}
            <button className="btn !py-1" onClick={scanNina} disabled={ninaScanning}>
              {ninaScanning ? "Scanning…" : "⟳ Scan Network"}
            </button>
          </div>
        }>
        <p className="text-xs text-dim mb-3 leading-relaxed max-w-3xl">
          Already running <span className="text-ink">NINA</span> on the machine at your scope?
          Point AstroDeck at NINA's <span className="text-ink">Advanced API</span> plugin and fly your
          existing rig from here — no driver reconfiguration. AstroDeck delegates capture, autofocus,
          plate-solving and guiding to NINA's own routines, then you can migrate to direct Alpaca
          device by device. (Enable the Advanced API plugin in NINA; default port 1888.)
        </p>

        {ninaFound !== null && (
          <div className="mb-4">
            {ninaFound.length === 0 && (
              <p className="text-warn text-xs">no NINA instances found on this network</p>
            )}
            <div className="flex flex-col gap-1.5">
              {ninaFound.map((inst) => (
                <div key={inst.url}
                  className="flex items-center gap-3 border border-line bg-bg/60 px-3 py-2.5">
                  <Led on />
                  <div className="min-w-0 flex-1">
                    <div className="mono text-xs text-ink truncate">
                      {inst.hostname ?? inst.host}
                      <span className="text-dim"> :{inst.port}</span>
                      {inst.nina_version && <span className="text-dim"> · NINA {inst.nina_version}</span>}
                    </div>
                    <div className="text-[10px] text-dim truncate">
                      {Object.keys(inst.devices).length
                        ? Object.entries(inst.devices).map(([r, n]) => `${r}: ${n}`).join("  ·  ")
                        : "no equipment connected in NINA"}
                    </div>
                  </div>
                  <button className="btn btn-accent !py-1" disabled={busy}
                    onClick={() => run(() => api.post("/api/connect/nina",
                      { host: inst.host, port: inst.port }))}>
                    ◈ Bridge
                  </button>
                </div>
              ))}
            </div>
          </div>
        )}

        <div className="grid grid-cols-[1fr_110px_auto] gap-2 items-end max-w-xl">
          <Field label="NINA host (or hint for scan)">
            <input className="field" value={ninaHost} onChange={(e) => setNinaHost(e.target.value)} />
          </Field>
          <Field label="API port">
            <input className="field" value={ninaPort} onChange={(e) => setNinaPort(e.target.value)} />
          </Field>
          <button className="btn btn-accent" disabled={busy || !ninaHost}
            onClick={() => run(() => api.post("/api/connect/nina",
              { host: ninaHost, port: Number(ninaPort) || 1888 }))}>
            ◈ Bridge to NINA
          </button>
        </div>
      </Panel>
    </div>
  );
}
