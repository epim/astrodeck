// ============================================================================
// Monitor presentational cells (monitor spec §11). Lane 2E.
//
// Consumes ONLY landed primitives: components/icons.tsx (Icon + IconName),
// components/ui.tsx (Stat/Led/HoldButton/Panel), lib/eta.ts (formatters +
// constants), lib/stateMeta.ts (honest state map). NO lucide-react — the
// project's own icon set is the in-lane primitive (master §A reconciliation;
// stateMeta.ts already chose project icons over lucide).
//
// Normative perceptual rules (monitor spec §9 / §4): state/urgency NEVER by
// color alone — always glyph + word + (ring-fill / shape). All decorative motion
// is gated behind prefers-reduced-motion (read once here via matchMedia). Leaf 1s
// tickers (LiveTimer / CountdownTile) own their own interval so a per-second
// repaint touches only those digits, never the MonitorView grid (resolves G1).
// ============================================================================

import {
  memo,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { u } from "../lib/base";
import { Icon, type IconName } from "./icons";
import { HoldButton as UiHoldButton, Led, Stat } from "./ui";
import { TrendLine } from "./graphs";
import { stateMeta, type StateTone } from "../lib/stateMeta";
import {
  deriveFinish,
  fmtClock,
  fmtCountdown,
  fmtDuration,
  SPARKLINE_SCALE_ARCSEC,
} from "../lib/eta";
import { pickSeries, type LiveSample } from "../lib/reportChart";
import type { SequenceState } from "../types";
import { deriveHealthIssues, type HealthIssue } from "../lib/health";
// re-exported so existing imports of `deriveHealthIssues`/`HealthIssue` from
// "../components/monitor" (this module's canonical home for presentational
// cells) keep working; the pure logic itself lives in lib/health.ts (DOM-free,
// so it can run directly under `npx tsx` — this file eagerly touches `window`
// via lib/base's `u()`, which a plain node/tsx run has none of).
export { deriveHealthIssues, type HealthIssue };

// ----------------------------------------------------------------- motion gate
/** One reactive read of `prefers-reduced-motion`. Decorative motion (blink,
 *  LIVE pulse, sub-frame animation) is suppressed when the user asks for it; the
 *  numeric/glyph liveness channels always remain (monitor spec §9 "Motion"). */
export function useReducedMotion(): boolean {
  const [reduced, setReduced] = useState(() => {
    if (typeof matchMedia !== "function") return false;
    return matchMedia("(prefers-reduced-motion: reduce)").matches;
  });
  useEffect(() => {
    if (typeof matchMedia !== "function") return;
    const mq = matchMedia("(prefers-reduced-motion: reduce)");
    const on = () => setReduced(mq.matches);
    mq.addEventListener?.("change", on);
    return () => mq.removeEventListener?.("change", on);
  }, []);
  return reduced;
}

/** 1s wall-clock tick, local to a leaf so only that leaf repaints (resolves
 *  G1/G2). Returns Date.now() refreshed every second. */
function useNowTick(periodMs = 1000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), periodMs);
    return () => clearInterval(t);
  }, [periodMs]);
  return now;
}

// tone → text color class (Monitor's StateTone is wider than the design Tone).
const TONE_TEXT: Record<StateTone, string> = {
  good: "text-good",
  warn: "text-warn",
  bad: "text-bad",
  accent: "text-accent",
  dim: "text-dim",
};

// ============================================================ STATE BADGE
/** Sequence-state badge: Icon + the WORD always (never icon-only, never
 *  color-only). Blinks only for blinkable states when motion is allowed
 *  (resolves D3/E.1/E.2). */
export const StateBadge = memo(function StateBadge({
  state,
  reducedMotion,
}: {
  state: SequenceState["state"];
  reducedMotion?: boolean;
}) {
  const m = stateMeta(state);
  const blink = m.blinkable && !reducedMotion;
  return (
    <span
      className={`inline-flex items-center gap-1.5 text-xs font-display font-semibold
        tracking-[0.2em] uppercase ${TONE_TEXT[m.tone]} ${blink ? "blink" : ""}`}
      role="status"
    >
      <Icon name={m.icon} size={15} />
      {m.label}
    </span>
  );
});

