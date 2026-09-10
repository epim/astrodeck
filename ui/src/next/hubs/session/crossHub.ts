// crossHub.ts - the SESSION hub's answer to the three pieces of chrome that
// live on every OTHER hub (plan sections E.1-E.3).
//
// The shell owns the components (`shell/CampaignStrip.tsx`, `shell/Banners.tsx`,
// the tab dot). This module owns the DATA, so the derivation exists once: a
// strip that counted nights one way and a ledger card that counted them another
// is not a cosmetic disagreement - the campaign ledger is what an operator reads
// to decide whether tonight can be cut short.
//
// SO NOTHING HERE DERIVES A CAMPAIGN. `now/useCampaign.ts` already reads the
// `budget` array out of `GET /api/flows/{id}/tonight`, resolves which flow the
// campaign is, folds tonight's live share and projects the night count; that
// module's own header says the cross-hub strip is one of its consumers. This
// file reads it and adds only what the CHROME needs and the card does not: is
// the campaign running or parked, and which session RESUME would resume.
//
// PARKED NEEDS NO EXTRA REQUEST. `GET /api/sequence/resume-arm` returns the
// auto_resume-armed dormant session, and `SessionStore.armed()` IS
// "status == dormant and auto_resume" (`server/astrodeck/sequence/session.py:
// 302-308`) - which is exactly E.1's definition of parked. Asking a second
// route for the same fact would be a chance for the two to disagree.
//
// NOT YET WIRED. The shell currently renders `CampaignStrip` and `Banners` from
// its own narrow store reads; pointing it at these hooks is a later integration
// task (ARCHITECTURE.md section 14, wave 8). Until then these are the richer
// answers, tested on their own.

import { useCallback, useEffect, useState } from "react";

import { listReports } from "../../../api/reports";
import { fmtIntegration } from "../../../api/sessionStack";
import { fmtClock } from "../../../lib/eta";
import { runIsLive } from "../../../lib/lastSessionFrame";
import { useResumeArm, useLastReportId, useSeq, useWeather } from "../../../store";
import { nav, useRoute } from "../../router";
import { useIncidents } from "../../shell/useIncidents";
import { useCampaign } from "./now/useCampaign";
import { useActiveSession } from "./now/sessionData";

// ------------------------------------------------- the campaign, for chrome

export interface CampaignChrome {
  flowId: string;
  name: string;
  /** Distinct nights the ledger has recorded. */
  night: number;
  /** DERIVED - there is no persisted planned-night count on the server, so
   *  every string that prints it carries a tilde (plan deviation D2). */
  totalNights: number;
  bankedH: number;
  goalH: number;
  duskMs: number | null;
  darkStartMs: number | null;
  /** The run on the glass right now IS this campaign. */
  live: boolean;
  /** Dormant, armed for auto-resume, and nothing running: the day-time form. */
  parked: boolean;
  /** What RESUME would resume. `POST /api/sessions/{id}/resume` is a SESSION
   *  verb, not a flow one. */
  sessionId: string | null;
  /** The frozen plan's cooling settings, when the session is loaded. Only the
   *  live session loads one, so a parked campaign yields nulls and the banner's
   *  cooler clause is dropped rather than estimated. */
  coolToC: number | null;
  coolTimeoutS: number | null;
}

/** The one campaign read the chrome and the Flows list share. Null when there
 *  is no campaign - and no campaign is a real answer, not a loading state. */
