// ConditionsScreen.tsx - WEATHER · CONDITIONS (plan section A.1).
//
// Reading order, top to bottom: the verdict and who said it, the standing
// high-cloud alert if there is one, what the sky is doing right now, what it is
// forecast to do for the next 24 h, the one override, and what the override
// actually overrides.
//
// THREE THINGS THIS SCREEN REFUSES TO PRETEND:
//
//  1. That the forecast arms a hold. It does not, and has not since the cloud
//     veto was removed (see verdict.ts's header for the file:line trail). The
//     copy says what the engine does.
//  2. That a stale forecast is a live one. `normalizeWeather` is fail-CLOSED at
//     45 minutes and the age leads the sub-line when it trips.
//  3. That naming Open-Meteo is attribution. CC BY 4.0 asks for a LINK, so the
//     provider chip is an anchor - the compliance, not decoration.

import { useEffect, useMemo, useState, type CSSProperties, type JSX } from "react";
import { api, ApiError } from "../../../../api";
import { getWeather, setIgnoreTonight } from "../../../../api/weather";
import { useStore, useWeather, useSeq, usePlan } from "../../../../store";
import { accessPhrase, useCan } from "../../../../lib/caps";
import { agoLabel, fmtHm, weatherSourceLabel } from "../../../../lib/weather";
import { useLock } from "../../../lib/gateHook";
import { nav, useRoute } from "../../../router";
import { ActionButton, BannerCard, Card, Label, Mono, Pill, Switch } from "../../../ui";
import type { MoonInfo, VisibilityNight, WeatherState } from "../../../../types";
import { CloudChart } from "./CloudChart";
import { ConditionsBand, NO_MOON_TARGET_HINT } from "./ConditionsBand";
import { useSlowClock } from "../slowClock";
import { contextTarget, fetchVisibility } from "./moon";
import {
  deriveVerdict, windowSamples, WEATHER_OFF_HINT, WEATHER_OFF_TITLE,
} from "./verdict";

/** The 409 the ignore-tonight route answers with when no night resolves for the
 *  site (`server-routes` section 3.12, `{code:"no_night"}`). */
export const NO_NIGHT_TOAST =
  "No night resolves for this site yet, so there is nothing to override. "
  + "Set the observing site first.";

export const IGNORE_LOCK_NOTE = `${accessPhrase("control.capture")} needed to override weather`;

/** GAP-ANALYSIS section 10 lists the fail-open veto as Missing. It is a
 *  sentence, not a control, and every clause in it was checked against
 *  `server/astrodeck/weather.py:685-726` on 2026-09-10 before it was written -
 *  see the report and verdict.ts. Telling an operator the veto fails open when
 *  it fails closed would be worse than saying nothing. */
export const VETO_NOTE =
  "Auto-resume asks the weather before it restarts a paused run, and what it asks "
  + "about is RAIN: forecast rain inside the next hour vetoes the restart. Cloud does "
  + "not - that gate was removed after it refused two nights that turned out clear, so "
  + "the forecast informs and the frames decide. When the forecast is stale or the feed "
  + "is down the answer is \"go\": the veto fails OPEN, so a dead feed never keeps the "
  + "rig parked all night. The safety monitor is the opposite - it fails closed, and an "
  + "unsafe reading still stops the run and parks.";

const DARK_NOTE_CAP = "tonight's dark window needs operator or admin access";
const DARK_NOTE_NONE = "no dark window resolved for this site, so this reads the whole 24 h";

/** Nearest hourly Astrospheric sample to now, or null past two hours away.
 *  Transcribed from `SkyConditionsPanel.tsx:72-90`. */
function nearestAstro(
  w: WeatherState, nowTs: number, key: "seeing" | "transparency",
): number | null {
  const a = w.astrospheric;
  if (!a || a.times.length === 0) return null;
  let best = -1;
  let bestD = Infinity;
  for (let i = 0; i < a.times.length; i++) {
    const t = Date.parse(a.times[i]) / 1000;
    const d = Math.abs(t - nowTs);
    if (Number.isFinite(t) && d < bestD) {
      bestD = d;
      best = i;
    }
  }
  if (best < 0 || bestD > 2 * 3600) return null;
  const v = a[key][best];
  return typeof v === "number" ? v : null;
}