// ============================================================ LIVE TIMER
/** Finish clock + relative remaining, both derived from ONE client clock
 *  (resolves C11/B4). `~`/"estimating…" until the server marks the ETA
 *  confident; "PAUSED — no ETA" when paused (a paused run has no honest finish).
 *  Finish clock is the largest header text (>=20px mono — accessibility-3). */
export const LiveTimer = memo(function LiveTimer({
  etaS,
  receivedAtMs,
  confident,
  paused,
}: {
  etaS?: number;
  receivedAtMs: number;
  confident?: boolean;
  paused?: boolean;
}) {
  const now = useNowTick();

  if (paused) {
    return (
      <div className="flex flex-col items-end leading-tight">
        <span className="mono text-[20px] text-warn tabular-nums">PAUSED</span>
        <span className="label !text-[10px]">no ETA while paused</span>
      </div>
    );
  }

  if (etaS == null) {
    return (
      <div className="flex flex-col items-end leading-tight">
        <span className="mono text-[20px] text-dim tabular-nums">—:—</span>
        <span className="label !text-[10px]">estimating…</span>
      </div>
    );
  }

  const { remainingS, finishAtMs } = deriveFinish(etaS, receivedAtMs, now);
  const lowConf = confident === false;
  const prefix = lowConf ? "~" : "";

  return (
    <div className="flex flex-col items-end leading-tight">
      <span
        className={`mono text-[20px] tabular-nums ${lowConf ? "text-dim" : "text-ink"}`}
        aria-label={`Finish at ${fmtClock(finishAtMs, now)}`}
      >
        {prefix}
        {fmtClock(finishAtMs, now)}
      </span>
      <span className={`label !text-[10px] ${lowConf ? "" : "text-dim"}`}>
        {lowConf ? "estimating…" : `in ${prefix}${fmtDuration(remainingS)}`}
      </span>
    </div>
  );
});

// ============================================================ HOLD BUTTON
/** Monitor-shaped Abort hold-button. Thin adapter over the canonical
 *  design-system HoldButton (master §A.0 — design-system OWNS it, everyone
 *  consumes). `label` is the canonical announce/a11y slot — the BARE verb
 *  phrase ("Abort sequence"); the design-system prepends "HOLD TO …" itself, so
 *  callers must NOT pass "Hold to …" (P2-6: that produced "HOLD TO HOLD TO …").
 *  `face` is the short resting button text ("Abort"). Maps
 *  {face,label,onConfirm,holdMs,danger} → the render-prop bind, drawing the
 *  ring-fill from `bind.progress`. >=48px, focus ring; the keyboard arm/confirm
 *  + aria-live live inside the design-system component. */
export function HoldButton({
  face,
  label,
  holdMs = 800,
  danger = true,
  onConfirm,
  hint,
  disabled,
}: {
  face: string; // short resting button text, e.g. "Abort"
  label: string; // bare announce verb phrase, e.g. "Abort sequence" (NO "Hold to")
  holdMs?: number;
  danger?: boolean;
  onConfirm: () => void;
  hint?: string; // optional sub-line, e.g. "link down — sending anyway"
  disabled?: boolean;
}) {
  return (
    <UiHoldButton onConfirm={onConfirm} label={label} holdMs={holdMs} disabled={disabled}>
      {(bind) => {
        // Persistent hold affordance (r1 backlog): a caller-supplied `hint` (e.g.
        // "link down") is more urgent and takes priority; otherwise, whenever the
        // button isn't mid-hold, default to a neutral "hold to X" caption so a
        // tap-and-release isn't the only way to discover this is a HOLD control.
        const caption = hint ?? (!bind.armed ? `hold to ${face.toLowerCase()}` : undefined);
        return (
          <button
            type="button"
            aria-label={bind["aria-label"]}
            onPointerDown={bind.onPointerDown}
            onPointerUp={bind.onPointerUp}
            onPointerCancel={bind.onPointerUp}
            onKeyDown={bind.onKeyDown}
            onKeyUp={bind.onKeyUp}
                  onBlur={bind.onBlur}
            disabled={disabled}
            className={`relative overflow-hidden inline-flex flex-col items-center justify-center
              min-h-[48px] min-w-[88px] px-4 select-none touch-none
              border ${danger ? "border-danger/55 text-danger" : "border-line2 text-ink"}
              bg-raise font-display font-semibold text-xs tracking-[0.14em] uppercase
              ${disabled ? "opacity-40 cursor-not-allowed" : "cursor-pointer"}`}
          >
            {/* ring-fill progress — the non-color hold signal (master §A.0) */}
            <span
              aria-hidden
              className="absolute inset-y-0 left-0 bg-danger/25 pointer-events-none transition-[width] duration-75"
              style={{ width: `${Math.round(bind.progress * 100)}%` }}
            />
            <span className="relative z-10 inline-flex items-center gap-1.5">
              <Icon name="stop" size={14} />
              {bind.armed ? bind.hintLabel : face}
            </span>
            {caption && (
              <span
                className={`relative z-10 label !text-[9px] !tracking-normal normal-case mt-0.5
                  ${hint ? "text-warn" : "text-dim"}`}
              >
                {caption}
              </span>
            )}
          </button>
        );
      }}
    </UiHoldButton>
  );
}

