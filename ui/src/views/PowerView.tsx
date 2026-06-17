import { useEffect, useState } from "react";
import { api } from "../api";
import { useStore, useStatus } from "../store";
import { Led, Panel } from "../components/ui";
import { useCanControlPower } from "../lib/caps";
import ReadOnlyBadge from "../components/ReadOnlyBadge";
import type { SwitchPort } from "../types";

export default function PowerView() {
  const status = useStatus();
  const showToast = useStore((s) => s.showToast);
  const canControl = useCanControlPower(); // viewer => read-only ports
  const [ports, setPorts] = useState<SwitchPort[] | null>(null);
  const connected = !!status?.connected?.switch?.connected;

  const refresh = async () => {
    try { setPorts(await api.get<SwitchPort[]>("/api/switch/ports")); }
    catch { setPorts(null); }
  };

  useEffect(() => {
    if (!connected) { setPorts(null); return; }
    refresh();
    const t = setInterval(refresh, 5000);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [connected]);

  const setPort = async (id: number, value: number) => {
    if (!canControl) return; // viewer: read-only, controls are inert anyway
    try { setPorts(await api.post<SwitchPort[]>("/api/switch/set", { port_id: id, value })); }
    catch (e) { showToast("error", (e as Error).message); }
  };

  if (!connected) {
    return (
      <Panel title="Power & Dew Control">
        <p className="text-dim text-xs py-8 text-center tracking-widest uppercase">
          no power box connected — connect a switch device (Pegasus UPB, etc.) on the Rig page
        </p>
      </Panel>
    );
  }

  const toggles = ports?.filter((p) => p.can_write && p.is_boolean) ?? [];
  const dimmers = ports?.filter((p) => p.can_write && !p.is_boolean) ?? [];
  const sensors = ports?.filter((p) => !p.can_write) ?? [];

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Panel title="Power Outputs" right={!canControl && <ReadOnlyBadge />}>
        <div className="flex flex-col gap-2">
          {toggles.map((p) => (
            <button key={p.id}
              disabled={!canControl}
              aria-disabled={!canControl || undefined}
              onClick={() => setPort(p.id, p.value > 0 ? 0 : 1)}
              className={`flex items-center gap-3 border px-3 py-3 transition-all text-left
                ${canControl ? "cursor-pointer" : "cursor-default opacity-60"}
                ${p.value > 0 ? "border-accent2 bg-accent/5" : "border-line bg-bg/60 " + (canControl ? "hover:border-line2" : "")}`}>
              <Led on={p.value > 0} />
              <span className="text-sm flex-1">{p.name}</span>
              <span className={`label ${p.value > 0 ? "text-good" : ""}`}>
                {p.value > 0 ? "ON" : "OFF"}
              </span>
            </button>
          ))}
        </div>
      </Panel>

      <div className="flex flex-col gap-4">
        <Panel title="Dew Heaters · PWM" right={!canControl && <ReadOnlyBadge />}>
          <div className="flex flex-col gap-4">
            {dimmers.map((p) => (
              <div key={p.id}>
                <div className="flex justify-between mb-1.5">
                  <span className="text-xs">{p.name}</span>
                  <span className="mono text-xs text-accent">{p.value.toFixed(0)}{p.unit}</span>
                </div>
                <input type="range" min={p.min} max={p.max} value={p.value}
                  disabled={!canControl}
                  className={`w-full accent-(--accent) ${canControl ? "cursor-pointer" : "opacity-50 cursor-default"}`}
                  onChange={(e) => setPort(p.id, Number(e.target.value))} />
              </div>
            ))}
            {dimmers.length === 0 && <p className="text-dim text-xs">no PWM ports</p>}
          </div>
        </Panel>

        <Panel title="Telemetry">
          <div className="grid grid-cols-2 gap-3">
            {sensors.map((p) => (
              <div key={p.id} className="border border-line bg-bg/60 px-3 py-2.5">
                <div className="label mb-1">{p.name}</div>
                <div className="mono text-lg text-accent">
                  {p.value.toFixed(1)}<span className="text-xs text-dim ml-1">{p.unit}</span>
                </div>
              </div>
            ))}
          </div>
        </Panel>
      </div>
    </div>
  );
}
