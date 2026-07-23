// CalibrationLibraryPanel.tsx — the Settings → Calibration tab (PRO-1). Mirrors
// PlanLibraryPanel's library-panel shape: a header "Rebuild library" action, a
// loadErr/empty/list tri-state body, and per-row delete. Masters are grouped by
// type (Dark → Flat → Bias) via the tested pure helpers in lib/calibrationLibrary.
//
// The masters list is the shared store slice (useMasters), so a rebuild/delete
// here also refreshes the live pre-flight coverage row. Correctness rides on the
// Task-6 tsx tests + the typechecker (thin render; no jsdom).
import { useCallback, useEffect, useState, type JSX } from "react";
import { api } from "../../api";
import { useStore, useMasters } from "../../store";
import { Panel } from "../ui";
import { Icon } from "../icons";
import { confirmDialog } from "../ConfirmDialog";
import {
  groupMasters,
  masterRowSummary,
  type CalibrationBuildReport,
  type MasterRow,
} from "../../lib/calibrationLibrary";
import { useCanControlCapture } from "../../lib/caps";

export default function CalibrationLibraryPanel(): JSX.Element {
  const masters = useMasters();
  const showToast = useStore((s) => s.showToast);
  const canWrite = useCanControlCapture();
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [building, setBuilding] = useState(false);

  // Fetch into the SHARED store slice (feeds the pre-flight row too) and surface
  // a load failure distinctly from a genuinely-empty library (PlanLibraryPanel
  // loadErr idiom — an error must not masquerade as "no masters yet").
  const refresh = useCallback(async () => {
    try {
      const rows = await api.get<MasterRow[]>("/api/calibration/masters");
      useStore.setState({ masters: rows });
      setLoadErr(null);
    } catch (e) {
      setLoadErr(e instanceof Error ? e.message : "couldn't load masters");
    }
  }, []);
  useEffect(() => {
    void refresh();
  }, [refresh]);

  const rebuild = async () => {
    if (!canWrite || building) return;
    setBuilding(true);
    try {
      const rep = await api.post<CalibrationBuildReport>("/api/calibration/build");
      showToast(
        "info",
        `Built ${rep.masters_built} masters from ${rep.frames_indexed} frames`,
      );
      await refresh();
    } catch (e) {
      showToast("error", (e as Error).message);
    } finally {
      setBuilding(false);
    }
  };

  const del = async (m: MasterRow) => {
    const ok = await confirmDialog({
      title: "Delete this master?",
      body: masterRowSummary(m),
      tone: "danger",
      mode: "confirm",
      confirmLabel: "Delete",
    });
    if (!ok) return;
    try {
      await api.del(`/api/calibration/masters/${m.id}`);
      await refresh();
    } catch (e) {
      showToast("error", (e as Error).message);
    }
  };

  // Honest-disabled Rebuild (spec §11.8): a viewer/operator without
  // control.capture sees a dim + locked button with a title — NEVER the native
  // `disabled` attribute. The click is swallowed while locked or building.
  const inert = !canWrite || building;
  const groups = groupMasters(masters);

  return (
    <Panel title="Calibration Library">
      <div className="flex flex-col gap-3">
        <div className="flex items-center justify-between gap-2 flex-wrap">
          <p className="text-[11px] text-dim flex-1 min-w-[12rem]">
            Master darks, flats and bias built from the calibration frames under
            your capture folder. Rebuild after capturing new calibration frames.
          </p>
          <button
            type="button"
            aria-disabled={inert || undefined}
            title={
              !canWrite
                ? "Building masters needs capture-control permission"
                : undefined
            }
            onClick={inert ? undefined : () => void rebuild()}
            className={`btn tap min-h-[44px] !px-3 !text-[11px] inline-flex items-center gap-1
              ${!inert ? "btn-accent" : "!text-dim cursor-not-allowed"}`}
          >
            {!canWrite && <Icon name="lock" size={11} />}
            {building ? "Rebuilding…" : "Rebuild library"}
          </button>
        </div>

        {loadErr && masters.length === 0 ? (
          <div className="flex items-center gap-2 border border-bad/50 bg-bad/5 px-2 py-1.5">
            <Icon name="alert" size={12} className="text-bad shrink-0" />
            <span className="text-[11px] text-ink flex-1">
              Couldn't load masters: {loadErr}
            </span>
            <button
              type="button"
              className="btn !py-1 !px-2 !text-[11px]"
              onClick={() => void refresh()}
            >
              Retry
            </button>
          </div>
        ) : masters.length === 0 ? (
          <p className="text-[11px] text-dim">
            No masters yet. Capture darks/flats/bias, then Rebuild.
          </p>
        ) : (
          <div className="flex flex-col gap-3">
            {groups.map((g) => (
              <div key={g.type} className="flex flex-col gap-1.5">
                <span className="label">
                  {g.type}s ({g.rows.length})
                </span>
                <div className="flex flex-col gap-1.5">
                  {g.rows.map((m) => (
                    <div
                      key={m.id}
                      className="flex items-center gap-2 border border-line bg-bg/50 px-2 py-1.5"
                    >
                      <span className="mono text-[10px] text-dim flex-1 min-w-0 break-words">
                        {masterRowSummary(m)}
                      </span>
                      {canWrite && (
                        <button
                          type="button"
                          className="tap min-h-[44px] min-w-[44px] inline-flex items-center justify-center text-bad hover:bg-bad/10"
                          aria-label={`Delete master ${masterRowSummary(m)}`}
                          onClick={() => void del(m)}
                        >
                          <Icon name="trash" size={13} />
                        </button>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </Panel>
  );
}