// ============================================================ PAUSE BUTTON
/** Plain (non-hold) Pause/Resume — pause is non-destructive (resolves A1/B5).
 *  >=44px, focus ring inherited from index.css. */
export function PauseButton({
  paused,
  onPause,
  onResume,
  disabled,
}: {
  paused: boolean;
  onPause: () => void;
  onResume: () => void;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={paused ? onResume : onPause}
      className={`btn ${paused ? "btn-accent" : ""} inline-flex items-center gap-1.5
        min-h-[44px] !px-4`}
      aria-label={paused ? "Resume sequence" : "Pause sequence"}
    >
      <Icon name={paused ? "play" : "pause"} size={14} />
      {paused ? "Resume" : "Pause"}
    </button>
  );
}

// ============================================================ SPARKLINE
const SPARK_W = 240;
const SPARK_H = 56;

/** Fixed-scale dual-axis guide sparkline (or single hfr trend). FIXED +-4"
 *  scale with clip indicators — no per-tick rescale that flattens spikes
 *  (resolves C2). RA vs DEC differentiated by dash + width + inline text labels,
 *  NOT color (night collapses accent ~= warn — resolves D10). Memoized. */
export const Sparkline = memo(function Sparkline({
  samples,
  scaleArcsec = SPARKLINE_SCALE_ARCSEC,
  mode = "guide",
}: {
  samples: { t: number; ra: number; dec: number }[] | number[];
  scaleArcsec?: number;
  mode?: "guide" | "trend";
}) {
  const paths = useMemo(() => {
    if (samples.length === 0) return null;

    const toX = (i: number, n: number) => (n <= 1 ? SPARK_W : (i / (n - 1)) * SPARK_W);

    if (mode === "trend") {
      const arr = samples as number[];
      const n = arr.length;
      // trend uses its own data-driven band (hfr px), not the arcsec guide scale.
      const max = Math.max(scaleArcsec, ...arr) || 1;
      const toY = (v: number) => SPARK_H - (Math.min(v, max) / max) * SPARK_H;
      let d = "";
      arr.forEach((v, i) => (d += `${i === 0 ? "M" : "L"}${toX(i, n).toFixed(1)} ${toY(v).toFixed(1)} `));
      return { trend: d.trim(), clipHi: false, clipLo: false };
    }

    const arr = samples as { t: number; ra: number; dec: number }[];
    const n = arr.length;
    const mid = SPARK_H / 2;
    const toY = (v: number) => mid - (Math.max(-scaleArcsec, Math.min(scaleArcsec, v)) / scaleArcsec) * mid;
    let clipHi = false;
    let clipLo = false;
    let ra = "";
    let dec = "";
    arr.forEach((s, i) => {
      if (s.ra > scaleArcsec || s.dec > scaleArcsec) clipHi = true;
      if (s.ra < -scaleArcsec || s.dec < -scaleArcsec) clipLo = true;
      const x = toX(i, n).toFixed(1);
      ra += `${i === 0 ? "M" : "L"}${x} ${toY(s.ra).toFixed(1)} `;
      dec += `${i === 0 ? "M" : "L"}${x} ${toY(s.dec).toFixed(1)} `;
    });
    return { ra: ra.trim(), dec: dec.trim(), clipHi, clipLo };
  }, [samples, scaleArcsec, mode]);

  if (!paths) {
    return <div className="text-dim text-xs py-4 text-center">no data</div>;
  }

  return (
    <svg
      viewBox={`0 0 ${SPARK_W} ${SPARK_H}`}
      preserveAspectRatio="none"
      className="w-full"
      style={{ height: SPARK_H }}
      role="img"
      aria-label={mode === "trend" ? "HFR trend" : "Guide error over time"}
    >
      {/* zero baseline (guide) */}
      {mode === "guide" && (
        <line x1={0} y1={SPARK_H / 2} x2={SPARK_W} y2={SPARK_H / 2} stroke="var(--line-bright)" strokeWidth={1} />
      )}
      {mode === "guide" ? (
        <>
          {/* RA = solid, thicker; DEC = dashed, thinner. Differentiated without color. */}
          <path d={paths.ra} fill="none" stroke="var(--accent)" strokeWidth={1.6} />
          <path d={paths.dec} fill="none" stroke="var(--text-dim)" strokeWidth={1} strokeDasharray="3 2" />
          {/* clip indicators (resolves C2) */}
          {paths.clipHi && <polygon points={`${SPARK_W - 6},2 ${SPARK_W - 1},8 ${SPARK_W - 11},8`} fill="var(--bad)" />}
          {paths.clipLo && (
            <polygon
              points={`${SPARK_W - 6},${SPARK_H - 2} ${SPARK_W - 1},${SPARK_H - 8} ${SPARK_W - 11},${SPARK_H - 8}`}
              fill="var(--bad)"
            />
          )}
        </>
      ) : (
        <path d={paths.trend} fill="none" stroke="var(--accent)" strokeWidth={1.4} />
      )}
    </svg>
  );
});

