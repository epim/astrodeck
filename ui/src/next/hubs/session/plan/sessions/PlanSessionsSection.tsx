// PlanSessionsSection.tsx - the multi-night sessions ledger inside the plan
// editor, rebuilt.
//
// A SESSION IS A QUOTA THAT OUTLIVES A NIGHT. The plan says "20 x 300s of Ha";
// the session records how many of those were ACCEPTED, across however many
// nights it takes, and can be resumed at dusk without anyone present. That is
// why the destructive verbs here need sentences and not labels: "abandon" keeps
// the ledger and the thumbnails, "delete" removes them, and the two words sound
// like synonyms.
//
// THE VERBS ARE NOT RE-DERIVED. `hubs/session/gallery/cardActions.ts` already
// owns resume / update-from-plan / auto-resume / abandon / delete for the new
// UI, with the confirm bodies verbatim and the toasts written. This section
// calls those, so the Gallery shelf and the plan editor cannot drift into two
// different answers about what DELETE removes.
//
// WHAT THE LEGACY PANEL HID, THIS ONE STATES. `SessionsPanel` rendered each
// write verb only when the role held `control.mount`, so a viewer saw a card
// with no verbs at all and no way to learn what was missing. Here every verb is
// present and carries the reason it will not fire (ARCHITECTURE section 8), and
// the read-only note is stated once at the top rather than implied by absence.
//
// ARMED AUTO-RESUME IS THE DANGEROUS STATE, so it keeps all three of the legacy
// warnings: no safety monitor, a cloud forecast that does NOT hold it, and an
// active weather override that suppresses the one forecast that would.

import { useCallback, useEffect, useState, type JSX } from "react";

import { setIgnoreTonight } from "../../../../../api/weather";
import { getSession, listSessions } from "../../../../../api/sessions";
import { sessionDates } from "../../../../../components/sequence/sessionDates";
import SessionReviewDrawer from "../../../../../components/sequence/SessionReviewDrawer";
import { targetProgress } from "../../../../../lib/sessions";
import { useSafety, useSequence, useStore, useWeather } from "../../../../../store";
import type { Session, SessionRow } from "../../../../../types";
import {
  ActionButton, Bar, Card, Label, LockNote, Mono, StatusPill, Switch,
} from "../../../../ui";
import {
  runAbandon, runAutoResume, runDelete, runResume, runUpdateFromPlan,
} from "../../gallery/cardActions";
import "./sessions.css";

export interface PlanSectionProps {
  lockedReason: string | null;
  onExplain: (reason: string) => void;
}

/** Nothing has ever been started from a plan on this rig. The legacy panel
 *  rendered `null` here, which hides the whole idea of a multi-night quota from
 *  the one screen that could explain it. */
export const NO_SESSIONS =
  "No multi-night session yet. Starting a plan whose steps count ACCEPTED frames "
  + "opens one, and it keeps its own tally across nights.";

const STATUS_TONE = {
  active: "good", dormant: "warn", complete: "accent", abandoned: "dim",
} as const;

