import { useState } from "react";
import { api } from "../api";
import { useStore } from "../store";
import { Field, Led, Panel } from "../components/ui";
import type { AlpacaServer } from "../types";

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
    const res = await fetch(`http://${manualHost}:${manualPort}/management/v1/configureddevices`);
    const body = await res.json();
    setServers([{ address: manualHost, port: Number(manualPort), devices: body.Value ?? [] }]);
  });

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

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Panel title="Equipment Rig">
        <div className="flex flex-col gap-2.5">
          {ROLES.concat("guide_camera").map((role) => {
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
    </div>
  );
}