// ============================================================ LIVE TREND STRIP
/** Four-metric live in-acquisition strip (report viewer spec §3 Task 5): HFR /
 *  stars / Guide RMS / Sensor temp, each a `TrendLine` over the SAME generalized
 *  ring MonitorView keeps (`pushLiveSample`) — no separate/duplicate ring state.
 *  Each TrendLine self-renders "no data" when its series is empty, so a rig with
 *  no guiding still shows HFR/stars. */
export const LiveTrendStrip = memo(function LiveTrendStrip({ ring }: { ring: LiveSample[] }) {
  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
      <TrendLine values={pickSeries(ring, "hfr")} label="HFR" decimals={2} />
      <TrendLine values={pickSeries(ring, "stars")} label="stars" decimals={0} />
      <TrendLine values={pickSeries(ring, "rms")} label="Guide RMS" unit="″" />
      <TrendLine values={pickSeries(ring, "temp")} label="Sensor °C" unit="°C" decimals={1} />
    </div>
  );
});

// ============================================================ COUNTDOWN TILE
/** Single countdown primitive (Donut removed). Urgency by RING-FILL proportion +
 *  glyph + text, NOT a color ramp (night collapses colors — resolves D2/E.9).
 *  `seconds=null` => non-counting variants render their static label only. Leaf
 *  1s ticker. */
