// PlanLibraryPanel.tsx — save/list/load/delete/export/import for the server
// plan library (sessions spec §7). GREENFIELD: /api/plans had zero client
// consumers before this panel — minimal compact section, no new patterns.
import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "../../api";
import { useStore } from "../../store";
import { Panel } from "../ui";
import { Icon } from "../icons";
import { confirmDialog } from "../ConfirmDialog";
import { BASE } from "../../lib/base";
import { parsePlanFile } from "../../lib/planFile";
import { useCanControlCapture } from "../../lib/caps";
import type { PlanRow, SequencePlan } from "../../types";

export default function PlanLibraryPanel() {
  const plan = useStore((s) => s.plan);
  const setPlan = useStore((s) => s.setPlan);
  const showToast = useStore((s) => s.showToast);
  const canWrite = useCanControlCapture();
  const [rows, setRows] = useState<PlanRow[]>([]);
  const fileRef = useRef<HTMLInputElement>(null);

  const refresh = useCallback(async () => {
    try {
      setRows(await api.get<PlanRow[]>("/api/plans"));
    } catch {
      /* list is non-critical; panel just shows empty */
    }
  }, []);
  useEffect(() => { void refresh(); }, [refresh]);

  const save = async () => {
    try {
      await api.post("/api/plans", { plan });
      showToast("info", `Saved plan '${plan.name}'`);
      await refresh();
    } catch (e) {
      // surface the server's name-collision code (spec §7) with a way through
      if (e instanceof ApiError && e.code === "name_collision") {
        const ok = await confirmDialog({
          title: `A plan named '${plan.name}' already exists`,
          body: "Save anyway as a second copy with the same name?",
          tone: "warn",
          mode: "confirm",
          confirmLabel: "Save anyway",
        });
        if (!ok) return;
        try {
          await api.post("/api/plans", { plan, overwrite: true });
          await refresh();
        } catch (e2) {
          showToast("error", (e2 as Error).message);
        }
        return;
      }
      showToast("error", (e as Error).message);
    }
  };

  const load = async (row: PlanRow) => {
    const ok = await confirmDialog({
      title: `Load '${row.name}'?`,
      body: "Replaces the current Plan panel contents.",
      tone: "warn",
      mode: "confirm",
      confirmLabel: "Load",
    });
    if (!ok) return;
    try {
      setPlan(await api.get<SequencePlan>(`/api/plans/${row.id}`));
    } catch (e) {
      showToast("error", (e as Error).message);
    }
  };

  const del = async (row: PlanRow) => {
    const ok = await confirmDialog({
      title: `Delete saved plan '${row.name}'?`,
      tone: "danger",
      mode: "confirm",
      confirmLabel: "Delete",
    });
    if (!ok) return;
    try {
      await api.del(`/api/plans/${row.id}`);
      await refresh();
    } catch (e) {
      showToast("error", (e as Error).message);
    }
  };

  const exportRow = (row: PlanRow) => {
    // anchor download: Content-Disposition names the file; cookie auth rides
    // along on the same-origin navigation.
    const a = document.createElement("a");
    a.href = `${BASE}/api/plans/${row.id}/export`;
    a.download = "";
    document.body.appendChild(a);
    a.click();
    a.remove();
  };

  const importFile = async (file: File) => {
    try {
      const raw = parsePlanFile(await file.text());
      await api.post("/api/plans/import", raw);
      showToast("info", "Plan imported");
      await refresh();
    } catch (e) {
      // ApiError carries the server's version_too_new/invalid detail verbatim
      showToast("error", `Import failed: ${(e as Error).message}`);
    }
  };

  return (
    <Panel title="Plan library" right={canWrite ? (
      <div className="inline-flex items-center gap-1.5">
        <button className="btn tap min-h-[44px] !px-3 !text-[11px]"
          onClick={() => void save()}>
          save current
        </button>
        <button
          className="tap min-h-[44px] !px-3 !text-[11px] border border-line2 text-dim
            hover:text-accent hover:border-accent/50"
          onClick={() => fileRef.current?.click()}>
          import
        </button>
        <input ref={fileRef} type="file" accept=".json,application/json"
          className="hidden" aria-label="Import a plan file"
          onChange={(e) => {
            const f = e.target.files?.[0];
            e.target.value = "";
            if (f) void importFile(f);
          }} />
      </div>
    ) : undefined}>
      {rows.length === 0 ? (
        <p className="text-[11px] text-dim">No saved plans yet.</p>
      ) : (
        <div className="flex flex-col gap-1.5 text-xs">
          {rows.map((r) => (
            <div key={r.id}
              className="flex items-center gap-2 border border-line bg-bg/50 px-2 py-1.5">
              <span className="text-ink truncate">{r.name}</span>
              <span className="mono text-[10px] text-dim">
                {r.targets}t · {r.frames}f · {Math.round(r.integration_min)}m
              </span>
              <div className="flex-1" />
              <button className="tap min-h-[44px] !px-2 !text-[11px] text-dim hover:text-accent"
                onClick={() => void load(r)}>load</button>
              <button className="tap min-h-[44px] !px-2 !text-[11px] text-dim hover:text-accent"
                onClick={() => exportRow(r)}>export</button>
              {canWrite && (
                <button
                  className="tap min-h-[44px] min-w-[44px] inline-flex items-center justify-center text-bad hover:bg-bad/10"
                  aria-label={`Delete plan ${r.name}`} onClick={() => void del(r)}>
                  <Icon name="trash" size={13} />
                </button>
              )}
            </div>
          ))}
        </div>
      )}
    </Panel>
  );
}