export function useSessionCampaign(): CampaignChrome | null {
  const { campaign, night } = useCampaign();
  const { session } = useActiveSession();
  const seq = useSeq();
  const resumeArm = useResumeArm();

  if (!campaign) return null;

  const live = runIsLive(seq);
  const armed = resumeArm?.armed ?? null;
  // `armed` is already "dormant AND auto_resume" server-side; the only thing
  // left to check is that it is THIS campaign's session and that nothing is
  // running over it.
  const parked = !live && armed != null
    && armed.origin === "flow" && armed.origin_id === campaign.flowId;

  return {
    flowId: campaign.flowId,
    name: campaign.name,
    night: campaign.night,
    totalNights: campaign.totalNights,
    bankedH: campaign.bankedH,
    goalH: campaign.goalH,
    duskMs: night?.dusk_unix != null ? night.dusk_unix * 1000 : null,
    darkStartMs: night?.dark_start_unix != null ? night.dark_start_unix * 1000 : null,
    live,
    parked,
    sessionId: live ? (seq.session?.id ?? null) : (parked ? armed!.id : null),
    coolToC: session?.plan?.cool_to ?? null,
    coolTimeoutS: session?.plan?.cool_timeout_s ?? null,
  };
}

// ------------------------------------------------------------- E.1 the strip

export interface CampaignStripData {
  /** The whole line, minus the chevron: `CampaignStrip` draws its own, and a
   *  second one baked into the text would render twice. */
  line: string;
  tone: "accent2";
  onPress(): void;
}

/** The purple line under the banners, on every hub except Session - Now
 *  (plan E.1; README "Cross-hub chrome"). Null when there is no campaign, and
 *  null on the screen that IS the campaign. */
export function useCampaignStrip(): CampaignStripData | null {
  const route = useRoute();
  const camp = useSessionCampaign();
  const onPress = useCallback(() => { nav.go("/session/now"); }, []);

  if (!camp) return null;
  if (route.hub === "session" && route.sub === "now") return null;

  const line = camp.parked
    ? `CAMPAIGN · ${camp.name} · parked · resumes at dusk${
      camp.duskMs != null ? ` ${fmtClock(camp.duskMs)}` : ""}`
    : `CAMPAIGN · ${camp.name} · night ${camp.night} of ~${camp.totalNights}`
      + ` · ${camp.bankedH.toFixed(1)} of ${camp.goalH} h banked`;

  return { line, tone: "accent2", onPress };
}

// ---------------------------------------------------------------- E.2 the dot

/** The Session tab's pulsing dot, in the top incident's colour, while another
 *  hub is open. Never colour alone: `label` is the tab's `aria-label`. */
export function useSessionDot(nowMs: number = Date.now()): { color: string; label: string } | null {
  const route = useRoute();
  const incidents = useIncidents(nowMs);
  if (incidents.length === 0) return null;
  if (route.hub === "session") return null;
  return { color: incidents[0].color, label: `Session - ${incidents[0].title}` };
}

// ------------------------------------------------------------ E.3 the banners

export interface BannerSpec {
  id: string;
  tone: "good" | "info" | "warn";
  text: string;
  /** The label carries no chevron: `BannerCard` appends one. */
  cta?: { label: string; onPress: () => void };
  onDismiss: () => void;
}

/** The first sentence of a paragraph, for a banner that has one line to say
 *  what a whole incident card says. */
export function firstSentence(s: string): string {
  const m = /^[\s\S]*?[.!?](\s|$)/.exec(s);
  return (m ? m[0] : s).trim();
}

/** Worst total cloud between now and `untilMs`, or null when the feed has no
 *  forecast to read. A missing number is not "clear". */
function peakCloud(times: string[], cloud: number[], nowMs: number, untilMs: number): number | null {
  let best = -1;
  for (let i = 0; i < times.length && i < cloud.length; i++) {
    const t = Date.parse(times[i]);
    if (!Number.isFinite(t) || t < nowMs || t > untilMs) continue;
    if (cloud[i] > best) best = cloud[i];
  }
  return best >= 0 ? best : null;
}

/** At most three, most urgent first; the shell slices to two (plan E.3).
 *
 *  EVERY CLAUSE IS DROPPED RATHER THAN GUESSED. The prototype's banner is a
 *  fixture: it prints a cooler start and an astronomical-dark time from
 *  constants. Here each one appears only when something actually reports it. */