export function CountdownTile({
  label,
  seconds,
  warnAtS,
  totalS,
  dueLabel,
  variant,
  tone = "accent",
  reducedMotion,
  sub,
}: {
  label: string;
  seconds: number | null;
  warnAtS: number;
  totalS?: number; // ring denominator; defaults to a sane window
  dueLabel?: string;
  variant: "flip" | "cooling";
  tone?: StateTone;
  reducedMotion?: boolean;
  sub?: ReactNode;
}) {
  const now = useNowTick();
  // Anchor the countdown to the client clock so it ticks smoothly between the 2s
  // status polls (CountdownTile is re-mounted with a fresh `seconds` each poll).
  const anchoredRef = useRef<{ at: number; seconds: number } | null>(null);
  useEffect(() => {
    anchoredRef.current = seconds == null ? null : { at: Date.now(), seconds };
  }, [seconds]);

  const liveSeconds =
    seconds == null
      ? null
      : Math.max(0, (anchoredRef.current?.seconds ?? seconds) - (now - (anchoredRef.current?.at ?? now)) / 1000);

  const due = liveSeconds != null && liveSeconds <= 0;
  const warn = liveSeconds != null && liveSeconds <= warnAtS && !due;
  // ring fills as the remaining time shrinks against the window.
  const denom = totalS && totalS > 0 ? totalS : Math.max(warnAtS * 4, 3600);
  const frac = liveSeconds == null ? 0 : 1 - Math.min(1, liveSeconds / denom);
  const ringTone: StateTone = due ? "bad" : warn ? "warn" : tone;

  const R = 18;
  const C = 2 * Math.PI * R;
  const blink = (variant === "flip" && warn && !reducedMotion);

  return (
    <div className="flex items-center gap-3 min-w-0">
      <svg width={44} height={44} viewBox="0 0 44 44" aria-hidden className="shrink-0">
        <circle cx={22} cy={22} r={R} fill="none" stroke="var(--line-bright)" strokeWidth={3} />
        {liveSeconds != null && (
          <circle
            cx={22}
            cy={22}
            r={R}
            fill="none"
            stroke={`var(--${ringTone === "accent" ? "accent" : ringTone === "bad" ? "bad" : ringTone === "warn" ? "warn" : "good"})`}
            strokeWidth={3}
            strokeLinecap="round"
            strokeDasharray={C}
            strokeDashoffset={C * (1 - frac)}
            transform="rotate(-90 22 22)"
          />
        )}
      </svg>
      <div className="min-w-0 leading-tight">
        <div className="label !text-[10px] truncate">{label}</div>
        {liveSeconds == null ? (
          <div className={`text-xs ${TONE_TEXT[tone]} truncate`}>{sub}</div>
        ) : due ? (
          <div className={`inline-flex items-center gap-1 text-sm font-semibold text-bad ${blink ? "blink" : ""}`}>
            <Icon name="alert" size={14} />
            {dueLabel ?? "DUE"}
          </div>
        ) : (
          <>
            <div
              className={`mono text-base tabular-nums ${due ? "text-bad" : warn ? "text-warn" : "text-ink"} ${blink ? "blink" : ""}`}
            >
              {fmtCountdown(liveSeconds)}
            </div>
            {sub && <div className="label !text-[9px] text-dim truncate">{sub}</div>}
          </>
        )}
      </div>
    </div>
  );
}

// ============================================================ THERMOMETER BAR
/** Cooler power readout. With power: 0-100% bar + target + at-target check.
 *  Without (`canReportPower=false`): degrade to an ON/OFF Led + the WORD
 *  "ON"/"OFF" + target + "(no power readout)" — the word is required because the
 *  Led is color-only and red at night (resolves accessibility-13/crit2-A2).
 *  Memoized: its props derive from the 2s status poll, never the parent's 1s
 *  coarse tick, so the per-second repaint skips this cell entirely (P3-8). */
export const ThermometerBar = memo(function ThermometerBar({
  power,
  on,
  target,
  atTarget,
  canReportPower,
}: {
  power: number | null;
  on: boolean;
  target: number | null;
  atTarget: boolean;
  canReportPower: boolean;
}) {
  const targetStr = target != null ? `${target > 0 ? "+" : ""}${target}°C` : "—";

  if (!canReportPower || power == null) {
    return (
      <div className="flex items-center gap-2 min-w-0">
        <Led state={on ? "on" : "off"} label={on ? "cooler on" : "cooler off"} />
        <span className={`mono text-xs ${on ? "text-ink" : "text-dim"}`}>{on ? "ON" : "OFF"}</span>
        <span className="text-xs text-dim">→ {targetStr}</span>
        {atTarget && (
          <span className="inline-flex items-center gap-0.5 text-good text-xs">
            <Icon name="check" size={12} /> at target
          </span>
        )}
        <span className="label !text-[9px] ml-auto">(no power readout)</span>
      </div>
    );
  }

  const pct = Math.max(0, Math.min(100, power));
  return (
    <div className="flex flex-col gap-1 min-w-0">
      <div className="flex items-center justify-between text-xs">
        <span className="label !text-[10px]">cooler power</span>
        <span className="mono text-ink tabular-nums">{Math.round(pct)}%</span>
      </div>
      {/* reuse the canonical progress track so the night min-contrast top edge applies */}
      <div className="progress-track !h-2.5" role="meter" aria-valuenow={Math.round(pct)} aria-valuemin={0} aria-valuemax={100}>
        <div className="progress-fill" style={{ width: `${pct}%` }} />
      </div>
      <div className="flex items-center gap-2 text-xs text-dim">
        <span>→ {targetStr}</span>
        {atTarget && (
          <span className="inline-flex items-center gap-0.5 text-good">
            <Icon name="check" size={12} /> at target
          </span>
        )}
      </div>
    </div>
  );
});

// ============================================================ PREVIEW TILE

