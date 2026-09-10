// Banners.tsx - the notification strip under the header (README "Cross-hub
// chrome"): at most two, dismissible, the whole text is the CTA.
//
// Green is good news, cyan is tonight's information, amber is something wrong.
// The order below is a PRIORITY, not a layout: everything eligible is collected
// most-urgent-first and only the first two render, so a cloud hold can never be
// pushed off the screen by a report that is ready.
//
// FOUR STRIPS LAND HERE, AND NONE OF THEM DERIVES ITS OWN NUMBERS.
//
//   1. The connection banner, from `lib/connection.ts`'s pure `bannerState` -
//      the same decision the legacy ConnectionBanner makes, so the two roots
//      cannot disagree about what "down" means.
//   2. The Session hub's own three (incident / report / tonight), from
//      `hubs/session/crossHub.ts`'s `useSessionBanners()`. The incident text is
//      the SAME derivation the Session card and the tab dot read; the report
//      line carries the integration and rejection counts, which need the report
//      list rather than the WS id alone.
//   3. The ARMED-and-waiting and SEQUENCE-RUNNING banners lifted from App.tsx.
//      A run that will start by itself is as much news as one already going.
//   4. The two notices the Now screen owned (`now/NowBanners.tsx`), which are
//      both about a rig that will start ON ITS OWN while nobody is next to it.
//      Those two still render on Session - Now from that component, so the copy
//      here is suppressed on that one route rather than printed twice.
//
// Dismissals are per-session and keyed by the banner's IDENTITY, not its slot:
// dismissing "report 2026-09-09 is ready" must not also dismiss the next one.

import { useState, type JSX, type ReactNode } from "react";
import {
  useStore, useWsPhase, useTelemetryStale, useEquipConnected,
  useResumeArm, useArmedBannerDismissed, useRunBanner, useSafety, useWeather,
  useAuthMethods, usePrincipal,
} from "../../store";
import { bannerState, authRequiredForBanner } from "../../lib/connection";
import { fmtHm } from "../../lib/weather";
import { BannerCard } from "../ui";
import { nav, type Route } from "../router";
import { useSessionBanners } from "../hubs/session/crossHub";

const MAX_BANNERS = 2;

interface Entry {
  /** Stable across renders while the SAME news is on screen, and different the
   *  moment it is different news. This is the dismissal key. */
  key: string;
  /** What KIND of news it is, for the test id. Separate from the key, which
   *  carries the identity (`report-2026-09-09`) that makes a NEW report
   *  re-announce itself after the last one was dismissed. */
  kind: string;
  tone: "good" | "info" | "warn";
  text: ReactNode;
  cta?: { label: string; onPress: () => void };
  /** The owner's own dismissal, for the entries whose state lives elsewhere. */
  onOwnerDismiss?: () => void;
}

/** Worst total cloud in the next 12 hours, and when. Null when the feed has no
 *  forecast to read - a missing number is not "clear". */
function peakCloud(times: string[], cloud: number[], nowMs: number): { pct: number; at: string } | null {
  let best = -1;
  let at = "";
  const horizonMs = nowMs + 12 * 3600 * 1000;
  for (let i = 0; i < times.length && i < cloud.length; i++) {
    const t = Date.parse(times[i]);
    if (!Number.isFinite(t) || t < nowMs || t > horizonMs) continue;
    if (cloud[i] > best) { best = cloud[i]; at = times[i]; }
  }
  return best >= 0 ? { pct: best, at } : null;
}

/** The engine's auto-resume weather gate is RAIN-ONLY and fail-open
 *  (`server/astrodeck/weather.py` `veto_reason`): cloud informs, it no longer
 *  gates, because a forecast over a ~10 km cell refused two clear nights in
 *  August. So this banner states what an armed resume with no safety monitor
 *  actually means, and does NOT repeat the old "auto-resume will hold" line. */
const NO_SAFETY_TEXT =
  "Auto-resume is armed with no safety monitor connected - nothing on the rig "
  + "is watching for rain.";
const WEATHER_ALERT_TEXT =
  "High cloud in tonight's forecast. It does not hold the resume - a run holds "
  + "on what its own frames show.";
