// PlanLibrary.tsx - which plan am I editing, is it saved, and what else is
// saved. The rebuild of `components/sequence/PlanLibraryPanel.tsx` (439 ln).
//
// FOUR TRAPS FROM THE LEGACY PANEL COME ACROSS INTACT.
//
// 1. SAVE AS DOES NOT ADOPT THE NAME UNTIL THE SAVE LANDS. Renaming the editor
//    first, then hitting the server's name-collision prompt and cancelling,
//    left the editor renamed and dirty while still tied to the ORIGINAL
//    `loadedPlanId` - so the next plain Save silently RENAMED the original
//    saved plan. The payload is built from the entered name; only a completed
//    save writes it back into the draft.
//
// 2. ONE SAVE AT A TIME, GUARDED BY A REF. The POST mints a plan, so a
//    double-tap either mints a second identical plan or trips the server's
//    collision guard - which then accuses you of duplicating the plan your own
//    first tap just made. Two presses in one React batch share one closure, so
//    only a ref is already true when the second handler runs.
//
// 3. EXPORT REPORTS WHAT CAME BACK, NOT WHAT WAS CLICKED. A toast fired from an
//    anchor click knows nothing about the response: a deleted plan (404) or an
//    expired session (401) produced the same cheerful "Exported ...". The bytes
//    are fetched, then saved, and the filename in the toast is the filename on
//    disk.
//
// 4. A FAILED LOAD IS NOT AN EMPTY LIBRARY. A swallowed fetch failure rendering
//    "No saved plans yet" is a confident wrong answer; the error and a retry
//    are shown instead, and only when there is no stale list to keep.
//
// The name gets a whole line to itself in the list, for the reason the legacy
// file's own header gives: at a 300 px column width names ellipsized to "Fli...",
// and this sheet is narrower still.

import { useCallback, useEffect, useRef, useState, type JSX } from "react";

import { api, ApiError } from "../../../../api";
import { confirmDialog } from "../../../../components/ConfirmDialog";
import { BASE } from "../../../../lib/base";
import { useCanControlCapture } from "../../../../lib/caps";
import { parsePlanFile, planExportFilename } from "../../../../lib/planFile";
import { planRowSummary, planSavedCue } from "../../../../lib/planLibrary";
import { useStore } from "../../../../store";
import type { PlanRow, SequencePlan } from "../../../../types";
import { explainLock } from "../../../shell/explain";
import {
  ActionButton, Card, Disclosure, Field, Label, LockNote, Mono, Pill, ReadoutGrid,
  ReadoutTile, TextInput,
} from "../../../ui";
import { fmtHoursMinutes, planFrames, planSeconds } from "./planModel";

