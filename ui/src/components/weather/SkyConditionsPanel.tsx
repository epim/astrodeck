// SkyConditionsPanel.tsx — Monitor-mosaic cloud forecast cell (weather spec
// §10). The CALLER mounts this ONLY for view.site_precise holders (conditional
// -mount idiom, SettingsView.tsx:140) — nothing weather-related renders for
// non-holders (spec §8). Holders with weather disabled get an empty state.
// Hand-rolled SVG time-series (GuideGraph/Sparkline precedent): series
// differentiated by dash pattern + stroke width + inline labels, NEVER hue
// alone (night rule, monitor.tsx:266-267).
import { useEffect, useMemo, useState } from "react";
import { useStore, useWeather } from "../../store";
import { Panel, Toggle } from "../ui";
import { Icon } from "../icons";
import { api } from "../../api";
import { getWeather, setIgnoreTonight } from "../../api/weather";
import { useCanControlCapture } from "../../lib/caps";
import { agoLabel, breachSpans, fmtHm, groupEndLabels } from "../../lib/weather";
import type { WeatherState } from "../../types";

// Minimum vertical gap (px) between two end-labels before they're treated as
// colliding and consolidated (groupEndLabels, spec §10/R2-WEA-01) — a bit
// taller than the 9px label font so two adjacent single-line labels never
// overprint.
const END_LABEL_MIN_GAP_PX = 9;

const W = 480;
const H = 150;
const PAD_L = 30;
const PAD_R = 40;
const PAD_T = 8;
const PAD_B = 16;
const PLOT_W = W - PAD_L - PAD_R;
const PLOT_H = H - PAD_T - PAD_B;
const HORIZON_S = 24 * 3600; // x-domain: now -> now + 24 h

interface Windowed {
  ts: number[]; // epoch seconds, ascending, within [now-450s, now+24h]
  cloud: number[];
  low: number[];
  mid: number[];
  high: number[];
}

function windowSamples(w: WeatherState, nowTs: number): Windowed {
  const out: Windowed = { ts: [], cloud: [], low: [], mid: [], high: [] };
  const f = w.forecast;
  if (!f) return out;
  for (let i = 0; i < f.times.length; i++) {
    const t = Date.parse(f.times[i]) / 1000;
    if (!Number.isFinite(t) || t < nowTs - 450 || t > nowTs + HORIZON_S) continue;
    out.ts.push(t);
    out.cloud.push(f.cloud[i]);
    out.low.push(f.cloud_low[i]);
    out.mid.push(f.cloud_mid[i]);
    out.high.push(f.cloud_high[i]);
  }
  return out;
}

