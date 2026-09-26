// SessionsPanel.tsx — multi-night session cards (sessions spec §7): name +
// status chip + per-target accepted/total bars; Resume (dormant), Update from
// Plan (dormant, id-safe with kept/new/dropped confirm), auto-resume arm (with
// the no-safety-monitor confirm + persistent warning chip), abandon
// (dormant/complete, soft-retire — plain confirm, reversible only via the
// API, never active), delete (confirm-then-delete, no undo — server state).
// Night-mode safe: existing tokens/classes only.
//
// A FILE THE STORE CANNOT READ GETS A ROW TOO (#242), and it is not a session
// card. The server lists it with its reason (not valid JSON, fails validation,
// or no status at all, #218) so that it can be seen and deleted; it used to be
// invisible, and removing it took a shell on the rig. It shows the name the
// file carries (its id beside it when they differ), the word UNREADABLE, the
// reason as sent and the same delete as a session, gated the same way. It has
// no counts, no dates, no review, resume, update or auto-resume, because every
// one of those reads the file that is broken, and a row of "0/0" would say the
// session is empty when nobody knows.
//
// ITS DELETE CONFIRM SAYS WHAT GOES (#266). The server removes only that file
// when a backup sits beside it, and keeps the backup and the thumbnails, since
// the backup can be the last good copy of the ledger; the row says
// `backup: true` then. So the confirm is `unreadableDeleteBody`'s, which names
// the file and, when the row reports one, the backup that stays. The session
// sentence ("Removes the session ledger and thumbnails") is a readable
// session's only.
import { useCallback, useEffect, useState } from "react";
import { useStore, useWeather } from "../../store";
import { Panel, Toggle } from "../ui";
import { Icon } from "../icons";
import { confirmDialog } from "../ConfirmDialog";
import SessionReviewDrawer from "./SessionReviewDrawer";
import { sessionDates } from "./sessionDates";
import { useCanControlCapture, useCanControlMount } from "../../lib/caps";
import { setIgnoreTonight } from "../../api/weather";
import { ensurePlanIds } from "../../lib/ids";
import { mergePreview, targetProgress } from "../../lib/sessions";
import {
  deleteSession, getSession, isUnreadableRow, listSessionRows, patchSession, resumeSession,
  unreadableDeleteBody,
} from "../../api/sessions";
import type { Session, SessionListRow, SessionRow } from "../../types";

/** A readable session's delete confirm. Not an unreadable row's (#266): see
 *  `unreadableDeleteBody`. */
const SESSION_DELETE_BODY =
  "Removes the session ledger and thumbnails. Saved FITS frames are NOT deleted. This cannot be undone.";

function StatusChip({ status }: { status: SessionListRow["status"] }) {
  const cls = status === "active" ? "text-good blink"
    : status === "dormant" ? "text-warn"
    : status === "complete" ? "text-accent"
    : status === "unreadable" ? "text-bad" : "text-dim";
  return <span className={`text-[10px] tracking-widest uppercase ${cls}`}>{status}</span>;
}

