// SessionsPanel.tsx — multi-night session cards (sessions spec §7): name +
// status chip + per-target accepted/total bars; Resume (dormant), Update from
// Plan (dormant, id-safe with kept/new/dropped confirm), auto-resume arm (with
// the no-safety-monitor confirm + persistent warning chip), delete
// (confirm-then-delete, no undo — server state). Night-mode safe: existing
// tokens/classes only.
import { useCallback, useEffect, useState } from "react";
import { useStore } from "../../store";
import { Panel, Toggle } from "../ui";
import { Icon } from "../icons";
import { confirmDialog } from "../ConfirmDialog";
import { useCanControlMount } from "../../lib/caps";
import { ensurePlanIds } from "../../lib/ids";
import { mergePreview, targetProgress } from "../../lib/sessions";
import {
  deleteSession, getSession, listSessions, patchSession, resumeSession,
} from "../../api/sessions";
import type { Session, SessionRow } from "../../types";

function StatusChip({ status }: { status: SessionRow["status"] }) {
  const cls = status === "active" ? "text-good blink"
    : status === "dormant" ? "text-warn"
    : status === "complete" ? "text-accent" : "text-dim";
  return <span className={`text-[10px] tracking-widest uppercase ${cls}`}>{status}</span>;
}

export default function SessionsPanel() {
  const plan = useStore((s) => s.plan);
  const safety = useStore((s) => s.safety);
  const sequence = useStore((s) => s.sequence);
  const showToast = useStore((s) => s.showToast);
  const canControl = useCanControlMount();
  const [rows, setRows] = useState<SessionRow[]>([]);
  const [details, setDetails] = useState<Record<string, Session>>({});

  const refresh = useCallback(async () => {
    try {
      const all = (await listSessions()).filter((r) => r.status !== "abandoned");
      setRows(all);
      const loaded = await Promise.all(
        all.map((r) => getSession(r.id).catch(() => null)));
      const map: Record<string, Session> = {};
      loaded.forEach((s) => { if (s) map[s.id] = s; });
      setDetails(map);
    } catch {
      /* additive surface — a fetch failure just leaves the panel empty */
    }
  }, []);

  // on mount + whenever the run state OR the live session sub-state changes
  // (start/dormant/complete all change what the cards should show, and a
  // back-to-back session swap changes session.id while state stays "running"),
  // + after every action below.
  useEffect(() => { void refresh(); }, [refresh, sequence.state, sequence.session?.id]);

  const noMonitor = !safety || !safety.connected;

  const act = async (label: string, fn: () => Promise<unknown>) => {
    try {
      await fn();
      await refresh();
    } catch (e) {
      showToast("error", `${label} failed: ${(e as Error).message}`);
    }
  };

  const onUpdateFromPlan = async (r: SessionRow) => {
    const s = details[r.id];
    if (!s) return;
    const next = ensurePlanIds(plan);
    const d = mergePreview(s, next);
    const ok = await confirmDialog({
      title: `Update "${r.name}" from the current plan?`,
      body: `${d.kept} step${d.kept === 1 ? "" : "s"} keep recorded progress · ` +
        `${d.added} new start at zero · ${d.dropped} with recorded frames dropped ` +
        `(their frames stay in the ledger but stop counting toward any quota).`,
      tone: d.dropped > 0 ? "danger" : "warn",
      mode: "confirm",
      confirmLabel: "Update session",
    });
    if (ok) await act("Update", () => patchSession(r.id, { plan: next }));
  };

  const onArm = async (r: SessionRow, v: boolean) => {
    if (v && noMonitor) {
      const ok = await confirmDialog({
        title: "Arm auto-resume without a safety monitor?",
        body: "No safety monitor is connected — the rig may start unattended in bad weather. A persistent warning stays on this card while armed.",
        tone: "danger",
        mode: "confirm",
        confirmLabel: "Arm anyway",
      });
      if (!ok) return;
    }
    await act("Auto-resume", () => patchSession(r.id, { auto_resume: v }));
  };

  const onDelete = async (r: SessionRow) => {
    const ok = await confirmDialog({
      title: `Delete session "${r.name}"?`,
      body: "Removes the session ledger and thumbnails. Saved FITS frames are NOT deleted. This cannot be undone.",
      tone: "danger",
      mode: "confirm",
      confirmLabel: "Delete",
    });
    if (ok) await act("Delete", () => deleteSession(r.id));
  };

  if (rows.length === 0) return null;

  return (
    <Panel title="Sessions">
      <div className="flex flex-col gap-3 text-xs">
        {rows.map((r) => {
          const s = details[r.id];
          return (
            <div key={r.id} className="border border-line bg-bg/50 p-3 flex flex-col gap-2">
              <div className="flex items-center gap-2 flex-wrap">
                <span className="font-display font-semibold text-accent tracking-wider">{r.name}</span>
                <StatusChip status={r.status} />
                <span className="mono text-[11px] text-dim">
                  {r.accepted}/{r.total} · {r.nights} night{r.nights === 1 ? "" : "s"}
                </span>
                <div className="flex-1" />
                {canControl && r.status === "dormant" && (
                  <button className="btn tap min-h-[44px] inline-flex items-center gap-1 !px-3 !text-[11px]"
                    onClick={() => void act("Resume", () => resumeSession(r.id))}>
                    <Icon name="play" size={12} /> resume
                  </button>
                )}
                {canControl && r.status === "dormant" && (
                  <button
                    className="tap min-h-[44px] inline-flex items-center gap-1 !px-3 !text-[11px]
                      border border-line2 text-dim hover:text-accent hover:border-accent/50"
                    title="Replace this session's plan with the current Plan panel (id-safe)"
                    onClick={() => void onUpdateFromPlan(r)}>
                    <Icon name="refresh" size={12} /> update from plan
                  </button>
                )}
                {canControl && r.status !== "active" && (
                  <button
                    className="tap min-h-[44px] min-w-[44px] inline-flex items-center justify-center
                      border border-bad/60 text-bad hover:bg-bad/10"
                    aria-label={`Delete session ${r.name}`}
                    title={`Delete ${r.name}`}
                    onClick={() => void onDelete(r)}>
                    <Icon name="trash" size={14} />
                  </button>
                )}
              </div>
              {s && targetProgress(s.plan, s.frames).map((tp) => (
                <div key={tp.target_id} className="flex items-center gap-2 text-[11px]">
                  <span className="text-dim w-28 truncate">{tp.name}</span>
                  <div className="flex-1 h-1.5 bg-line2/60 overflow-hidden">
                    <div className="h-full bg-accent/70"
                      style={{ width: `${tp.total ? Math.min(100, (100 * tp.accepted) / tp.total) : 0}%` }} />
                  </div>
                  <span className="mono text-dim">{tp.accepted}/{tp.total}</span>
                </div>
              ))}
              {canControl && r.status === "dormant" && (
                <label className="flex items-center justify-between gap-2">
                  <span className="text-dim">auto-resume at dusk</span>
                  <Toggle checked={r.auto_resume} onChange={(v) => void onArm(r, v)} />
                </label>
              )}
              {r.auto_resume && noMonitor && (
                <span className="text-[11px] text-warn inline-flex items-center gap-1">
                  <Icon name="alert" size={12} />
                  auto-resume armed without a safety monitor — rig may start in bad weather
                </span>
              )}
            </div>
          );
        })}
      </div>
    </Panel>
  );
}