function nearestAstro(
  w: WeatherState,
  nowTs: number,
  key: "seeing" | "transparency",
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

export default function SkyConditionsPanel() {
  const weather = useWeather();
  const wsConnected = useStore((s) => s.wsConnected);
  const showToast = useStore((s) => s.showToast);
  const canOperate = useCanControlCapture();
  const [busy, setBusy] = useState(false);
  const [darkWindow, setDarkWindow] = useState<
    { start_iso: string; end_iso: string } | null
  >(null);

  // Cold load on mount + on every WS reconnect (spec §9; holders only — the
  // caller already cap-gated this mount). Routed through handleEvent so the
  // WS path stays the single source of truth for how weather state applies.
  useEffect(() => {
    if (!wsConnected) return;
    let gone = false;
    void (async () => {
      try {
        const raw = await getWeather();
        if (!gone) {
          useStore.getState().handleEvent({
            type: "weather",
            data: raw as unknown as Record<string, unknown>,
            ts: Date.now() / 1000,
          });
        }
      } catch {
        /* non-fatal — WS events keep it fresh */
      }
    })();
    return () => {
      gone = true;
    };
  }, [wsConnected]);

  // Tonight's dark band (plan decision 4: /api/site/sky dark_window, the
  // existing display variant under view.status). Non-fatal: no band if absent.
  useEffect(() => {
    let gone = false;
    void (async () => {
      try {
        const sky = await api.get<{
          dark_window: { start_iso: string; end_iso: string } | null;
        }>("/api/site/sky");
        if (!gone) setDarkWindow(sky.dark_window ?? null);
      } catch {
        /* band simply not drawn */
      }
    })();
    return () => {
      gone = true;
    };
  }, []);

  const nowTs = Date.now() / 1000;
  const win = useMemo(
    () => (weather ? windowSamples(weather, nowTs) : null),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [weather],
  );

  const onIgnore = async (v: boolean) => {
    if (busy) return;
    setBusy(true);
    try {
      const raw = await setIgnoreTonight(v);
      useStore.getState().handleEvent({
        type: "weather",
        data: raw as unknown as Record<string, unknown>,
        ts: Date.now() / 1000,
      });
    } catch (e) {
      // 409 no_night etc. surface via the parsed ApiError message (B plumbing)
      showToast("error", e instanceof Error ? e.message : "ignore-tonight failed");
    } finally {
      setBusy(false);
    }
  };

  // ---- chart geometry (pure, from the windowed samples) ----
  const chart = useMemo(() => {
    if (!win || win.ts.length < 2 || !weather) return null;
    const t0 = nowTs;
    const sx = (t: number) => PAD_L + ((t - t0) / HORIZON_S) * PLOT_W;
    const sy = (pct: number) => PAD_T + (1 - pct / 100) * PLOT_H;
    const path = (vals: number[]) =>
      win.ts
        .map((t, i) => `${i === 0 ? "M" : "L"}${sx(t).toFixed(1)},${sy(vals[i]).toFixed(1)}`)
        .join(" ");
    // breach spans: the lib/weather.ts helper — SAME sustained rule as the
    // server, display-only (spec §10).
    const spans = breachSpans(win.cloud, weather.threshold_pct, weather.sustain_minutes);
    let dark: { x0: number; x1: number } | null = null;
    if (darkWindow) {
      const ds = Date.parse(darkWindow.start_iso) / 1000;
      const de = Date.parse(darkWindow.end_iso) / 1000;
      if (Number.isFinite(ds) && Number.isFinite(de) && de > t0 && ds < t0 + HORIZON_S) {
        dark = { x0: sx(Math.max(ds, t0)), x1: sx(Math.min(de, t0 + HORIZON_S)) };
      }
    }
    const ticks: { x: number; label: string }[] = [];
    for (let h = 0; h <= 24; h += 6) {
      const t = t0 + h * 3600;
      ticks.push({ x: sx(t), label: fmtHm(new Date(t * 1000).toISOString()) });
    }
    // End-of-series labels, collision-consolidated (lib/weather.ts) so
    // coincident series (e.g. all-zero cloud tonight) don't overprint.
    const endLabels = groupEndLabels(
      [
        { name: "total", y: sy(win.cloud[win.cloud.length - 1]) },
        { name: "low", y: sy(win.low[win.low.length - 1]) },
        { name: "mid", y: sy(win.mid[win.mid.length - 1]) },
        { name: "high", y: sy(win.high[win.high.length - 1]) },
      ],
      END_LABEL_MIN_GAP_PX,
    );
    return { sx, sy, path, spans, dark, ticks, endLabels };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [win, weather, darkWindow]);

  if (!weather || !weather.enabled) {
    return (
      <Panel className="col-span-full lg:col-span-6" title="Sky Conditions">
        <p className="text-dim text-xs py-6 text-center">
          Weather is off — enable it in Settings → Connect.
        </p>
      </Panel>
    );
  }

  const alert = weather.alert;
  const seeingNow = nearestAstro(weather, nowTs, "seeing");
  const transNow = nearestAstro(weather, nowTs, "transparency");

  return (
    <Panel className="col-span-full lg:col-span-6" title="Sky Conditions">
      <div className="data-dim flex flex-col gap-2">
        {chart && win ? (
          <svg
            viewBox={`0 0 ${W} ${H}`}
            className="w-full block"
            role="img"
            aria-label="Cloud cover forecast, next 24 hours"
          >
            {/* tonight's dark window (shaded band, spec §10) */}
            {chart.dark && (
              <rect
                x={chart.dark.x0}
                y={PAD_T}
                width={Math.max(0, chart.dark.x1 - chart.dark.x0)}
                height={PLOT_H}
                fill="var(--text-faint)"
                opacity={0.18}
              />
            )}
            {/* sustained-breach spans */}
            {chart.spans.map((s, i) => (
              <rect
                key={i}
                x={chart.sx(win.ts[s.start])}
                y={PAD_T}
                width={Math.max(1, chart.sx(win.ts[s.end]) - chart.sx(win.ts[s.start]))}
                height={PLOT_H}
                fill="var(--warn)"
                opacity={0.12}
              />
            ))}
            {/* threshold rule, labeled with the active hold policy (threshold +
                sustain minutes — same weather payload as breachSpans above,
                so the label can never drift from the rule it's drawn beside) */}
            <line
              x1={PAD_L}
              x2={W - PAD_R}
              y1={chart.sy(weather.threshold_pct)}
              y2={chart.sy(weather.threshold_pct)}
              stroke="var(--text-faint)"
              strokeDasharray="2 5"
            />
            <text
              x={PAD_L + 3}
              y={Math.max(PAD_T + 8, chart.sy(weather.threshold_pct) - 3)}
              fill="var(--text-faint)"
              fontSize={8}
              fontFamily="IBM Plex Mono"
            >
              hold ≥{weather.threshold_pct}% for {weather.sustain_minutes}m
            </text>
            {/* "now" cursor */}
            <line x1={PAD_L} x2={PAD_L} y1={PAD_T} y2={PAD_T + PLOT_H} stroke="var(--line-bright)" />
            {/* series: total solid/thick; low dotted; mid dashed; high
                long-dash + thicker. Dash + width + inline label — never hue
                alone (night rule). */}
            <path d={chart.path(win.cloud)} fill="none" stroke="var(--accent)" strokeWidth={1.8} />
            <path d={chart.path(win.low)} fill="none" stroke="var(--text-dim)" strokeWidth={1} strokeDasharray="1 3" />
            <path d={chart.path(win.mid)} fill="none" stroke="var(--text-dim)" strokeWidth={1} strokeDasharray="4 3" />
            <path d={chart.path(win.high)} fill="none" stroke="var(--text-dim)" strokeWidth={1.4} strokeDasharray="8 3" />
            {/* end labels: collision-consolidated (groupEndLabels) so coincident
                series never overprint — a group containing "total" stays
                accent-colored, otherwise dim (never hue-only: the dash
                pattern above is still the primary series distinguisher). */}
            {chart.endLabels.map((g) => (
              <text
                key={g.label}
                x={W - PAD_R + 3}
                y={g.y + 3}
                fill={g.label.split("+").includes("total") ? "var(--accent)" : "var(--text-dim)"}
                fontSize={9}
                fontFamily="IBM Plex Mono"
              >
                {g.label}
              </text>
            ))}
            <text x={2} y={PAD_T + 8} fill="var(--text-dim)" fontSize={9} fontFamily="IBM Plex Mono">100%</text>
            <text x={2} y={PAD_T + PLOT_H} fill="var(--text-dim)" fontSize={9} fontFamily="IBM Plex Mono">0%</text>
            {chart.ticks.map((t, i) => (
              <text key={i} x={t.x} y={H - 4} textAnchor="middle" fill="var(--text-faint)" fontSize={8} fontFamily="IBM Plex Mono">
                {t.label}
              </text>
            ))}
          </svg>
        ) : (
          <p className="text-dim text-xs py-6 text-center">
            no forecast yet — first fetch lands within a minute
          </p>
        )}

        {/* chips row (spec §10) */}
        <div className="flex items-center gap-2 flex-wrap text-[11px]">
          <span className={weather.stale ? "text-warn" : "text-dim"}>
            {weather.stale && <Icon name="alert" size={11} className="inline mr-1" />}
            {agoLabel(weather.fetched_ts, nowTs)}
          </span>
          {seeingNow !== null && (
            <span className="text-dim border border-line px-1.5 py-0.5">seeing {seeingNow}</span>
          )}
          {transNow !== null && (
            <span className="text-dim border border-line px-1.5 py-0.5">transparency {transNow}</span>
          )}
          {weather.astrospheric?.credits_used_today != null && (
            <span className="text-dim">
              astrospheric {weather.astrospheric.credits_used_today}/100 credits
            </span>
          )}
          {alert && !weather.ignore_tonight && (
            <span className="text-warn inline-flex items-center gap-1">
              <Icon name="alert" size={11} />
              high cloud tonight — auto-resume will hold unless overridden
            </span>
          )}
          {weather.ignore_tonight && (
            <span className="text-warn inline-flex items-center gap-1">
              <Icon name="alert" size={11} />
              weather override active — resume will ignore clouds tonight
            </span>
          )}
        </div>

        {/* ignore-tonight toggle (operator cap; B lock-note idiom otherwise) */}
        <label className="flex items-center justify-between gap-2 text-xs">
          <span className="text-dim">ignore weather tonight</span>
          <Toggle
            checked={weather.ignore_tonight}
            disabled={!canOperate || busy}
            onChange={(v) => void onIgnore(v)}
            label="Ignore weather tonight"
          />
        </label>
        {!canOperate && (
          <span className="text-[11px] text-dim inline-flex items-center gap-1">
            <Icon name="lock" size={11} />
            operator or admin access needed to override weather
          </span>
        )}
      </div>
    </Panel>
  );
}
