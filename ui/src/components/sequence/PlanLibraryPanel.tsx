// PlanLibraryPanel.tsx — the unified Plan panel (G2 merge). ONE harmonious
// surface that carries the plan IDENTITY (editable name + saved/unsaved cue +
// frames/integration) AND the server plan LIBRARY (Save / Save as… / Import in
// the header; per-row load / export / delete below), so "which plan am I
// editing, is it saved, and what else is saved?" reads as one system instead of
// two disconnected boxes (user screenshot user-plan-library-truncation.png;
// codex R3-PLAN-02 "one long undifferentiated surface" / R3-PLAN-03 "no
// saved/unsaved ownership cue near the plan name"). Sessions stays its OWN region
// (SequenceView renders SessionsPanel separately — not merged here).
//
// Names get width priority (the user's primary complaint — names ellipsized to
// "Fli…"/"To…"): each saved row is TWO lines — the name on its own full-width
// line (wraps up to 2), metadata + actions on the line below — so a plan name is
// never truncated to a few characters at the panel's ~300px column width.
import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "../../api";
import { useStore } from "../../store";
import { Panel, Stat } from "../ui";
import { Icon } from "../icons";
import { confirmDialog } from "../ConfirmDialog";
import { BASE } from "../../lib/base";
import { parsePlanFile, planExportFilename } from "../../lib/planFile";
import { planRowSummary, planSavedCue } from "../../lib/planLibrary";
import { useCanControlCapture } from "../../lib/caps";
import type { PlanRow, SequencePlan } from "../../types";