export function ConditionsScreen(): JSX.Element {
  const weather = useWeather();
  const wsConnected = useStore((s) => s.wsConnected);
  const canSiteDerived = useCan("view.site_derived");
  const route = useRoute();
  const seq = useSeq();
  const plan = usePlan();
  const nowTs = useSlowClock();
  // ignore-tonight requires BOTH control.capture (the principal) and
  // view.weather (a dependency: the response echoes the full weather payload,
  // which carries site_lat/site_lon - server/astrodeck/api/app.py:2476-2481,
  // reasoned at :2462-2471). Declaring control.capture alone let a
  // control.capture holder without view.weather see this control as
  // unlocked. `gate.ts`'s `GateInput` takes ONE `cap` (do not widen it), so
  // this checks both locks and takes the first reason - the repo's pattern
  // for a two-capability control (`incidentActions.ts`'s `busyLane2`).
  const captureLock = useLock({ cap: "control.capture" });
  const weatherLock = useLock({ cap: "view.weather" });
  const ignoreLock = captureLock.lockedReason ?? weatherLock.lockedReason;
  const onExplain = captureLock.onExplain;
  // The same gate the hub header's gear carries, so both doors to the sheet
  // behave identically - one of them refusing while the other opens would read
  // as a bug in whichever one the operator pressed second.
  const { lockedReason: settingsLock } = useLock({ cap: "config.site_optics" });

  const [busy, setBusy] = useState(false);
  const [dark, setDark] = useState<{ start_iso: string; end_iso: string } | null>(null);
  const [darkAsked, setDarkAsked] = useState(false);
  const [moon, setMoon] = useState<MoonInfo | null>(null);

  // Cold load on mount and on every WS reconnect, routed through `handleEvent`
  // so the socket push, this GET and the ignore-tonight POST all apply the
  // slice the SAME way (SkyConditionsPanel.tsx:107-126). A second application
  // path is how two surfaces end up disagreeing about tonight.
  useEffect(() => {
    if (!wsConnected) return;
    let gone = false;
    void (async () => {
      try {
        const raw = await getWeather();
        if (gone) return;
        useStore.getState().handleEvent({
          type: "weather",
          data: raw as unknown as Record<string, unknown>,
          ts: Date.now() / 1000,
        });
      } catch {
        /* non-fatal - the WS keeps it fresh */
      }
    })();
    return () => { gone = true; };
  }, [wsConnected]);

  // Tonight's dark band. Cap-gated so a non-holder never issues a request it
  // would only eat a redaction for (App.tsx:507's idiom).
  useEffect(() => {
    if (!canSiteDerived) return;
    let gone = false;
    void (async () => {
      try {
        const sky = await api.get<{
          dark_window: { start_iso: string; end_iso: string } | null;
        }>("/api/site/sky");
        if (gone) return;
        setDark(sky.dark_window ?? null);
      } catch {
        if (!gone) setDark(null);
      } finally {
        if (!gone) setDarkAsked(true);
      }
    })();
    return () => { gone = true; };
  }, [canSiteDerived]);

  const target = useMemo(
    () => contextTarget(seq?.target ?? null, route.params, plan?.targets),
    [seq?.target, route.params, plan?.targets],
  );

  // The moon rides on the context target's ephemeris - there is no target-free
  // moon route. No target, no request (and the tile says so).
  useEffect(() => {
    if (!canSiteDerived || !target) { setMoon(null); return; }
    let gone = false;
    void fetchVisibility(target.ra_hours, target.dec_deg)
      .then((night: VisibilityNight) => { if (!gone) setMoon(night?.moon ?? null); })
      .catch(() => { if (!gone) setMoon(null); });
    return () => { gone = true; };
  }, [canSiteDerived, target]);

  const win = useMemo(
    () => (weather ? windowSamples(weather, nowTs) : null),
    [weather, nowTs],
  );

  const darkNote = canSiteDerived
    ? (darkAsked && !dark ? DARK_NOTE_NONE : null)
    : DARK_NOTE_CAP;

  const verdict = deriveVerdict({ weather, win, dark, darkNote, nowTs });

  const onIgnore = async (next: boolean): Promise<void> => {
    if (busy) return;
    setBusy(true);
    try {
      const raw = await setIgnoreTonight(next);
      useStore.getState().handleEvent({
        type: "weather",
        data: raw as unknown as Record<string, unknown>,
        ts: Date.now() / 1000,
      });
    } catch (e) {
      const title = e instanceof ApiError && e.status === 409
        ? NO_NIGHT_TOAST
        : (e instanceof Error ? e.message : "Could not change the weather override");
      useStore.getState().enqueueToast({ level: "error", title });
    } finally {
      setBusy(false);
    }
  };

  // ------------------------------------------------------------------ off
  if (verdict.kind === "off") {
    return (
      <div data-testid="wx-conditions" style={COL}>
        <Card tone="dashed" data-testid="wx-off">
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            <Label size={11}>WEATHER IS OFF</Label>
            <Mono size={11} tone="dim">{`${WEATHER_OFF_TITLE} ${WEATHER_OFF_HINT}`}</Mono>
            <Mono size={10} tone="dim">
              Nothing is fetched while it is off: no forecast, no radar tiles and no
              high-cloud warning.
            </Mono>
            <ActionButton
              kind="secondary"
              onPress={() => nav.sheet("weatherSettings")}
              lockedReason={settingsLock}
              onExplain={onExplain}
              data-testid="wx-off-cta"
            >
              WEATHER SETTINGS
            </ActionButton>
          </div>
        </Card>
      </div>
    );
  }

  const w = weather as WeatherState;
  const seeing = nearestAstro(w, nowTs, "seeing");
  const transparency = nearestAstro(w, nowTs, "transparency");
  const credits = w.astrospheric?.credits_used_today;

  return (
    <div data-testid="wx-conditions" style={COL}>
      {/* ---- verdict + who said it -------------------------------------- */}
      <div style={{
        display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 10,
      }}>
        <div style={{ display: "flex", flexDirection: "column", gap: 3, minWidth: 0 }}>
          <span
            className="nx-display"
            data-testid="wx-verdict"
            style={{
              fontWeight: 600, fontSize: 15, letterSpacing: ".1em",
              color: toneColor(verdict.tone),
            }}
          >
            {verdict.headline}
          </span>
          <Mono size={10} tone="dim">{verdict.sub}</Mono>
        </div>
        <Pill tone="dim" data-testid="wx-provider">
          {/* Open-Meteo's CC BY 4.0 terms ask for a LINK next to any location the
              data are displayed, not just the name. The anchor IS the compliance
              (SkyConditionsPanel.tsx:329-347), which is why it is an <a> and not
              a Pill with an onClick. */}
          <a
            href="https://open-meteo.com/"
            target="_blank"
            rel="noreferrer"
            style={{ textDecoration: "underline dotted", textUnderlineOffset: 2, color: "inherit" }}
          >
            {weatherSourceLabel(!!w.astrospheric)}
          </a>
          {` · ${agoLabel(w.fetched_ts, nowTs)}`}
        </Pill>
      </div>

      {credits != null && (
        <Mono size={10} tone="dim">{`astrospheric ${credits}/100 credits used today`}</Mono>
      )}

      {/* ---- the standing alert, inline. The one-shot dialog lives in the
              shell and fires on the edge; this is the steady state, and someone
              who dismissed the dialog at 20:00 has nothing else to read. ---- */}
      {w.alert && !w.ignore_tonight && (
        <BannerCard
          tone="warn"
          data-testid="wx-alert-card"
          text={
            <span>
              high cloud tonight - the forecast, not a hold
              <br />
              {`peak ${w.alert.peak_pct}% (${w.alert.dominant_layer} layer) `}
              {`${fmtHm(w.alert.start_iso)} - ${fmtHm(w.alert.end_iso)} · `}
              {"auto-resume is not blocked by cloud; the run's own frames decide"}
            </span>
          }
        />
      )}
      {w.ignore_tonight && (
        <BannerCard
          tone="warn"
          data-testid="wx-override-card"
          text="weather override active - auto-resume will restart through a rain forecast tonight"
        />
      )}

      {/* ---- what the sky is doing now ---------------------------------- */}
      <ConditionsBand
        now={w.now ?? null}
        seeing={seeing}
        transparency={transparency}
        moon={moon}
        moonTargetName={target?.name ?? null}
        moonReason={canSiteDerived ? NO_MOON_TARGET_HINT : "needs operator or admin access"}
      />

      {/* ---- what it is forecast to do ---------------------------------- */}
      <Card>
        {verdict.kind === "waiting" || verdict.kind === "nowindow" || !win ? (
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            <Label size={10}>CLOUD · NEXT 24 H</Label>
            <span data-testid="wx-chart-empty"><Mono size={11} tone="dim">{verdict.sub}</Mono></span>
          </div>
        ) : (
          <CloudChart
            win={win}
            thresholdPct={w.threshold_pct}
            sustainMinutes={w.sustain_minutes}
            dark={dark}
            nowTs={nowTs}
          />
        )}
      </Card>

      {/* ---- the one override, and what it overrides -------------------- */}
      <Card>
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <Switch
            checked={w.ignore_tonight}
            onChange={(v) => void onIgnore(v)}
            label="IGNORE WEATHER TONIGHT"
            note={w.ignore_tonight
              ? "rain veto disarmed until the next dusk · the safety monitor still stops the run"
              : "forecast rain inside the hour blocks an auto-resume · this lets it restart anyway tonight"}
            lockedReason={ignoreLock ?? (busy ? "one moment - the last change is still in flight" : null)}
            onExplain={onExplain}
            data-testid="wx-ignore"
          />
          {ignoreLock && (
            <span data-testid="wx-ignore-lock"><Mono size={10} tone="dim">{IGNORE_LOCK_NOTE}</Mono></span>
          )}
          <Mono size={10} tone="dim">{VETO_NOTE}</Mono>
        </div>
      </Card>
    </div>
  );
}

const COL: CSSProperties = {
  display: "flex", flexDirection: "column", gap: 10, padding: "0 2px 24px",
};

function toneColor(tone: string): string {
  if (tone === "good") return "var(--good)";
  if (tone === "bad") return "var(--bad)";
  if (tone === "warn") return "var(--warn)";
  return "var(--text-dim)";
}
