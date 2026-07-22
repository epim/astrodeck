import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { useStore, useStatus } from "../store";
import { EmptyState, Led, Panel } from "../components/ui";
import { Icon } from "../components/icons";
import { useCanControlPower } from "../lib/caps";
import ReadOnlyBadge from "../components/ReadOnlyBadge";
import type { SwitchPort } from "../types";

export default function PowerView() {
  const status = useStatus();
  const showToast = useStore((s) => s.showToast);
  const canControl = useCanControlPower(); // viewer => read-only ports
  const [ports, setPorts] = useState<SwitchPort[] | null>(null);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  // Local slider drafts (UX-17): the PWM sliders draft locally while dragging and
  // POST only on release, so the thumb no longer fights the 5s server echo and we
  // don't flood /api/switch/set with a POST-per-tick. `draggingRef` holds the port
  // currently being edited so a refresh mid-drag can't yank the thumb (reconcile
  // effect below), mirroring CaptureView's cooler/dew draft-then-commit pattern.
  const [drafts, setDrafts] = useState<Record<number, number>>({});
  const draggingRef = useRef<number | null>(null);
  const connected = !!status?.connected?.switch?.connected;

  const refresh = async () => {
    try {
      setPorts(await api.get<SwitchPort[]>("/api/switch/ports"));
      setLoadErr(null);
    } catch (e) {
      // UX-18: a failed GET must read as an error, NOT "no switches". Keep any
      // already-loaded ports (a transient poll failure must not blank the view)
      // and surface the reason distinctly — full-panel when we have nothing yet,
      // inline banner when data is already on screen (EquipmentView's idiom).
      setLoadErr(e instanceof Error ? e.message : "couldn't load power ports");
    }
  };

  useEffect(() => {
    if (!connected) { setPorts(null); setLoadErr(null); return; }
    refresh();
    const t = setInterval(refresh, 5000);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [connected]);

  // Reconcile slider drafts with server truth: once a fresh ports snapshot lands
  // and we're NOT mid-drag, drop the drafts so the echoed value shows. The commit
  // POST returns the updated ports, so release → commit → clear shows the new
  // value with no snap-back.
  useEffect(() => {
    if (draggingRef.current == null && Object.keys(drafts).length) setDrafts({});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ports]);

  const setPort = async (id: number, value: number) => {
    if (!canControl) return; // viewer: read-only, controls are inert anyway
    try { setPorts(await api.post<SwitchPort[]>("/api/switch/set", { port_id: id, value })); }
    catch (e) { showToast("error", (e as Error).message); }
  };

  // Commit a dimmer slider once, on pointer/key release: POST the released value,
  // then let the reconcile effect clear the draft when the echo returns.
  const commitDimmer = (id: number, value: number) => {
    if (draggingRef.current !== id) return; // nothing was edited on this port
    draggingRef.current = null;
    void setPort(id, value);
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

  // UX-18: first-load failure has no data to fall back to — a DISTINCT error
  // (with retry) instead of the blank/empty "no ports" render (EquipmentView §render).
  if (loadErr && !ports) {
    return (
      <Panel title="Power & Dew Control">
        <EmptyState
          icon="alert"
          title="Couldn't load power ports"
          hint={loadErr}
          action={
            <button type="button" className="btn btn-accent !py-1.5" onClick={() => void refresh()}>
              Retry
            </button>
          }
        />
      </Panel>
    );
  }

  const toggles = ports?.filter((p) => p.can_write && p.is_boolean) ?? [];
  const dimmers = ports?.filter((p) => p.can_write && !p.is_boolean) ?? [];
  const sensors = ports?.filter((p) => !p.can_write) ?? [];

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      {/* UX-18: background-refresh failure with data already shown — a non-destructive
          inline banner + retry (never the full-panel blank above), spanning both cols. */}
      {loadErr && ports && (
        <div className="lg:col-span-2 flex items-center gap-2 border border-bad/50 bg-bad/5 px-3 py-2">
          <Icon name="alert" size={14} className="text-bad shrink-0" />
          <span className="text-sm text-ink flex-1">Couldn't refresh power ports: {loadErr}</span>
          <button type="button" className="btn !py-1 !px-2 text-[11px]" onClick={() => void refresh()}>
            Retry
          </button>
        </div>
      )}
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
            {dimmers.map((p) => {
              // Draft wins while dragging THIS port; otherwise the server value
              // shows (reconcile effect drops drafts when idle).
              const shown = drafts[p.id] ?? p.value;
              return (
                <div key={p.id}>
                  <div className="flex justify-between mb-1.5">
                    <span className="text-xs">{p.name}</span>
                    <span className="mono text-xs text-accent">{shown.toFixed(0)}{p.unit}</span>
                  </div>
                  <input type="range" min={p.min} max={p.max} value={shown}
                    disabled={!canControl}
                    className={`w-full accent-(--accent) ${canControl ? "cursor-pointer" : "opacity-50 cursor-default"}`}
                    onChange={(e) => {
                      // Drag/keyboard tick: update the local draft only — no POST.
                      draggingRef.current = p.id;
                      const v = Number(e.target.value);
                      setDrafts((d) => ({ ...d, [p.id]: v }));
                    }}
                    onPointerUp={(e) => commitDimmer(p.id, Number(e.currentTarget.value))}
                    onKeyUp={(e) => commitDimmer(p.id, Number(e.currentTarget.value))} />
                </div>
              );
            })}
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