const WEATHER_OVERRIDE_TEXT =
  "Weather override on for tonight: the in-run cloud hold is off as well as the "
  + "forecast.";

export function Banners({ route, nowMs }: { route: Route; nowMs: number }): JSX.Element | null {
  const [dismissed, setDismissed] = useState<Record<string, true>>({});

  const wsPhase = useWsPhase();
  const telemetryStale = useTelemetryStale();
  const equipConnected = useEquipConnected();
  const authMethods = useAuthMethods();
  const principal = usePrincipal();

  const resumeArm = useResumeArm();
  const armedDismissed = useArmedBannerDismissed();
  const dismissArmedBanner = useStore((s) => s.dismissArmedBanner);
  const runBanner = useRunBanner();
  const dismissRunBanner = useStore((s) => s.dismissRunBanner);
  const sequence = useStore((s) => s.sequence);
  const safety = useSafety();
  const weather = useWeather();
  const weatherIgnored = useStore((s) => s.weather?.ignore_tonight === true);

  const sessionBanners = useSessionBanners(nowMs);
  const onSession = route.hub === "session";
  // `now/NowBanners.tsx` still renders the auto-resume pair on the Now screen
  // itself. Two announcements of one condition is how dismissing one of them
  // reads as a bug.
  const onSessionNow = onSession && route.sub === "now";

  const entries: Entry[] = [];

  // 1. The link. `view` is passed as the hub id: its only job in that function
  //    is to suppress the quiet no-rig note on the page where connecting IS the
  //    task, which is Rig here rather than "connect".
  const conn = bannerState({
    phase: wsPhase,
    stale: telemetryStale,
    equipConnected,
    view: route.hub === "rig" ? "connect" : route.hub,
    authRequired: authRequiredForBanner(authMethods, principal),
  });
  if (conn === "signin") {
    entries.push({
      key: "conn:signin",
      kind: "conn",
      tone: "warn",
      text: "Sign-in required. The rig is reachable but this browser has no session.",
    });
  } else if (conn === "down") {
    entries.push({
      key: "conn:down",
      kind: "conn",
      tone: "warn",
      text: "No link to the rig. The session keeps running there; this view is the last state received.",
    });
  } else if (conn === "stale") {
    entries.push({
      key: "conn:stale",
      kind: "conn",
      tone: "warn",
      text: "Telemetry has gone quiet. The socket is open and nothing has come down it.",
    });
  } else if (conn === "norig") {
    entries.push({
      key: "conn:norig",
      kind: "conn",
      tone: "info",
      text: "Browsing. No rig connected - the sky, the dome and the list all work.",
      cta: { label: "set up", onPress: () => nav.go("/rig/devices") },
    });
  }

  // 2. The incident, from the Session hub's fold. It is already suppressed on
  //    the Session hub (the card is there at full size); what this adds is the
  //    LINK suppression, because a link incident under a connection banner
  //    saying the same thing is one piece of news taking both slots.
  const incident = sessionBanners.find((b) => b.id.startsWith("inc-")) ?? null;
  const linkAlreadySaid = conn === "down" || conn === "stale";
  if (incident && !(incident.id === "inc-link" && linkAlreadySaid)) {
    entries.push({
      key: incident.id,
      kind: "inc",
      tone: incident.tone,
      text: incident.text,
      cta: incident.cta,
      onOwnerDismiss: incident.onDismiss,
    });
  }

  // 3. A run that is armed and waiting. From App.tsx: nothing is moving, so the
  //    tone is amber rather than accent, and the CTA changes to SEE WHY when
  //    something is holding it, because "open live" on a run that has not
  //    started is an instruction to go and watch nothing happen.
  const armed = resumeArm?.armed;
  if (armed && !runBanner?.active && !onSession && armedDismissed !== armed.id) {
    const owed = armed.owed > 0 ? ` - ${armed.owed} frame${armed.owed === 1 ? "" : "s"} owed` : "";
    const hold = resumeArm?.hold ? ` - holding: ${resumeArm.hold.reason}` : "";
    entries.push({
      key: `armed:${armed.id}`,
      kind: "armed",
      tone: "warn",
      text: <><b>RUN ARMED</b> - {armed.name}{owed}{hold}</>,
      cta: {
        label: resumeArm?.hold ? "see why" : "open live",
        onPress: () => nav.go("/session/now"),
      },
    });
  }

  // 4. A run in progress, while the user is on another hub.
  if (runBanner?.active && !onSession) {
    const paused = sequence.state === "paused";
    const pct = typeof runBanner.percent === "number" ? ` - ${Math.round(runBanner.percent)}%` : "";
    const name = runBanner.plan_name ? ` - ${runBanner.plan_name}` : "";
    entries.push({
      key: `run:${runBanner.plan_name ?? ""}:${paused ? "paused" : "running"}`,
      kind: "run",
      tone: "info",
      text: <><b>{paused ? "SEQUENCE PAUSED" : "SEQUENCE RUNNING"}</b>{name}{pct}</>,
      cta: { label: "open live", onPress: () => nav.go("/session/now") },
    });
  }

  // 5. Auto-resume armed with nothing watching the sky. A STANDING CONDITION,
  //    not news: it stops being true when a monitor is connected or the arm is
  //    cleared, which is why it carries no dismiss.
  const autoArmed = resumeArm?.armed != null;
  if (autoArmed && safety?.connected === false && !onSessionNow) {
    entries.push({
      key: "arm:nosafety",
      kind: "no-safety",
      tone: "warn",
      text: NO_SAFETY_TEXT,
      cta: { label: "safety", onPress: () => nav.go("/rig/devices/safety") },
    });
  }

  // 6. The forecast, while something is armed to start by itself.
  if (autoArmed && weather?.alert && !onSessionNow) {
    entries.push({
      key: `arm:weather:${weatherIgnored ? "override" : "alert"}`,
      kind: "weather-veto",
      tone: "info",
      text: weatherIgnored ? WEATHER_OVERRIDE_TEXT : WEATHER_ALERT_TEXT,
      cta: { label: "weather", onPress: () => nav.hub("weather") },
    });
  }

  // 7. Last night's report, and tonight's sky - both from the Session fold,
  //    which carries the numbers (banked integration, rejected frames, the
  //    astronomical-dark time) that this file has no way to read.
  let toldTonight = false;
  for (const b of sessionBanners) {
    if (b.id.startsWith("inc-")) continue;
    if (b.id === "tonight") toldTonight = true;
    entries.push({
      key: b.id,
      kind: b.id.split("-")[0],
      tone: b.tone,
      text: b.text,
      cta: b.cta,
      onOwnerDismiss: b.onDismiss,
    });
  }

  // 8. Tonight, when the fold above could not say it. Its line needs the
  //    astronomical-dark time, which arrives only with a CAMPAIGN's ephemeris
  //    payload - so without a campaign there would be no sky line at all. This
  //    is the shorter honest form: the worst cloud coming, and when. Not a
  //    second opinion - it never runs while the fuller line is up.
  const fc = weather?.forecast;
  const peak = !toldTonight && fc ? peakCloud(fc.times, fc.cloud, nowMs) : null;
  if (weather?.enabled && peak) {
    entries.push({
      key: `sky:${peak.at}:${Math.round(peak.pct)}`,
      kind: "sky",
      tone: "info",
      text: `Tonight: clear ${Math.max(0, 100 - Math.round(peak.pct))}% at worst, around ${fmtHm(peak.at)}.`,
      cta: { label: "weather", onPress: () => nav.go("/weather/conditions") },
    });
  }

  const shown = entries.filter((e) => !dismissed[e.key]).slice(0, MAX_BANNERS);
  if (shown.length === 0) return null;

  return (
    <div className="nx-banners" data-testid="banners">
      {shown.map((e) => (
        <BannerCard
          key={e.key}
          tone={e.tone}
          text={e.text}
          cta={e.cta}
          onDismiss={() => {
            setDismissed((d) => ({ ...d, [e.key]: true }));
            // The banners whose dismissal state lives elsewhere are dismissed
            // THERE as well, or the other reader would keep showing what this
            // one just cleared.
            e.onOwnerDismiss?.();
            if (e.key.startsWith("armed:")) dismissArmedBanner();
            if (e.key.startsWith("run:")) dismissRunBanner();
          }}
          data-testid={`banner-${e.kind}`}
        />
      ))}
    </div>
  );
}