/**
 * WHICH ASSET THE LIVE TILE SHOWS, and why it is not the thumb (#221).
 *
 * Reported 2026-08-10: "the live view option while the plan runs shows a really
 * pixelated image — like a digital zoom, or an ultra-compressed thumbnail being
 * shown as the full image", and then the telling detail, "until I click on it
 * anyway".
 *
 * That is exactly what it was. The server publishes each frame at two sizes:
 *
 *     /api/preview/{id}            display bytes, to_jpeg max_width 1400 q85,
 *                                  retained PREVIEW_DISPLAY_KEEP = 8 frames
 *     /api/preview/{id}/thumb.jpg  to_thumb max_width 160 q70,
 *                                  retained PREVIEW_THUMB_KEEP = 50 frames
 *
 * This tile asked for the 160 px one and stretched it across a `w-full`
 * `aspect-[16/10]` box — around 700 px on a desktop, a 4x upscale of an image
 * that has 8.75x less resolution than the one sitting right beside it. Clicking
 * through to Capture opened PreviewStage, which uses the display bytes, so the
 * picture "fixed itself" on interaction.
 *
 * The thumb was not chosen carelessly: the original note says it keeps NINA
 * frames and evicted older frames live instead of 404-ing to STALE, and that is
 * a real property — only 8 display frames are kept against 50 thumbs. So the
 * order is inverted rather than the thumb removed. The newest frame, which is
 * what a LIVE tile shows, is always inside the display window; anything older
 * or backend-supplied falls back on 404 and behaves exactly as before.
 */
type PreviewTier = "display" | "thumb";

function previewUrlFor(id: number, tier: PreviewTier): string {
  return tier === "display"
    ? `/api/preview/${id}`
    : `/api/preview/${id}/thumb.jpg`;
}
/** Last-frame thumbnail. Double-buffered (no per-frame flash) with an onError
 *  guard (evicted/decoded-fail keeps the previous frame and flips to STALE,
 *  resolves F3). Per-tile night brightness dimmer (resolves D1, applied as an
 *  extra filter on top of img.astro). LIVE = a dim outline (not a glow). Real
 *  <button> for keyboard/SR (resolves accessibility-7). Server thumb route
 *  (/api/preview/{id}/thumb.jpg, same as the filmstrip) so NINA JPEG + evicted
 *  older frames stay live instead of 404→STALE on the .png compat route.
 *  Memoized so the parent's 1s coarse tick repaints this heavy cell only when a
 *  prop it actually depends on changes (live/stale flip, new frame, brightness),
 *  not every second — caller must pass useMemo-stable meta/clip (P3-8). */