export function PlanLibrary({ plan, setPlan, lockedReason }: {
  plan: SequencePlan;
  setPlan: (p: SequencePlan, dirty?: boolean) => void;
  /** Why the plan cannot be edited at all right now (null when it can). The
   *  library is usable during a run - saving tomorrow night's plan while
   *  tonight runs is a thing people do - so this is normally null; it exists
   *  because the SAME sentence has to appear on every control if it ever is
   *  not. */
  lockedReason: string | null;
}): JSX.Element {
  const editorDirty = useStore((s) => s.editorDirty);
  const setEditorDirty = useStore((s) => s.setEditorDirty);
  const loadedPlanId = useStore((s) => s.loadedPlanId);
  const setLoadedPlanId = useStore((s) => s.setLoadedPlanId);
  const toast = useStore((s) => s.enqueueToast);
  const canWrite = useCanControlCapture();

  const [rows, setRows] = useState<PlanRow[]>([]);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [saveAsName, setSaveAsName] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [exportingId, setExportingId] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const savingRef = useRef(false);

  const writeReason = lockedReason
    ?? (canWrite ? null : "Saving, importing and deleting plans needs operator or admin access.");

  const refresh = useCallback(async () => {
    try {
      setRows(await api.get<PlanRow[]>("/api/plans"));
      setLoadErr(null);
    } catch (e) {
      setLoadErr(e instanceof Error ? e.message : "the server did not answer");
    }
  }, []);
  useEffect(() => { void refresh(); }, [refresh]);

  const cue = planSavedCue(editorDirty, loadedPlanId !== null, canWrite);
  const frames = planFrames(plan);
  const seconds = planSeconds(plan);

  /** Save `planToSave`. A loaded library plan carries its id so the server
   *  UPSERTS in place; a fresh draft mints a new plan and the server's
   *  name-collision guard opens the deliberate second-copy path. */
  const savePlan = async (planToSave: SequencePlan, id: string | null): Promise<boolean> => {
    if (savingRef.current) return false;
    savingRef.current = true;
    setSaving(true);
    try {
      const row = await api.post<PlanRow>(
        "/api/plans", id ? { plan: planToSave, id } : { plan: planToSave });
      toast({ level: "success", title: `Saved plan "${planToSave.name}"` });
      setLoadedPlanId(row.id);
      setEditorDirty(false);
      await refresh();
      return true;
    } catch (e) {
      if (e instanceof ApiError && e.code === "name_collision") {
        const ok = await confirmDialog({
          title: `A plan named "${planToSave.name}" already exists`,
          body: "Save anyway as a second copy with the same name?",
          tone: "warn",
          mode: "confirm",
          confirmLabel: "Save anyway",
        });
        if (!ok) return false;
        try {
          const row = await api.post<PlanRow>("/api/plans", { plan: planToSave, overwrite: true });
          setLoadedPlanId(row.id);
          setEditorDirty(false);
          await refresh();
          return true;
        } catch (e2) {
          toast({ level: "error", title: "Save failed", detail: (e2 as Error).message });
          return false;
        }
      }
      toast({ level: "error", title: "Save failed", detail: (e as Error).message });
      return false;
    } finally {
      savingRef.current = false;
      setSaving(false);
    }
  };

  const submitSaveAs = async (name: string) => {
    const trimmed = name.trim();
    if (trimmed === "") return;
    const next = { ...plan, name: trimmed };
    // No id: the server mints a fresh uuid. The draft adopts the name only
    // after the save lands (trap 1 at the top of this file).
    if (await savePlan(next, null)) {
      setPlan(next, false);
      setSaveAsName(null);
    }
  };

  const load = async (row: PlanRow) => {
    const ok = await confirmDialog({
      title: `Load "${row.name}"?`,
      body: "Replaces what is in this editor.",
      tone: "warn",
      mode: "confirm",
      confirmLabel: "Load",
    });
    if (!ok) return;
    try {
      setPlan(await api.get<SequencePlan>(`/api/plans/${row.id}`), false);
      setLoadedPlanId(row.id);
    } catch (e) {
      toast({ level: "error", title: "Could not load that plan", detail: (e as Error).message });
    }
  };

  const del = async (row: PlanRow) => {
    const ok = await confirmDialog({
      title: `Delete saved plan "${row.name}"?`,
      tone: "danger",
      mode: "confirm",
      confirmLabel: "Delete",
    });
    if (!ok) return;
    try {
      await api.del(`/api/plans/${row.id}`);
      // The working plan stays in the editor but no longer maps to a saved row,
      // so the cue has to stop claiming it is saved.
      if (loadedPlanId === row.id) setLoadedPlanId(null);
      await refresh();
    } catch (e) {
      toast({ level: "error", title: "Delete failed", detail: (e as Error).message });
    }
  };

  const exportRow = async (row: PlanRow) => {
    if (exportingId) return;
    setExportingId(row.id);
    const filename = planExportFilename(row.name);
    try {
      const res = await fetch(`${BASE}/api/plans/${row.id}/export`);
      if (!res.ok) {
        throw new Error(res.status === 404
          ? "that plan is no longer on the server"
          : `the server said ${res.status}`);
      }
      const url = URL.createObjectURL(await res.blob());
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      toast({ level: "success", title: `Exported ${filename}` });
    } catch (e) {
      toast({
        level: "error", title: "Export failed",
        detail: e instanceof Error ? e.message : String(e),
      });
    } finally {
      setExportingId(null);
    }
  };

  const importFile = async (file: File) => {
    try {
      const raw = parsePlanFile(await file.text());
      await api.post("/api/plans/import", raw);
      toast({ level: "info", title: "Plan imported" });
      await refresh();
    } catch (e) {
      // ApiError carries the server's version_too_new / invalid detail verbatim.
      toast({ level: "error", title: "Import failed", detail: (e as Error).message });
    }
  };

  return (
    <Card data-testid="plan-library">
      <div className="nx-plan-stack">
        <div className="nx-plan-row">
          <Label size={11}>PLAN</Label>
          <span style={{ marginLeft: "auto" }}>
            <Pill tone={cue.tone === "warn" ? "warn" : "dim"} data-testid="plan-saved-cue">
              {cue.label}
            </Pill>
          </span>
        </div>

        <Field label="PLAN NAME">
          <TextInput
            value={plan.name}
            onChange={(v) => setPlan({ ...plan, name: v })}
            ariaLabel="Plan name"
            lockedReason={lockedReason}
            data-testid="plan-name"
          />
        </Field>

        <ReadoutGrid cols={3}>
          <ReadoutTile label="FRAMES" value={String(frames)} />
          <ReadoutTile label="INTEGRATION" value={fmtHoursMinutes(seconds)} />
          <ReadoutTile label="TARGETS" value={String(plan.targets.length)} />
        </ReadoutGrid>

        <div className="nx-plan-row">
          <ActionButton
            kind="primary"
            onPress={() => void savePlan(plan, loadedPlanId)}
            busy={saving}
            lockedReason={writeReason}
            onExplain={explainLock}
            data-testid="plan-save"
          >
            {saving ? "SAVING" : "SAVE"}
          </ActionButton>
          <ActionButton
            kind="secondary"
            onPress={() => setSaveAsName(plan.name.trim() || "New plan")}
            lockedReason={writeReason}
            onExplain={explainLock}
            data-testid="plan-save-as"
          >
            SAVE AS
          </ActionButton>
          <ActionButton
            kind="ghost"
            onPress={() => fileRef.current?.click()}
            lockedReason={writeReason}
            onExplain={explainLock}
            data-testid="plan-import"
          >
            IMPORT
          </ActionButton>
          <input
            ref={fileRef}
            type="file"
            accept=".json,application/json"
            style={{ display: "none" }}
            aria-label="Import a plan file"
            onChange={(e) => {
              const f = e.target.files?.[0];
              e.target.value = "";
              if (f) void importFile(f);
            }}
          />
        </div>

        {saveAsName !== null && (
          <div className="nx-plan-stack" data-testid="plan-save-as-form">
            <Field label="NEW PLAN NAME" hint="Saves a second copy and leaves the original alone.">
              <TextInput
                value={saveAsName}
                onChange={setSaveAsName}
                onEnter={(v) => void submitSaveAs(v)}
                ariaLabel="New plan name"
                placeholder="Plan name"
                data-testid="plan-save-as-name"
              />
            </Field>
            <div className="nx-plan-row">
              <ActionButton
                kind="primary"
                onPress={() => void submitSaveAs(saveAsName)}
                busy={saving}
                lockedReason={saveAsName.trim() === "" ? "A copy needs a name." : writeReason}
                onExplain={explainLock}
                data-testid="plan-save-copy"
              >
                {saving ? "SAVING" : "SAVE COPY"}
              </ActionButton>
              <ActionButton kind="ghost" onPress={() => setSaveAsName(null)}>CANCEL</ActionButton>
            </div>
          </div>
        )}

        <Disclosure
          summary="SAVED PLANS"
          sub={loadErr && rows.length === 0 ? "could not load" : `${rows.length}`}
          defaultOpen={false}
          data-testid="plan-saved-list"
        >
          {loadErr && rows.length === 0 ? (
            <div className="nx-plan-stack">
              <p className="nx-plan-note" data-tone="bad">
                Could not load saved plans: {loadErr}
              </p>
              <ActionButton kind="ghost" onPress={() => void refresh()}
                data-testid="plan-saved-retry">RETRY</ActionButton>
            </div>
          ) : rows.length === 0 ? (
            <p className="nx-plan-note">
              No saved plans yet. SAVE writes this one to the rig so any device can load it.
            </p>
          ) : (
            <div className="nx-plan-list">
              {rows.map((r) => (
                <div key={r.id} className="nx-plan-lib"
                  data-active={r.id === loadedPlanId ? "true" : "false"}>
                  <div className="nx-plan-row">
                    <span className="nx-plan-lib-name">{r.name}</span>
                    {r.id === loadedPlanId && (
                      <Pill tone="accent">{editorDirty ? "LOADED · EDITED" : "LOADED"}</Pill>
                    )}
                  </div>
                  <div className="nx-plan-row">
                    <Mono size={10} tone="dim">{planRowSummary(r)}</Mono>
                    <span style={{ marginLeft: "auto" }} className="nx-plan-row">
                      <ActionButton kind="ghost" onPress={() => void load(r)}
                        data-testid={`plan-load-${r.id}`}>LOAD</ActionButton>
                      <ActionButton
                        kind="ghost"
                        onPress={() => void exportRow(r)}
                        busy={exportingId === r.id}
                        lockedReason={exportingId != null && exportingId !== r.id
                          ? "Another export is still downloading." : null}
                        onExplain={explainLock}
                        data-testid={`plan-export-${r.id}`}
                      >
                        {exportingId === r.id ? "EXPORTING" : "EXPORT"}
                      </ActionButton>
                      {/* No `arm` here: `confirmDialog` IS the confirm, and two
                          confirms on one destructive tap trains people to
                          dismiss both without reading either. */}
                      <ActionButton
                        kind="danger"
                        onPress={() => void del(r)}
                        lockedReason={writeReason}
                        onExplain={explainLock}
                        ariaLabel={`Delete saved plan ${r.name}`}
                        data-testid={`plan-delete-${r.id}`}
                      >
                        DELETE
                      </ActionButton>
                    </span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </Disclosure>

        <LockNote reason={writeReason} data-testid="plan-library-lock" />
      </div>
    </Card>
  );
}
