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
// AND THE CAPABILITY IS READ HERE, NOT TAKEN ON TRUST FROM THE CALLER. The
// `lockedReason` prop is the plan-wide sentence; it is not the only thing
// holding these five verbs. Mounted with `lockedReason={null}` - which is what
// the plan editor did - a viewer got a live DELETE that reached
// `DELETE /api/sessions/{id}` and learned the rule from a 403 after the confirm
// dialog. `verbsFor(card, useCanControlMount())` answers before the press, in
// the server's own words.
//
// ARMED AUTO-RESUME IS THE DANGEROUS STATE, so it keeps all three of the legacy
// warnings: no safety monitor, a cloud forecast that does NOT hold it, and an
// active weather override that suppresses the one forecast that would.

import { useCallback, useEffect, useRef, useState, type JSX } from "react";

import { setIgnoreTonight } from "../../../../../api/weather";
import { getSession, listSessions } from "../../../../../api/sessions";
import { sessionDates } from "../../../../../components/sequence/sessionDates";
import SessionReviewDrawer from "../../../../../components/sequence/SessionReviewDrawer";
import {
  accessPhrase, useCanControlCapture, useCanControlMount,
} from "../../../../../lib/caps";
import { targetProgress } from "../../../../../lib/sessions";
import { useSafety, useSequence, useStore, useWeather } from "../../../../../store";
import type { Session, SessionRow } from "../../../../../types";
import {
  ActionButton, Bar, Card, Label, LockNote, Mono, StatusPill, Switch,
} from "../../../../ui";
import {
  runAbandon, runAutoResume, runDelete, runResume, runUpdateFromPlan, verbsFor,
  type VerbId,
} from "../../gallery/cardActions";
import type { SessionCardData } from "../../gallery/useSessionCards";
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

/** How many rows get their frame ledger read for the per-target progress bars.
 *  `GET /api/sessions/{id}` carries every frame row of every night, so a rig
 *  with thirty sessions was firing thirty of those in parallel on every run-state
 *  edge. Beyond the cap the row still renders with its accepted/total tally from
 *  the index - the same bound, and the same reason, as the Gallery's
 *  `THUMB_RESOLVE_CAP`. */
export const SESSION_DETAIL_CAP = 12;

/** A ledger row in the shape the Gallery's verb table takes.
 *
 *  The alternative - a second per-status reason table here - is exactly the
 *  drift this file's header refuses: the plan editor would answer one thing
 *  about who may DELETE a session and the Gallery shelf another, about the same
 *  row and the same route. */
function asCard(r: SessionRow): SessionCardData {
  return {
    key: r.id,
    id: r.id,
    name: r.name,
    status: r.status,
    createdTs: r.created_ts,
    updatedTs: r.updated_ts,
    accepted: r.accepted,
    total: r.total,
    nights: r.nights,
    autoResume: r.auto_resume,
    integrationS: null,
    reportId: null,
  };
}

/** Why each verb on one row will not fire, most general first: the section-wide
 *  reason (the plan itself is read-only), then `verbsFor`'s capability and
 *  per-status sentences - which are the ones the server would answer with. */
export function rowVerbReasons(
  r: SessionRow, canControl: boolean, lockedReason: string | null,
): Record<VerbId, string | null> {
  const out = {} as Record<VerbId, string | null>;
  for (const v of verbsFor(asCard(r), canControl)) out[v.id] = lockedReason ?? v.reason;
  return out;
}

export function PlanSessionsSection({ lockedReason, onExplain }: PlanSectionProps): JSX.Element {
  const plan = useStore((s) => s.plan);
  const safety = useSafety();
  const sequence = useSequence();
  const weather = useWeather();
  const canControl = useCanControlMount();
  const canCapture = useCanControlCapture();
  const [rows, setRows] = useState<SessionRow[]>([]);
  const [details, setDetails] = useState<Record<string, Session>>({});
  const [reviewId, setReviewId] = useState<string | null>(null);

  // Which refresh is the current one. The detail reads are now sequential, so a
  // second refresh (a run started while the first was still walking the rows)
  // would otherwise finish underneath the newer one and publish an older ledger.
  const gen = useRef(0);

  const refresh = useCallback(async () => {
    const mine = ++gen.current;
    let all: SessionRow[];
    try {
      all = (await listSessions()).filter((r) => r.status !== "abandoned");
      if (gen.current !== mine) return;
      setRows(all);
    } catch {
      // Additive surface: a fetch failure leaves the ledger empty rather than
      // taking the plan editor down with it.
      return;
    }
    // CAPPED AND SEQUENCED, like `useSessionCards`. Each detail read is a whole
    // frame ledger - hundreds of rows on a multi-night session - and this ran
    // one per row, in parallel, on every `sequence.state` edge. Newest first,
    // one at a time, and the rows past the cap keep their index tally.
    const map: Record<string, Session> = {};
    for (const r of all.slice(0, SESSION_DETAIL_CAP)) {
      try {
        map[r.id] = await getSession(r.id);
      } catch {
        // One unreadable session costs its progress bars, not the ledger.
      }
      if (gen.current !== mine) return;
    }
    setDetails(map);
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

  /** The weather override is NOT one of the five session verbs: it writes
   *  `POST /api/weather/ignore-tonight`, which the server gates on
   *  `control.capture` (app.py:2603-2609). Naming the wrong capability in the
   *  lock note would send the reader to ask for the wrong role. */
  const ignoreWeatherReason = lockedReason
    ?? (canCapture ? null : `That needs ${accessPhrase("control.capture")}.`);

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
        // One capability answer per row, from the Gallery's own verb table.
        const why = rowVerbReasons(r, canControl, lockedReason);
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
                lockedReason={why.resume}
                onExplain={onExplain}
                onPress={() => void runResume(r.id, after)}
                data-testid={`plan-session-resume-${r.id}`}
              >
                resume
              </ActionButton>
              <ActionButton
                kind="ghost"
                lockedReason={why.update}
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
                lockedReason={why.abandon}
                onExplain={onExplain}
                onPress={() => void runAbandon(r.id, r.name, after)}
                data-testid={`plan-session-abandon-${r.id}`}
              >
                abandon
              </ActionButton>
              <ActionButton
                kind="danger"
                lockedReason={why.delete}
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
              lockedReason={why.autoResume}
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
                lockedReason={ignoreWeatherReason}
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