export default function PlanLibraryPanel() {
  const plan = useStore((s) => s.plan);
  const setPlan = useStore((s) => s.setPlan);
  const editorDirty = useStore((s) => s.editorDirty);
  const setEditorDirty = useStore((s) => s.setEditorDirty);
  const loadedPlanId = useStore((s) => s.loadedPlanId);
  const setLoadedPlanId = useStore((s) => s.setLoadedPlanId);
  const showToast = useStore((s) => s.showToast);
  const canWrite = useCanControlCapture();
  const [rows, setRows] = useState<PlanRow[]>([]);
  // A failed load must read as an ERROR, not "No saved plans yet" (UX-18):
  // track it so the list body can distinguish a swallowed fetch failure from a
  // genuinely empty library (mirrors EquipmentView's loadErr idiom).
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [listOpen, setListOpen] = useState(true);
  // Inline "Save as…" name prompt (SitePanel "Save as preset…" idiom — the modal
  // has no text input). null = closed.
  const [saveAsName, setSaveAsName] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  // One save at a time (SitePanel's `run()` guard, R5-PLN-01). The POST mints a
  // plan, so a double-tap is not a wasted request — the first tap creates the
  // row and the second either mints a SECOND identical plan or trips the
  // server's name-collision guard, which then accuses you of duplicating the
  // plan your own first tap just made. The ref is what answers the second tap:
  // two presses inside one frame share a closure, so the state flag alone is
  // still the pre-press value when the second handler runs.
  const savingRef = useRef(false);
  const [saving, setSaving] = useState(false);
  // Which row is downloading, so a slow export can say so and can't be
  // double-fired into two downloads of the same file.
  const [exportingId, setExportingId] = useState<string | null>(null);

  // Per-plan headline numbers for the identity block (mirrors SequenceView's
  // plan totals + the server's plans.py::_summarize whole-minute rounding).
  const totalFrames = plan.targets.reduce(
    (a, t) => a + t.steps.reduce((b, s) => b + s.count, 0), 0);
  const totalMinutes = plan.targets.reduce(
    (a, t) => a + t.steps.reduce((b, s) => b + s.count * s.exposure_s, 0), 0) / 60;

  // canWrite threads through so a viewer (no Save button) never sees a warn-toned
  // alarm they cannot act on — the cue stays, but dim/informational.
  const cue = planSavedCue(editorDirty, loadedPlanId !== null, canWrite);

  const refresh = useCallback(async () => {
    try {
      setRows(await api.get<PlanRow[]>("/api/plans"));
      setLoadErr(null);
    } catch (e) {
      // client fetch failures bypass the log→toast path, so surface it here:
      // an empty list on error would masquerade as "No saved plans yet" (UX-18).
      setLoadErr(e instanceof Error ? e.message : "couldn't load saved plans");
    }
  }, []);
  useEffect(() => { void refresh(); }, [refresh]);

  // Save `planToSave`. When a library plan is loaded (`id` non-null AND it still
  // exists server-side) the id rides along so the server UPSERTS it in place
  // ("save my edits back"); a fresh draft (id null) mints a new plan and the
  // server's name-collision guard prompts for the deliberate second-copy path
  // (spec §7). On success the returned row's id becomes the loaded plan and the
  // dirty cue clears. Returns true on a completed save (for the Save-as flow).
  const savePlan = async (planToSave: SequencePlan, id: string | null): Promise<boolean> => {
    if (savingRef.current) return false;
    savingRef.current = true;
    setSaving(true);
    try {
      const row = await api.post<PlanRow>(
        "/api/plans", id ? { plan: planToSave, id } : { plan: planToSave });
      showToast("info", `Saved plan '${planToSave.name}'`);
      setLoadedPlanId(row.id);
      setEditorDirty(false);
      await refresh();
      return true;
    } catch (e) {
      // surface the server's name-collision code (spec §7) with a way through
      if (e instanceof ApiError && e.code === "name_collision") {
        const ok = await confirmDialog({
          title: `A plan named '${planToSave.name}' already exists`,
          body: "Save anyway as a second copy with the same name?",
          tone: "warn",
          mode: "confirm",
          confirmLabel: "Save anyway",
        });
        if (!ok) return false;
        try {
          const row = await api.post<PlanRow>(
            "/api/plans", { plan: planToSave, overwrite: true });
          setLoadedPlanId(row.id);
          setEditorDirty(false);
          await refresh();
          return true;
        } catch (e2) {
          showToast("error", (e2 as Error).message);
          return false;
        }
      }
      showToast("error", (e as Error).message);
      return false;
    } finally {
      savingRef.current = false;
      setSaving(false);
    }
  };

  const save = () => void savePlan(plan, loadedPlanId);

  // Save as… forks a NEW named copy: build the payload from the ENTERED name
  // WITHOUT pre-mutating the working plan (SitePanel.submitSaveCurrent idiom —
  // payload from the prompt value, form synced only after success). Adopting the
  // name BEFORE the save was a real trap: a cancelled collision prompt would
  // leave the editor renamed + dirty but still tied to the ORIGINAL loadedPlanId,
  // so the next plain Save would silently RENAME the original saved plan. Save
  // WITHOUT an id so the server mints a fresh uuid; only a completed save adopts
  // the name into the editor (clean — savePlan already marked it).
  const submitSaveAs = async (name: string) => {
    const trimmed = name.trim();
    if (trimmed === "") return;
    const next = { ...plan, name: trimmed };
    if (await savePlan(next, null)) {
      setPlan(next, false); // sync the name field to the saved copy, stay clean
      setSaveAsName(null);
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
      // freshly loaded → clean (dirty=false), and tie the editor to this row.
      setPlan(await api.get<SequencePlan>(`/api/plans/${row.id}`), false);
      setLoadedPlanId(row.id);
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
      // the working plan stays in the editor, but it no longer maps to a saved
      // row — drop the tie so the cue honestly reads "not saved".
      if (loadedPlanId === row.id) setLoadedPlanId(null);
      await refresh();
    } catch (e) {
      showToast("error", (e as Error).message);
    }
  };

  const exportRow = async (row: PlanRow) => {
    // R2-PLN-03: exporting gave zero confirmation — the download can land
    // silently in the browser's download tray with no on-screen feedback. But
    // the toast used to fire from the anchor CLICK, which knows nothing about
    // what came back: a deleted plan (404) or an expired session (401) produced
    // the same green "Exported NGC7000.astroplan.json" as a real download.
    // Fetch it (ProfileList.exportRow's idiom), then save the bytes we actually
    // hold — and name the file here, so the name in the toast is the name on
    // disk rather than a second guess at the server's Content-Disposition.
    if (exportingId) return;
    setExportingId(row.id);
    const filename = planExportFilename(row.name);
    try {
      const res = await fetch(`${BASE}/api/plans/${row.id}/export`);
      if (!res.ok) {
        throw new Error(res.status === 404
          ? "that plan is no longer on the server"
          : `server said ${res.status}`);
      }
      const url = URL.createObjectURL(await res.blob());
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      showToast("success", `Exported ${filename}`);
    } catch (e) {
      showToast("error", `Export failed: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setExportingId(null);
    }
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
    <Panel title="Plan">
      <div className="flex flex-col gap-3">
        {/* ---------------------------------------- identity: name + saved cue */}
        <div className="flex flex-col gap-1 min-w-0">
          <div className="flex items-center justify-between gap-2">
            <span className="label">Plan name</span>
            <span className={`text-[11px] inline-flex items-center gap-1 ${
              cue.tone === "warn" ? "text-warn" : "text-dim"}`}>
              {cue.tone === "warn" && (
                <Icon name="alert" size={11} className="shrink-0" aria-hidden />
              )}
              {cue.label}
            </span>
          </div>
          <input
            className="field"
            value={plan.name}
            aria-label="Plan name"
            onChange={(e) => setPlan({ ...plan, name: e.target.value })}
          />
        </div>

        {/* ------------------------------------------- library-management actions */}
        {canWrite && (
          <div className="flex flex-wrap items-center gap-2">
            <button
              className="btn btn-accent tap min-h-[44px] !px-3 !text-[11px] inline-flex items-center gap-1"
              onClick={save}
              disabled={saving}
              aria-busy={saving}
            >
              <Icon name="check" size={13} /> {saving ? "Saving…" : "Save"}
            </button>
            <button
              className="btn tap min-h-[44px] !px-3 !text-[11px]"
              onClick={() => setSaveAsName(plan.name.trim() || "New plan")}
              disabled={saving}
            >
              Save as…
            </button>
            <button
              className="tap min-h-[44px] !px-3 !text-[11px] inline-flex items-center gap-1
                border border-line2 text-dim hover:text-accent hover:border-accent/50"
              onClick={() => fileRef.current?.click()}
            >
              <Icon name="upload" size={13} /> Import
            </button>
            <input
              ref={fileRef}
              type="file"
              accept=".json,application/json"
              className="hidden"
              aria-label="Import a plan file"
              onChange={(e) => {
                const f = e.target.files?.[0];
                e.target.value = "";
                if (f) void importFile(f);
              }}
            />
          </div>
        )}

        {/* Inline Save-as name prompt (SitePanel idiom). */}
        {saveAsName !== null && (
          <div className="flex flex-wrap items-center gap-2">
            <input
              className="field flex-1 !w-auto min-w-[8rem]"
              value={saveAsName}
              autoFocus
              aria-label="New plan name"
              placeholder="Plan name"
              onChange={(e) => setSaveAsName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") void submitSaveAs(saveAsName);
                if (e.key === "Escape") setSaveAsName(null);
              }}
            />
            <button
              className="btn btn-accent tap min-h-[44px] !px-3 !text-[11px]"
              disabled={saving || saveAsName.trim() === ""}
              aria-busy={saving}
              onClick={() => void submitSaveAs(saveAsName)}
            >
              {saving ? "Saving…" : "Save copy"}
            </button>
            <button
              className="btn tap min-h-[44px] !px-3 !text-[11px]"
              onClick={() => setSaveAsName(null)}
            >
              Cancel
            </button>
          </div>
        )}

        {/* ----------------------------------------------- plan headline numbers */}
        <div className="grid grid-cols-2 gap-3">
          <Stat label="frames" value={totalFrames} />
          <Stat
            label="integration"
            value={`${Math.floor(totalMinutes / 60)}h ${Math.round(totalMinutes % 60)}m`}
          />
        </div>

        {/* -------------------------------------- saved plans (collapsible list) */}
        <div className="border-t border-line pt-3">
          <button
            type="button"
            aria-expanded={listOpen}
            aria-label={`${listOpen ? "Hide" : "Show"} saved plans (${rows.length})`}
            onClick={() => setListOpen((v) => !v)}
            className="w-full tap min-h-[44px] flex items-center gap-2 text-left
              text-dim hover:text-accent transition-colors cursor-pointer"
          >
            <Icon name="plan" size={14} className="shrink-0" />
            <span className="label">Saved plans ({rows.length})</span>
            <span className="flex-1" />
            <span aria-hidden className="text-sm leading-none">{listOpen ? "▾" : "▸"}</span>
          </button>

          {listOpen && (
            // A swallowed load failure leaves rows empty — show a DISTINCT error
            // + retry (EquipmentView loadErr idiom) instead of the empty state,
            // but only when there's nothing to fall back to; a stale list stays
            // put on a background refresh failure (UX-18).
            loadErr && rows.length === 0 ? (
              <div className="mt-1.5 flex items-center gap-2 border border-bad/50 bg-bad/5 px-2 py-1.5">
                <Icon name="alert" size={12} className="text-bad shrink-0" />
                <span className="text-[11px] text-ink flex-1">
                  Couldn't load saved plans: {loadErr}
                </span>
                <button
                  type="button"
                  className="btn !py-1 !px-2 !text-[11px]"
                  onClick={() => void refresh()}
                >
                  Retry
                </button>
              </div>
            ) : rows.length === 0 ? (
              <p className="text-[11px] text-dim mt-1">No saved plans yet.</p>
            ) : (
              <div className="mt-1.5 flex flex-col gap-1.5 max-h-72 overflow-y-auto">
                {rows.map((r) => {
                  const active = r.id === loadedPlanId;
                  return (
                    <div
                      key={r.id}
                      className={`flex flex-col gap-1 border px-2 py-1.5 ${
                        active
                          ? "border-accent/60 bg-accent/[0.06]"
                          : "border-line bg-bg/50"}`}
                    >
                      {/* line 1 — the NAME on its own line, full width, wraps to
                          two lines; never ellipsized to a few chars. */}
                      <div className="flex items-start gap-2">
                        <span className="text-ink text-xs leading-snug break-words line-clamp-2 flex-1 min-w-0">
                          {r.name}
                        </span>
                        {active && (
                          <span className="mono text-[9px] uppercase tracking-wider text-accent
                            border border-accent/40 px-1 py-0.5 shrink-0 whitespace-nowrap">
                            loaded{editorDirty ? " · edited" : ""}
                          </span>
                        )}
                      </div>
                      {/* line 2 — metadata chip + per-row actions. */}
                      <div className="flex items-center gap-2">
                        <span className="mono text-[10px] text-dim">{planRowSummary(r)}</span>
                        <div className="flex-1" />
                        <button
                          className="tap min-h-[44px] !px-2 !text-[11px] text-dim hover:text-accent"
                          onClick={() => void load(r)}
                        >
                          load
                        </button>
                        <button
                          className="tap min-h-[44px] !px-2 !text-[11px] text-dim hover:text-accent"
                          onClick={() => void exportRow(r)}
                          disabled={exportingId != null}
                          aria-busy={exportingId === r.id}
                        >
                          {exportingId === r.id ? "exporting…" : "export"}
                        </button>
                        {canWrite && (
                          <button
                            className="tap min-h-[44px] min-w-[44px] inline-flex items-center justify-center text-bad hover:bg-bad/10"
                            aria-label={`Delete plan ${r.name}`}
                            onClick={() => void del(r)}
                          >
                            <Icon name="trash" size={13} />
                          </button>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            )
          )}
        </div>
      </div>
    </Panel>
  );
}
