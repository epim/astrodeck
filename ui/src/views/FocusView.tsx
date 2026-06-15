import { useState } from "react";
import { api } from "../api";
import { useStore, useStatus, useFocus } from "../store";
import { VCurve } from "../components/graphs";
import { Field, Panel, Stat } from "../components/ui";

export default function FocusView() {
  const status = useStatus();
  const focus = useFocus();
  const showToast = useStore((s) => s.showToast);
  const [absTarget, setAbsTarget] = useState("");
  const [afExposure, setAfExposure] = useState("2");
  const [afStep, setAfStep] = useState("350");

  const foc = status?.focuser;
  const pos = foc?.position ?? 0;
  const running = focus?.state === "running";

  const act = async (fn: () => Promise<unknown>) => {
    try { await fn(); } catch (e) { showToast("error", (e as Error).message); }
  };
  const moveTo = (p: number) => act(() => api.post("/api/focuser/move", { position: Math.round(p) }));

  return (
    <div className="grid gap-4 lg:grid-cols-[1fr_320px]">
      <Panel title="V-Curve · HFR vs Position"
        right={running && <span className="text-accent text-[11px] blink tracking-widest uppercase">measuring…</span>}>
        <VCurve points={focus?.points ?? []} best={focus?.best ?? null} />
        {focus?.state === "done" && focus.best && (
          <p className="text-good text-xs mono mt-2">
            ✓ best focus {focus.best.position}{focus.best.hfr ? ` · HFR ${focus.best.hfr.toFixed(2)} px` : ""}
          </p>
        )}
        {focus?.state === "failed" && (
          <p className="text-bad text-xs mono mt-2">✗ autofocus failed — check stars in frame</p>
        )}
      </Panel>

      <div className="flex flex-col gap-4">
        <Panel title="Focuser">
          <div className="flex items-end justify-between mb-4">
            <Stat label="position" value={foc ? pos : "—"} />
            <Stat label="max" value={foc?.max ?? "—"} />
            <Stat label="temp" value={foc?.temperature?.toFixed(1) ?? "—"} unit="°C" />
          </div>
          <div className="grid grid-cols-3 gap-2 mb-3">
            {[-1000, -100, -10, 10, 100, 1000].map((d) => (
              <button key={d} className="btn mono !normal-case" disabled={!foc || running}
                onClick={() => moveTo(pos + d)}>
                {d > 0 ? `+${d}` : d}
              </button>
            ))}
          </div>
          <div className="grid grid-cols-[1fr_auto_auto] gap-2 items-end">
            <Field label="Go to position">
              <input className="field" placeholder={String(pos)} value={absTarget}
                onChange={(e) => setAbsTarget(e.target.value)} />
            </Field>
            <button className="btn" disabled={!foc || !absTarget || running}
              onClick={() => moveTo(Number(absTarget))}>Go</button>
            <button className="btn btn-danger"
              onClick={() => act(() => api.post("/api/focuser/halt"))}>Halt</button>
          </div>
        </Panel>

        <Panel title="Autofocus">
          <div className="grid grid-cols-2 gap-3 mb-4">
            <Field label="Exposure (s)">
              <input className="field" value={afExposure} onChange={(e) => setAfExposure(e.target.value)} />
            </Field>
            <Field label="Step size">
              <input className="field" value={afStep} onChange={(e) => setAfStep(e.target.value)} />
            </Field>
          </div>
          <button className="btn btn-accent w-full" disabled={!foc || running}
            onClick={() => act(() => api.post("/api/focuser/autofocus", {
              exposure_s: Number(afExposure) || 2,
              step: Number(afStep) || 350,
            }))}>
            {running ? "Running…" : "◎ Run Autofocus"}
          </button>
          <p className="text-[11px] text-dim mt-3 leading-relaxed">
            Sweeps 4 steps each side of current position, measures star HFR,
            fits the V-curve and drives to its minimum.
          </p>
        </Panel>
      </div>
    </div>
  );
}