export function PlanSessionsSection({ lockedReason, onExplain }: PlanSectionProps): JSX.Element {
  const plan = useStore((s) => s.plan);
  const safety = useSafety();
  const sequence = useSequence();
  const weather = useWeather();
  const [rows, setRows] = useState<SessionRow[]>([]);
  const [details, setDetails] = useState<Record<string, Session>>({});
  const [reviewId, setReviewId] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const all = (await listSessions()).filter((r) => r.status !== "abandoned");
      setRows(all);
      const loaded = await Promise.all(all.map((r) => getSession(r.id).catch(() => null)));
      const map: Record<string, Session> = {};
      loaded.forEach((s) => { if (s) map[s.id] = s; });
      setDetails(map);
    } catch {
      // Additive surface: a fetch failure leaves the ledger empty rather than
      // taking the plan editor down with it.
    }
  }, []);

  // On mount, and whenever the run state OR the live session sub-state changes:
  // start / dormant / complete all change what the cards should show, and a
  // back-to-back session swap changes `session.id` while state stays "running".
  useEffect(() => { void refresh(); }, [refresh, sequence.state, sequence.session?.id]);

  const noMonitor = !safety || !safety.connected;
  const after = () => { void refresh(); };

  const onIgnoreWeather = async (v: boolean) => {
    try {
      const raw = await setIgnoreTonight(v);
      useStore.getState().handleEvent({
        type: "weather",
        data: raw as unknown as Record<string, unknown>,
        ts: Date.now() / 1000,
      });
    } catch (e) {
      useStore.getState().enqueueToast({
        level: "error",
        title: "Weather override failed",
        detail: (e as Error).message,
      });
    }
  };

  /** Why a verb will not fire, most specific first. The capability is the same
   *  one the server enforces on resume / PATCH / DELETE, so a refusal here says
   *  what a 403 would have said, before the round trip. */
  const dormantOnly = (r: SessionRow) => lockedReason
    ?? (r.status === "dormant" ? null : "Only a dormant session can be resumed or re-pointed.");
  const retireable = (r: SessionRow) => lockedReason
    ?? (r.status === "dormant" || r.status === "complete"
      ? null : "Only a dormant or complete session can be abandoned.");
  const deletable = (r: SessionRow) => lockedReason
    ?? (r.status === "active" ? "The session is running - stop the run first." : null);

  return (
    <Card className="nx-plansess" data-testid="plan-sessions">
      <div className="nx-plansess-head">
        <Label>SESSIONS</Label>
        <Mono size={10} tone="dim">
          {rows.length === 0 ? "none yet"
            : `${rows.length} session${rows.length === 1 ? "" : "s"} carrying a quota`}
        </Mono>
      </div>

      <LockNote reason={lockedReason} data-testid="plan-sessions-lock" />

      {rows.length === 0 && <Mono size={10.5} tone="dim">{NO_SESSIONS}</Mono>}

      {rows.map((r) => {
        const s = details[r.id];
        const progress = s ? targetProgress(s.plan, s.frames) : [];
        return (
          <div className="nx-plansess-row" key={r.id} data-testid={`plan-session-${r.id}`}>
            <div className="nx-plansess-title">
              <Mono size={11}>{r.name}</Mono>
              <StatusPill
                text={r.status}
                tone={STATUS_TONE[r.status] ?? "dim"}
                pulse={r.status === "active"}
              />
            </div>
            <Mono size={10} tone="dim">
              {r.accepted}/{r.total} accepted over {r.nights} night{r.nights === 1 ? "" : "s"}
              {" · "}{sessionDates(r.created_ts, r.updated_ts)}
            </Mono>

            {progress.map((tp) => (
              <div className="nx-plansess-target" key={tp.target_id}>
                <Mono size={10} tone="dim">{tp.name}</Mono>
                <Bar value={tp.total ? Math.min(1, tp.accepted / tp.total) : 0} />
                <Mono size={10} tone="dim">{tp.accepted}/{tp.total}</Mono>
              </div>
            ))}

            <div className="nx-plansess-verbs">
              <ActionButton
                kind="secondary"
                lockedReason={dormantOnly(r)}
                onExplain={onExplain}
                onPress={() => void runResume(r.id, after)}
                data-testid={`plan-session-resume-${r.id}`}
              >
                resume
              </ActionButton>
              <ActionButton
                kind="ghost"
                lockedReason={dormantOnly(r)}
                onExplain={onExplain}
                onPress={() => void runUpdateFromPlan(r.id, plan, after)}
                ariaLabel={`Re-point ${r.name} at the current plan`}
                data-testid={`plan-session-update-${r.id}`}
              >
                update from plan
              </ActionButton>
              <ActionButton
                kind="ghost"
                onPress={() => setReviewId(r.id)}
                ariaLabel={`Review the frames of ${r.name}`}
                data-testid={`plan-session-review-${r.id}`}
              >
                review frames
              </ActionButton>
              <ActionButton
                kind="ghost"
                lockedReason={retireable(r)}
                onExplain={onExplain}
                onPress={() => void runAbandon(r.id, r.name, after)}
                data-testid={`plan-session-abandon-${r.id}`}
              >
                abandon
              </ActionButton>
              <ActionButton
                kind="danger"
                lockedReason={deletable(r)}
                onExplain={onExplain}
                onPress={() => void runDelete(r.id, r.name, after)}
                ariaLabel={`Delete session ${r.name}`}
                data-testid={`plan-session-delete-${r.id}`}
              >
                delete
              </ActionButton>
            </div>

            <Switch
              checked={r.auto_resume}
              onChange={(v) => void runAutoResume(r.id, v, !noMonitor, after)}
              label={`Auto-resume ${r.name} at dusk`}
              note="Starts this session again at dusk with nobody present."
              lockedReason={dormantOnly(r)}
              onExplain={onExplain}
              data-testid={`plan-session-arm-${r.id}`}
            />

            {r.auto_resume && noMonitor && (
              <p className="nx-plansess-warn" data-testid={`plan-session-nomonitor-${r.id}`}>
                Auto-resume is armed with no safety monitor connected - the rig may start
                itself in bad weather.
              </p>
            )}
            {r.auto_resume && weather?.alert && !weather.ignore_tonight && (
              <p className="nx-plansess-warn">
                High cloud is forecast tonight. It does not hold auto-resume; only forecast
                rain within the hour does.
              </p>
            )}
            {r.auto_resume && weather?.ignore_tonight && (
              <p className="nx-plansess-warn">
                A weather override is active: forecast rain will not hold auto-resume until
                the next dusk. Cloud forecasts never do.
              </p>
            )}
            {r.auto_resume && weather?.alert && (
              <Switch
                checked={!!weather.ignore_tonight}
                onChange={(v) => void onIgnoreWeather(v)}
                label="Ignore weather tonight"
                note="Lets auto-resume run through tonight's rain forecast. Clears at the next dusk."
                lockedReason={lockedReason}
                onExplain={onExplain}
                data-testid={`plan-session-ignore-weather-${r.id}`}
              />
            )}
          </div>
        );
      })}

      {/* Per-session frame review. `SessionReviewDrawer` is NOT one of wave R7's
          77 rebuilt presentation files and has no native equivalent under
          `next/` - the Archive sheet browses frames by NIGHT, not by session -
          so it is mounted unchanged rather than dropped. Named in this task's
          report for classification. */}
      <SessionReviewDrawer
        id={reviewId}
        onClose={() => { setReviewId(null); void refresh(); }}
      />
    </Card>
  );
}