export default function SessionsPanel() {
  const plan = useStore((s) => s.plan);
  const safety = useStore((s) => s.safety);
  const sequence = useStore((s) => s.sequence);
  const showToast = useStore((s) => s.showToast);
  const canControl = useCanControlMount();
  const canCapture = useCanControlCapture();
  const weather = useWeather();

  const onIgnoreWeather = async (v: boolean) => {
    try {
      const raw = await setIgnoreTonight(v);
      useStore.getState().handleEvent({
        type: "weather",
        data: raw as unknown as Record<string, unknown>,
        ts: Date.now() / 1000,
      });
    } catch (e) {
      showToast("error", `Weather override failed: ${(e as Error).message}`);
    }
  };
  const [rows, setRows] = useState<SessionListRow[]>([]);
  const [details, setDetails] = useState<Record<string, Session>>({});
  const [reviewId, setReviewId] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const all = (await listSessionRows()).filter((r) => r.status !== "abandoned");
      setRows(all);
      // Not for an unreadable file: its GET answers 500 by construction, and
      // there are no per-target bars to draw for it.
      const loaded = await Promise.all(
        all.filter((r) => !isUnreadableRow(r)).map((r) => getSession(r.id).catch(() => null)));
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

  const onAbandon = async (r: SessionRow) => {
    const ok = await confirmDialog({
      title: `Abandon "${r.name}"?`,
      body: "Removes it from the panel but keeps its ledger and thumbnails on disk. Delete, by contrast, permanently removes them.",
      tone: "warn",
      mode: "confirm",
      confirmLabel: "Abandon",
    });
    if (ok) await act("Abandon", () => patchSession(r.id, { status: "abandoned" }));
  };

  // By id and a label rather than a row, so an unreadable file (which has no
  // name, only its id) goes through the very same confirm and route. The body
  // is the caller's, because the two remove different things (#266).
  const onDelete = async (id: string, label: string, body: string) => {
    const ok = await confirmDialog({
      title: `Delete session "${label}"?`,
      body,
      tone: "danger",
      mode: "confirm",
      confirmLabel: "Delete",
    });
    if (ok) await act("Delete", () => deleteSession(id));
  };

  if (rows.length === 0) return null;

  return (
    <>
      <Panel title="Sessions">
      <div className="flex flex-col gap-3 text-xs">
        {rows.map((r) => {
          if (isUnreadableRow(r)) {
            // Read-only: the reason, and the delete gated exactly as a
            // session's (control.mount; it can never be the running one).
            return (
              <div key={r.id} className="border border-bad/40 bg-bg/50 p-3 flex flex-col gap-2"
                data-testid={`session-unreadable-${r.id}`}>
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="font-display font-semibold text-ink tracking-wider break-all">{r.name}</span>
                  {/* The id is what the delete removes; shown when the name
                      inside the file does not already say it, so a broken
                      copy can be told from a readable session of that name. */}
                  {r.name !== r.id && (
                    <span className="mono text-[11px] text-dim break-all">{r.id}</span>
                  )}
                  <StatusChip status={r.status} />
                  <div className="flex-1" />
                  {canControl && (
                    <button
                      className="tap min-h-[44px] min-w-[44px] inline-flex items-center justify-center
                        border border-bad/60 text-bad hover:bg-bad/10"
                      aria-label={`Delete unreadable session ${r.name}`}
                      title={`Delete ${r.id}`}
                      onClick={() => void onDelete(r.id, r.name, unreadableDeleteBody(r))}>
                      <Icon name="trash" size={14} />
                    </button>
                  )}
                </div>
                <p className="text-[11px] text-dim break-words">{r.unreadable}</p>
              </div>
            );
          }
          const s = details[r.id];
          return (
            <div key={r.id} className="border border-line bg-bg/50 p-3 flex flex-col gap-2">
              <div className="flex items-center gap-2 flex-wrap">
                <span className="font-display font-semibold text-accent tracking-wider">{r.name}</span>
                <StatusChip status={r.status} />
                <span className="mono text-[11px] text-dim">
                  {r.accepted}/{r.total} · {r.nights} night{r.nights === 1 ? "" : "s"}
                </span>
                {/* #35 — which "Tonight" is this one? */}
                <span className="mono text-[11px] text-dim whitespace-nowrap">
                  {sessionDates(r.created_ts, r.updated_ts)}
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
                <button
                  className="tap min-h-[44px] inline-flex items-center gap-1 !px-3 !text-[11px]
                    border border-line2 text-dim hover:text-accent hover:border-accent/50"
                  title={`Review frames of ${r.name}`}
                  onClick={() => setReviewId(r.id)}>
                  <Icon name="eye" size={12} /> review
                </button>
                {canControl && (r.status === "dormant" || r.status === "complete") && (
                  <button
                    className="tap min-h-[44px] inline-flex items-center gap-1 !px-3 !text-[11px]
                      border border-line2 text-dim hover:text-warn hover:border-warn/50"
                    title={`Abandon ${r.name} — soft-retire, keeps ledger and thumbnails on disk`}
                    onClick={() => void onAbandon(r)}>
                    <Icon name="x" size={12} /> abandon
                  </button>
                )}
                {canControl && r.status !== "active" && (
                  <button
                    className="tap min-h-[44px] min-w-[44px] inline-flex items-center justify-center
                      border border-bad/60 text-bad hover:bg-bad/10"
                    aria-label={`Delete session ${r.name}`}
                    title={`Delete ${r.name}`}
                    onClick={() => void onDelete(r.id, r.name, SESSION_DELETE_BODY)}>
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
                  <Toggle checked={r.auto_resume} onChange={(v) => void onArm(r, v)}
                    label={`Auto-resume ${r.name} at dusk`} />
                </label>
              )}
              {r.auto_resume && noMonitor && (
                <span className="text-[11px] text-warn inline-flex items-center gap-1">
                  <Icon name="alert" size={12} />
                  auto-resume armed without a safety monitor — rig may start in bad weather
                </span>
              )}
              {r.auto_resume && weather?.alert && !weather.ignore_tonight && (
                <span className="text-[11px] text-warn inline-flex items-center gap-1">
                  <Icon name="alert" size={12} />
                  high cloud forecast tonight - it does not hold auto-resume; only forecast rain within the hour does
                </span>
              )}
              {r.auto_resume && weather?.ignore_tonight && (
                <span className="text-[11px] text-warn inline-flex items-center gap-1">
                  <Icon name="alert" size={12} />
                  weather override active - forecast rain will not hold auto-resume until the next dusk (cloud forecasts never do)
                </span>
              )}
              {r.auto_resume && weather?.alert && (
                <label className="flex items-center justify-between gap-2">
                  <span className="text-dim">ignore weather tonight</span>
                  <Toggle
                    checked={!!weather.ignore_tonight}
                    disabled={!canCapture}
                    onChange={(v) => void onIgnoreWeather(v)}
                    label="Ignore weather tonight"
                  />
                </label>
              )}
            </div>
          );
        })}
      </div>
      </Panel>
      <SessionReviewDrawer id={reviewId}
        onClose={() => { setReviewId(null); void refresh(); }} />
    </>
  );
}
