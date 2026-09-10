// Banners.tsx - the notification strip under the header (README "Cross-hub
// chrome"): at most two, dismissible, the whole text is the CTA.
//
// Green is good news, cyan is tonight's information, amber is something wrong.
// The order below is a PRIORITY, not a layout: everything eligible is collected
// most-urgent-first and only the first two render, so a cloud hold can never be
// pushed off the screen by a report that is ready.
//
// This is where the legacy shell's three separate strips land: the connection
// banner (lib/connection.ts's pure `bannerState`), the ARMED-and-waiting banner
// and the SEQUENCE RUNNING banner from App.tsx. Their CONTENT is carried over
// verbatim in substance - a run that will start by itself is as much news as one
// already going, and dim text in the middle of an empty panel on one screen is
// not communication.
//
// Dismissals are per-session and keyed by the banner's IDENTITY, not its slot:
// dismissing "report 2026-09-09 is ready" must not also dismiss the next one.

import { useState, type JSX, type ReactNode } from "react";
import {
  useStore, useWsPhase, useTelemetryStale, useEquipConnected,
  useResumeArm, useArmedBannerDismissed, useRunBanner, useWeather, useLastReportId,
  useAuthMethods, usePrincipal,
} from "../../store";
import { bannerState, authRequiredForBanner } from "../../lib/connection";
import { fmtHm } from "../../lib/weather";
import { BannerCard } from "../ui";
import { nav, type Route } from "../router";
import { useIncidents } from "./useIncidents";

const MAX_BANNERS = 2;

interface Entry {
  /** Stable across renders while the SAME news is on screen, and different the
   *  moment it is different news. This is the dismissal key. */
  key: string;
  tone: "good" | "info" | "warn";
  text: ReactNode;
  cta?: { label: string; onPress: () => void };
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
  const weather = useWeather();
  const lastReportId = useLastReportId();

  const incidents = useIncidents(nowMs);
  const onSession = route.hub === "session";

  const entries: Entry[] = [];

  // 1. The link. `bannerState` is the same pure decision the legacy
  //    ConnectionBanner uses, so the two roots can never disagree about what
  //    "down" means. `view` is passed as the hub id: its only job in that
  //    function is to suppress the quiet no-rig note on the page where
  //    connecting IS the task, which is Rig here rather than "connect".
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
      tone: "warn",
      text: "Sign-in required. The rig is reachable but this browser has no session.",
    });
  } else if (conn === "down") {
    entries.push({
      key: "conn:down",
      tone: "warn",
      text: "No link to the rig. The session keeps running there; this view is the last state received.",
    });
  } else if (conn === "stale") {
    entries.push({
      key: "conn:stale",
      tone: "warn",
      text: "Telemetry has gone quiet. The socket is open and nothing has come down it.",
    });
  } else if (conn === "norig") {
    entries.push({
      key: "conn:norig",
      tone: "info",
      text: "Browsing. No rig connected - the sky, the dome and the list all work.",
      cta: { label: "set up", onPress: () => nav.go("/rig/devices") },
    });
  }

  // 2. An incident, while the user is somewhere other than the Session hub -
  //    the hub that already shows the incident card at full size. The link
  //    incident is suppressed when the connection banner is already saying it.
  const top = incidents.find((i) => !(i.kind === "link" && (conn === "down" || conn === "stale")));
  if (top && !onSession) {
    entries.push({
      key: `inc:${top.kind}:${top.sinceMs ?? 0}`,
      tone: "warn",
      text: <><b>{top.title}.</b> {top.engine}</>,
      cta: { label: "session", onPress: () => nav.go("/session/now") },
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
      tone: "info",
      text: <><b>{paused ? "SEQUENCE PAUSED" : "SEQUENCE RUNNING"}</b>{name}{pct}</>,
      cta: { label: "open live", onPress: () => nav.go("/session/now") },
    });
  }

  // 5. Last night's report. Good news, and the only banner that is.
  if (lastReportId) {
    entries.push({
      key: `report:${lastReportId}`,
      tone: "good",
      text: "Night report ready.",
      cta: { label: "open", onPress: () => nav.go(`/session/gallery/report?id=${encodeURIComponent(lastReportId)}`) },
    });
  }

  // 6. Tonight's sky. The design's line also carries the astronomical-dark time
  //    and the cooler start; neither is in the store today (the dark window
  //    comes back per-target from /api/visibility, which nothing caches), so
  //    this says only what it can actually read - the worst cloud coming and
  //    when - rather than printing a placeholder time.
  const fc = weather?.forecast;
  const peak = fc ? peakCloud(fc.times, fc.cloud, nowMs) : null;
  if (weather?.enabled && peak) {
    entries.push({
      key: `sky:${peak.at}:${Math.round(peak.pct)}`,
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
            // The two banners the store owns are dismissed THERE as well, or
            // the legacy root would keep showing what this one just cleared.
            if (e.key.startsWith("armed:")) dismissArmedBanner();
            if (e.key.startsWith("run:")) dismissRunBanner();
          }}
          data-testid={`banner-${e.key.split(":")[0]}`}
        />
      ))}
    </div>
  );
}