export function useSessionBanners(nowMs: number = Date.now()): BannerSpec[] {
  const route = useRoute();
  const incidents = useIncidents(nowMs);
  const camp = useSessionCampaign();
  const resumeArm = useResumeArm();
  const weather = useWeather();
  const lastReportId = useLastReportId();

  // In memory for this session, NOT localStorage: a re-armed session is new
  // news, and a dismissal that outlived the browser tab would hide it.
  const [dismissed, setDismissed] = useState<Record<string, true>>({});
  const dismiss = useCallback((id: string) => {
    setDismissed((d) => ({ ...d, [id]: true }));
  }, []);

  // One read of the report the store has just announced. `lastReportId` is the
  // id off the WS "report" event and carries no numbers, so the summary comes
  // from the list - one request per NEW report, never a poll.
  const [report, setReport] = useState<{ id: string; integration_s: number; frames_rejected: number } | null>(null);
  useEffect(() => {
    if (!lastReportId) { setReport(null); return; }
    let alive = true;
    listReports()
      .then((rows) => {
        if (!alive) return;
        const hit = rows.find((r) => r.id === lastReportId) ?? null;
        setReport(hit && {
          id: hit.id,
          integration_s: hit.integration_s,
          frames_rejected: hit.frames_rejected,
        });
      })
      .catch(() => { if (alive) setReport(null); });
    return () => { alive = false; };
  }, [lastReportId]);

  const out: BannerSpec[] = [];

  // 1. An incident, while the user is somewhere other than the Session hub -
  //    the hub that already shows the card at full size.
  const top = incidents[0];
  if (top && route.hub !== "session") {
    out.push({
      id: `inc-${top.kind}`,
      tone: "warn",
      text: `${top.title}. ${firstSentence(top.engine)}`,
      cta: { label: "session", onPress: () => nav.go("/session/now") },
      onDismiss: () => dismiss(`inc-${top.kind}`),
    });
  }

  // 2. Last night's report. The id makes a NEW report re-announce itself after
  //    the previous one was dismissed, matching `armedBannerDismissed`.
  if (report) {
    const id = `report-${report.id}`;
    out.push({
      id,
      tone: "good",
      text: `Night report ready. ${fmtIntegration(report.integration_s)} banked,`
        + ` ${report.frames_rejected} rejected.`,
      cta: { label: "open", onPress: () => nav.sheet("report", { id: report.id }) },
      onDismiss: () => dismiss(id),
    });
  }

  // 3. Tonight. Only when dusk is still ahead AND both halves are actually
  //    known - the cloud figure from the weather feed, the dark time from the
  //    campaign's own ephemeris payload. No dark time, no banner: a plausible
  //    21:12 from a default site is the failure this line exists to avoid.
  const darkMs = camp?.darkStartMs ?? null;
  const duskMs = camp?.duskMs ?? null;
  const fc = weather?.forecast;
  if (weather?.enabled && fc && darkMs != null && duskMs != null && duskMs > nowMs) {
    const pct = peakCloud(fc.times, fc.cloud, nowMs, darkMs + 6 * 3600 * 1000);
    if (pct != null) {
      // The cooler starts before dusk by the plan's own cool timeout - the only
      // derivable start time there is. Without a `cool_to` or without a
      // timeout, the clause is dropped rather than estimated.
      const coolerLine = camp && camp.coolToC != null && camp.coolTimeoutS
        ? ` · cooler starts ${fmtClock(duskMs - camp.coolTimeoutS * 1000, nowMs)}`
        : "";
      const resumeLine = resumeArm?.armed && camp?.parked
        ? " · the campaign resumes by itself."
        : "";
      out.push({
        id: "tonight",
        tone: "info",
        text: `Tonight: clear ${Math.max(0, 100 - Math.round(pct))}%`
          + ` · astronomical dark ${fmtClock(darkMs, nowMs)}${coolerLine}${resumeLine}`,
        cta: { label: "weather", onPress: () => nav.hub("weather") },
        onDismiss: () => dismiss("tonight"),
      });
    }
  }

  return out.filter((b) => !dismissed[b.id]).slice(0, 3);
}
