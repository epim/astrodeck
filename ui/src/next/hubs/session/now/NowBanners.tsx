// NowBanners.tsx - the two notices this screen owns (plan section A.14).
//
// GAP-ANALYSIS section 8 named both as missing, and both are about a rig that
// will start ON ITS OWN while nobody is standing next to it:
//
//   * AUTO-RESUME WITH NO SAFETY MONITOR. Nothing is watching the sky, and the
//     campaign is armed to start at dusk anyway. This one does NOT auto-dismiss
//     and has no X: it is a standing condition, not news, and it stops being
//     true when a monitor is connected or the arm is cleared.
//   * THE WEATHER NOTE. A high-cloud forecast alert never holds the resume by
//     itself - only forecast rain within the hour does (server: weather.py
//     WeatherService.veto_reason: "RAIN VETOES. CLOUD DOES NOT."). This states
//     that, or that the rain block is currently overridden until the next dusk.
//
// They live on this screen rather than in `shell/Banners.tsx` because the shell
// is another task's file; the shell's own strip already carries the link, the
// incident, the armed run and the report. Named in the report so the two can be
// folded together when the cross-hub task lands.
//
// A THIRD, AND THE ONE THAT COMES FIRST: THE RECOVERY LADDER (#246). After a
// restart ResumeArm blind-solves and re-centres the mount for minutes before
// it starts the armed session, with the engine idle all that time, so this
// screen said RUN ARMED over a rig that was slewing on its own. The banner is
// the server's session and step word, as sent (`resumeRecoveryLine`), and it
// sits at the top of Now, over the RUN ARMED card of the session it names. It
// is not gated on `idle`: a night that ended on a safety stop leaves the state
// on its terminal word, which is the night auto-resume picks back up. It is
// silent over a live run, which can only be the 20 s poll lagging the ladder's
// own start. No X, like the safety notice: it is true until the ladder returns.

import type { JSX } from "react";

import { resumeRecoveryLine } from "../../../../api/sessions";
import { useResumeArm, useSafety, useSeq, useStore, useWeather } from "../../../../store";
import { BannerCard } from "../../../ui";
import { nav } from "../../../router";
import { useActiveSession } from "./sessionData";

export const NO_SAFETY_WARNING =
  "auto-resume armed without a safety monitor - rig may start in bad weather";
export const WEATHER_VETO =
  "high cloud forecast tonight - it does not hold auto-resume; only forecast rain within the hour does";
export const WEATHER_OVERRIDE =
  "weather override active - forecast rain will not hold auto-resume until the next dusk (cloud forecasts never do)";

export function NowBanners(): JSX.Element | null {
  const resumeArm = useResumeArm();
  const seq = useSeq();
  const safety = useSafety();
  const weather = useWeather();
  const { row } = useActiveSession();
  const ignoreTonight = useStore((s) => s.weather?.ignore_tonight === true);

  const armed = resumeArm?.armed != null || row?.auto_resume === true;
  const recovering = resumeRecoveryLine(resumeArm, seq.state);
  const out: JSX.Element[] = [];

  if (recovering) {
    out.push(
      <BannerCard
        key="resume-recovering"
        tone="warn"
        text={recovering}
        data-testid="banner-resume-recovering"
      />,
    );
  }

  if (armed && safety?.connected === false) {
    out.push(
      <BannerCard
        key="no-safety"
        tone="warn"
        text={NO_SAFETY_WARNING}
        cta={{ label: "safety", onPress: () => nav.go("/rig/devices/safety") }}
        data-testid="banner-no-safety"
      />,
    );
  }

  if (armed && weather?.alert) {
    out.push(
      <BannerCard
        key="weather-veto"
        tone="info"
        text={ignoreTonight ? WEATHER_OVERRIDE : WEATHER_VETO}
        cta={{ label: "weather", onPress: () => nav.hub("weather") }}
        data-testid="banner-weather-veto"
      />,
    );
  }

  if (out.length === 0) return null;
  return (
    <div data-testid="now-banners" style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      {out}
    </div>
  );
}