export const PreviewTile = memo(function PreviewTile({
  previewId,
  live,
  stale,
  ageMs,
  hfr,
  stars,
  meta,
  clip,
  brightness,
  onBrightness,
  onOpen,
  hfrGood = 3,
  hfrWarn = 5,
  reducedMotion,
}: {
  previewId: number | null;
  live: boolean;
  stale: boolean;
  /** Age of the shown frame. Stale says HOW stale -- "STALE" alone cannot tell
   *  a frame 3 s past its window from one 40 minutes old. */
  ageMs?: number | null;
  hfr?: number;
  stars?: number;
  meta?: string;
  clip?: boolean;
  brightness: number;
  onBrightness: (v: number) => void;
  onOpen: () => void;
  hfrGood?: number;
  hfrWarn?: number;
  reducedMotion?: boolean;
}) {
  // Double buffer: track the id currently painted; only swap on a successful load
  // of the incoming id. On error we keep the old frame and report stale upward.
  const [shownId, setShownId] = useState<number | null>(previewId);
  const [loadError, setLoadError] = useState(false);
  // Which asset this frame is being fetched from. See PREVIEW_TIERS: the tile
  // asks for the FULL display bytes first and only falls back to the thumb,
  // rather than starting at the thumb and never leaving it (#221).
  const [tier, setTier] = useState<PreviewTier>("display");

  useEffect(() => {
    if (previewId == null) return;
    setLoadError(false);
    setTier("display");
  }, [previewId]);

  const showStale = stale || loadError;
  const showLive = live && !showStale;
  const hfrTone = hfr == null ? "" : hfr < hfrGood ? "text-good" : hfr < hfrWarn ? "text-warn" : "text-bad";

  return (
    <div className="flex flex-col gap-2 min-w-0">
      <button
        type="button"
        onClick={onOpen}
        aria-label="Open live preview in Capture"
        className="astro-surface group relative block w-full overflow-hidden border bg-black
          aspect-[16/10] cursor-pointer"
        style={{
          borderColor: showLive ? "var(--accent-dim)" : "var(--line)",
          boxShadow: showLive && !reducedMotion ? "0 0 0 1px var(--accent-dim)" : undefined,
        }}
      >
        {previewId == null ? (
          <span className="absolute inset-0 flex items-center justify-center text-dim text-xs tracking-[0.2em] uppercase">
            no frame yet
          </span>
        ) : (
          <>
            {/* Incoming buffer (hidden until it loads) drives the swap.
                DISPLAY BYTES FIRST, thumb only as a fallback — see
                PREVIEW_TIERS. */}
            <img
              key={`${previewId}:${tier}`}
              src={u(previewUrlFor(previewId, tier))}
              alt=""
              className="astro absolute inset-0 w-full h-full object-contain"
              style={{ filter: `brightness(${brightness})` }}
              onLoad={() => {
                setShownId(previewId);
                setLoadError(false);
              }}
              onError={() => {
                // A 404 here means the display bytes were evicted (only ~8 are
                // kept) or the backend never made any — the exact case the tile
                // used to start at the thumb for. Step down ONCE, then report.
                if (tier === "display") setTier("thumb");
                else setLoadError(true);
              }}
            />
            {/* fallback: last good frame stays visible if the new one failed */}
            {loadError && shownId != null && shownId !== previewId && (
              <img
                src={u(`/api/preview/${shownId}/thumb.jpg`)}
                alt=""
                className="astro absolute inset-0 w-full h-full object-contain"
                style={{ filter: `brightness(${brightness})` }}
              />
            )}
          </>
        )}

        {/* LIVE / STALE chip — tied to real frame age, not motion */}
        {previewId != null && (
          <span className="absolute top-1.5 left-1.5 preview-chip inline-flex items-center gap-1">
            <span
              className="w-1.5 h-1.5 rounded-full"
              style={{ background: showLive ? "var(--accent)" : "var(--warn)" }}
              aria-hidden
            />
            {showLive
              ? "LIVE"
              : (typeof ageMs === "number" && Number.isFinite(ageMs) && ageMs >= 0
                  ? `STALE (${Math.round(ageMs / 1000)}s)`
                  : "STALE")}
          </span>
        )}

        {/* clip chip (only when linear+clipping, gated by caller) */}
        {clip && (
          <span className="absolute top-1.5 right-1.5 preview-chip !text-bad inline-flex items-center gap-1">
            <Icon name="alert" size={10} /> CLIP
          </span>
        )}

        {/* HFR / stars / exposure chips, bottom-left */}
        {(hfr != null || stars != null || meta) && (
          <span className="absolute bottom-1.5 left-1.5 flex flex-wrap gap-1">
            {hfr != null && (
              <span className={`preview-chip inline-flex items-center gap-1 ${hfrTone}`}>
                HFR {hfr.toFixed(2)}
              </span>
            )}
            {stars != null && <span className="preview-chip">{stars}★</span>}
            {meta && <span className="preview-chip">{meta}</span>}
          </span>
        )}
      </button>

      {/* per-tile night brightness dimmer (ships day-one, persisted by caller) */}
      <label className="flex items-center gap-2 text-[10px]">
        <Icon name="moon" size={12} className="text-dim shrink-0" />
        <input
          type="range"
          min={0.2}
          max={1}
          step={0.05}
          value={brightness}
          onChange={(e) => onBrightness(Number(e.target.value))}
          className="dimmer flex-1"
          aria-label="Thumbnail brightness"
        />
        <span className="mono text-dim tabular-nums w-8 text-right">{Math.round(brightness * 100)}%</span>
      </label>
    </div>
  );
});

// ============================================================ METRIC STRIP
/** Compact label/value strip wrapping Stat — used for thermal + guide RMS rows.
 *  Pure layout, no state. */
export function MetricStrip({ children }: { children: ReactNode }) {
  return <div className="grid grid-cols-2 gap-x-4 gap-y-2 sm:grid-cols-3">{children}</div>;
}

// ============================================================ RMS WORD
/** RMS qualitative word + glyph — the accessible channel (resolves C3/D16).
 *  <1" good, 1-2" soft, >2" poor. Color is decorative reinforcement only. */
export function RmsVerdict({ rms, stale }: { rms: number | null | undefined; stale?: boolean }) {
  if (rms == null) return <Stat label="RMS" value={null} />;
  // UX-46: a frozen guider must not keep asserting a confident "good" — when the
  // reading is stale, downgrade the whole verdict to a neutral "stale".
  const tone: StateTone = stale ? "warn" : rms < 1 ? "good" : rms <= 2 ? "warn" : "bad";
  const word = stale ? "stale" : rms < 1 ? "good" : rms <= 2 ? "soft" : "poor";
  const glyph: IconName = stale ? "clock" : rms < 1 ? "check" : rms <= 2 ? "alert" : "x";
  return (
    <div className={`flex flex-col gap-0.5 min-w-0 ${stale ? "opacity-60" : ""}`}>
      <span className="label">RMS total</span>
      <span className={`inline-flex items-center gap-1 mono text-sm ${TONE_TEXT[tone]}`}>
        <Icon name={glyph} size={12} />
        {rms.toFixed(2)}″ <span className="text-xs">{word}</span>
      </span>
    </div>
  );
}

// ============================================================ HEALTH STRIP
// The single "is my night OK?" verdict (implementation brief §5 / doc 03 §5 /
// doc 04 §6 rec 1). `deriveHealthIssues` (the pure fold of safety/disk/
// backend_links/meridian/nina_link/status.providers/end_reason) lives in
// lib/health.ts — DOM-free, so it's unit-testable directly with `npx tsx`
// (re-exported above for existing/legacy imports from this module).

/** Renders `deriveHealthIssues` output: Tier-2 as sticky red banners (one per
 *  issue, never auto-dismissed — doc 03 §5), Tier-1 as a row of amber chips,
 *  Tier-0 (empty) as one calm ambient line. Shape+word carry every verdict
 *  (never color alone).
 *
 *  `weatherMonitored` (UX-2026-07-26 #28): the calm line used to read
 *  "✓ Night looks OK" unconditionally. With `weather.enabled === false` nothing
 *  polls the sky, so `deriveHealthIssues` CANNOT raise a weather issue — the
 *  reassurance was manufactured out of the absence of a check. When monitoring
 *  is off the line says what it is actually calm about, and offers the fix. It
 *  stays a Tier-0 ambient line (dim + info glyph), not a new alarm: an
 *  unmonitored sky is a normal, deliberate configuration for someone standing
 *  next to the scope. */
export function HealthStrip({
  issues,
  weatherMonitored = true,
  onEnableWeather,
}: {
  issues: HealthIssue[];
  weatherMonitored?: boolean;
  onEnableWeather?: () => void;
}) {
  const acts = issues.filter((i) => i.tier === 2);
  const notices = issues.filter((i) => i.tier === 1);

  if (acts.length === 0 && notices.length === 0) {
    if (!weatherMonitored) {
      return (
        <div className="flex items-center gap-1.5 flex-wrap text-xs text-dim" role="status">
          <Icon name="info" size={13} className="shrink-0" />
          <span>No faults reported - but weather monitoring is off, so nothing is watching the sky.</span>
          {onEnableWeather && (
            <button
              type="button"
              onClick={onEnableWeather}
              className="tap min-h-[44px] text-accent hover:underline"
            >
              Turn it on →
            </button>
          )}
        </div>
      );
    }
    return (
      <div className="flex items-center gap-1.5 text-xs text-dim" role="status">
        <Icon name="check" size={13} className="text-good shrink-0" />
        <span>Night looks OK</span>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-1.5">
      {acts.map((iss, i) => (
        <div
          key={`act-${i}`}
          role="alert"
          className="flex items-center gap-2 border border-bad/60 bg-bad/10 px-3 py-2 text-xs text-bad"
        >
          <Icon name={iss.icon} size={14} className="shrink-0" />
          <span className="font-semibold">{iss.text}</span>
        </div>
      ))}
      {notices.length > 0 && (
        <div className="flex items-center gap-1.5 flex-wrap" role="status">
          {notices.map((iss, i) => (
            <span
              key={`note-${i}`}
              className="inline-flex items-center gap-1.5 border border-warn/50 bg-warn/10 px-2 py-1 text-[11px] text-warn"
            >
              <Icon name={iss.icon} size={11} className="shrink-0" />
              {iss.text}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
