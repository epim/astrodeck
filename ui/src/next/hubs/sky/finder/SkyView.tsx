// SkyView.tsx - the finder box itself: every layer in the plan's A.4, in the
// prototype's own DOM order, back to front.
//
// This component is DUMB on purpose. It takes a `SkyModel` and draws it; it owns
// the pointer gestures, the wake lock and the `<video>` element's stream, and
// nothing else. Every number it prints was decided in `model.ts`, which is what
// keeps the reticle's verdict and the reach strip's verdict the same verdict.
//
// The geometry is inline style rather than `next.css` classes because it is
// per-pixel positioning computed at render time - a class cannot carry "this
// marker is at x=143.2", and the design's own prototype writes the same inline
// styles. Colours are design TOKENS (`var(--accent)`, `var(--bad)`) and not the
// hex literals the prototype used, because night mode swaps the tokens and a
// literal would stay cyan on a red-adapted screen (ARCHITECTURE section 6).
//
// The wake lock is held while the finder is mounted AND the phone is either
// showing the camera or following the compass - the two states where the user is
// holding the phone up at the sky and not touching it. It is deliberately NOT
// tied to a lock or to a running sequence: `useWakeLock` is fully guarded and a
// silent no-op where the API is missing, so it is safe to call unconditionally
// with a boolean.

import { useEffect, useRef } from "react";
import type { JSX, PointerEvent as RPointerEvent } from "react";
import { NxIcon } from "../../../icons";
import { useWakeLock } from "../../../../lib/useWakeLock";
import { useSkyGestures } from "./gestures";
import { LOCK_RADIUS_PX, type SkyModel } from "./model";

export interface SkyViewProps {
  model: SkyModel;
  /** Box width in px. The height follows the design's 370:372 ratio. */
  boxPx: number;
  frameOn: boolean;
  /** A marker was tapped. The finder also centres on it and draws its arc. */
  onLock?: (id: string) => void;
  /** The funnel button - the hub root owns the lens dial. */
  onOpenLens?: () => void;
  /** The layers button - the hub root owns the popover. */
  onOpenLayers?: () => void;
}

const MONO = "'IBM Plex Mono', ui-monospace, monospace";
const DISPLAY = "'Chakra Petch', system-ui, sans-serif";

export function SkyView({
  model,
  boxPx,
  frameOn,
  onLock,
  onOpenLens,
  onOpenLayers,
}: SkyViewProps): JSX.Element {
  const boxRef = useRef<HTMLDivElement | null>(null);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const ppRef = useRef(model.projector.pp);
  ppRef.current = model.projector.pp;
  const viewRef = useRef({ az: model.az, alt: model.alt });
  viewRef.current = { az: model.az, alt: model.alt };

  // Holding the phone up at the sky is exactly the case the screen timeout was
  // designed for and exactly the case it ruins.
  useWakeLock(model.mode === "cam" || model.gyro);

  const setView = model.setView;
  const gestures = useSkyGestures({
    boxRef,
    ppRef,
    viewRef,
    onPan: (next) => setView(next),
    enabled: !model.gyro,
  });

  useEffect(() => {
    const v = videoRef.current;
    if (!v) return;
    // `srcObject` is not an attribute, so React cannot set it - this is the one
    // imperative handoff the AR layer needs.
    if (v.srcObject !== model.cameraStream) {
      v.srcObject = model.cameraStream ?? null;
    }
  }, [model.cameraStream]);

  const W = model.boxW;
  const H = model.boxH;
  const cx = W / 2;
  const cy = H / 2;
  const stop = (e: RPointerEvent<HTMLElement>) => e.stopPropagation();

  return (
    <div
      data-sky-view
      ref={boxRef}
      role="application"
      aria-label="sky finder - drag to look around"
      style={{
        position: "relative",
        width: "100%",
        maxWidth: boxPx,
        height: H,
        flexShrink: 0,
        borderRadius: 16,
        overflow: "hidden",
        border: "1px solid var(--line)",
        background: "linear-gradient(180deg,#080b16 0%,#0c1226 55%,#121a33 100%)",
        touchAction: "none",
        cursor: "grab",
        userSelect: "none",
      }}
      onPointerDown={gestures.onPointerDown}
      onPointerMove={gestures.onPointerMove}
      onPointerUp={gestures.onPointerUp}
      onPointerCancel={gestures.onPointerCancel}
    >
      {/* ---------------------------------------------------- AR passthrough */}
      {model.mode === "cam" && (
        <>
          <video
            ref={videoRef}
            autoPlay
            muted
            playsInline
            data-sky-camera
            style={{ position: "absolute", inset: 0, width: "100%", height: "100%", objectFit: "cover" }}
          />
          <div
            style={{
              position: "absolute",
              inset: 0,
              boxShadow: "inset 0 0 80px rgba(0,0,0,.55)",
              pointerEvents: "none",
            }}
          />
        </>
      )}

      {/* ------------------------------------------------------- the MAP grid */}
      {model.mode === "map" && !frameOn && (
        <>
          <div style={{ position: "absolute", inset: 0, background: "var(--bg)" }} />
          {model.altLines.map((l) => (
            <div
              key={`alt-${l.label}`}
              style={{
                position: "absolute", left: 0, right: 0, top: l.y,
                borderTop: "1px dashed var(--line)",
              }}
            >
              <span
                style={{
                  position: "absolute", right: 8, top: 2,
                  fontFamily: MONO, fontSize: 10, color: "var(--text-faint)",
                }}
              >
                {l.label}
              </span>
            </div>
          ))}
          {model.ticks.map((t) => (
            <div
              key={`v-${t.x.toFixed(1)}`}
              style={{
                position: "absolute", top: 0, bottom: 0, left: t.x,
                borderLeft: "1px dashed var(--line)",
              }}
            />
          ))}
        </>
      )}

      {/* ------------------------------------------------------ horizon fill */}
      {!frameOn && model.horizonPath && (
        <svg
          viewBox={`0 0 ${W} ${H}`}
          preserveAspectRatio="none"
          aria-hidden="true"
          style={{ position: "absolute", inset: 0, width: "100%", height: "100%", display: "block", pointerEvents: "none" }}
        >
          <path
            d={model.horizonPath}
            // Through `style` and not the presentation attribute: color-mix is a
            // CSS value, and the tokens are what make this legible under night
            // mode instead of a fixed pink.
            style={{
              fill: "color-mix(in srgb, var(--bad) 14%, transparent)",
              stroke: "color-mix(in srgb, var(--bad) 70%, transparent)",
            }}
            strokeWidth={1.2}
            strokeDasharray="4 3"
          />
        </svg>
      )}

      {/* ------------------------------------------------------- cloud tiles */}
      {!frameOn && model.cloudTiles.length > 0 && (
        <svg
          viewBox={`0 0 ${W} ${H}`}
          preserveAspectRatio="none"
          aria-hidden="true"
          style={{ position: "absolute", inset: 0, width: "100%", height: "100%", display: "block", pointerEvents: "none" }}
        >
          {model.cloudTiles.map((c) => (
            <rect
              key={c.key}
              x={c.x}
              y={c.y}
              width={c.w}
              height={c.h}
              // The design's cloud grey through the dim-text token: a fixed
              // rgba(200,208,228) is the brightest thing on the night screen,
              // and cloud carries its state in the label and the density, not
              // in its hue.
              style={{
                fill: `color-mix(in srgb, var(--text-dim) ${(c.opacity * 100).toFixed(1)}%, transparent)`,
              }}
              data-cloud-tile
            />
          ))}
        </svg>
      )}
      {!frameOn &&
        model.cloudLabels.map((l) => (
          <div
            key={`cl-${l.key}`}
            style={{
              position: "absolute", left: l.x, top: l.y,
              transform: "translate(-50%,-50%)",
              padding: "2px 6px", borderRadius: 999,
              background: "color-mix(in srgb, var(--bg) 60%, transparent)",
              fontFamily: MONO, fontSize: 10, color: "var(--text-dim)",
              letterSpacing: ".12em", whiteSpace: "nowrap", pointerEvents: "none",
            }}
          >
            {l.text}
          </div>
        ))}

      {/* ---------------------------------------------------- target markers */}
      {model.markers.map((m) => (
        <button
          key={m.id}
          type="button"
          data-sky-marker={m.id}
          aria-label={`${m.name}, altitude ${m.altTag}, ${m.target.statusTxt}`}
          onPointerDown={stop}
          onClick={() => {
            setView({ az: m.target.azNow, alt: m.target.altNow, trackId: m.id });
            onLock?.(m.id);
          }}
          style={{
            position: "absolute", left: m.x, top: m.y,
            transform: "translate(-50%,-50%)",
            display: "flex", flexDirection: "column", alignItems: "center", gap: 5,
            cursor: "pointer", padding: 6, border: 0, background: "transparent",
          }}
        >
          <span
            style={{
              width: 14, height: 14, borderRadius: "50%",
              border: `1.5px solid ${m.color}`,
              boxShadow: `0 0 12px color-mix(in srgb, ${m.color} 40%, transparent)`,
            }}
          />
          <span
            style={{
              padding: "3px 8px", borderRadius: 999,
              background: "var(--bg-panel)", border: "1px solid var(--line)",
              fontFamily: DISPLAY, fontWeight: 600, fontSize: 10.5, letterSpacing: ".08em",
              color: m.color, whiteSpace: "nowrap",
              display: "flex", gap: 6, alignItems: "center",
            }}
          >
            <NxIcon name={model.kindIcon[m.kind]} size={11} />
            {m.name}
            <span style={{ fontFamily: MONO, fontWeight: 400, color: "var(--text-dim)" }}>
              {m.altTag}
            </span>
          </span>
        </button>
      ))}

      {/* ------------------------------------------------------ compass strip */}
      {!frameOn && (
        <div
          aria-hidden="true"
          style={{
            position: "absolute", left: 0, right: 0, top: 0, height: 30,
            background: "linear-gradient(color-mix(in srgb, var(--bg) 85%, transparent),transparent)",
            pointerEvents: "none",
          }}
        >
          {model.ticks.map((t) => (
            <div
              key={`t-${t.x.toFixed(1)}`}
              style={{
                position: "absolute", left: t.x, top: 4,
                transform: "translateX(-50%)",
                display: "flex", flexDirection: "column", alignItems: "center", gap: 2,
              }}
            >
              <div style={{ width: 1, height: t.h, background: "var(--line-bright)" }} />
              <div style={{ fontFamily: MONO, fontSize: 10, color: "var(--text-dim)", letterSpacing: ".08em" }}>
                {t.label}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* ------------------------------------------------------------- track */}
      {!frameOn && model.track && (
        <>
          <svg
            viewBox={`0 0 ${W} ${H}`}
            preserveAspectRatio="none"
            aria-hidden="true"
            style={{ position: "absolute", inset: 0, width: "100%", height: "100%", display: "block", pointerEvents: "none" }}
          >
            {model.track.segments.map((s, i) => (
              <polyline
                key={`seg-${i}-${s.color}`}
                points={s.points}
                fill="none"
                stroke={s.color}
                strokeWidth={2}
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeDasharray={s.dash}
                opacity={0.85}
                data-track-segment
              />
            ))}
            {model.track.dots.map((d, i) => (
              <circle
                key={`dot-${i}`}
                cx={d.x}
                cy={d.y}
                r={2.5}
                fill="var(--bg)"
                stroke="var(--text)"
                strokeWidth={1}
              />
            ))}
          </svg>
          {model.track.labels.map((l) => (
            <div
              key={`lbl-${l.label}`}
              style={{
                position: "absolute", left: l.x, top: l.y,
                transform: "translate(-50%,-130%)",
                padding: "1px 5px", borderRadius: 6,
                background: "color-mix(in srgb, var(--bg) 75%, transparent)",
                fontFamily: MONO, fontSize: 10, color: "var(--text-dim)",
                whiteSpace: "nowrap", pointerEvents: "none",
              }}
            >
              {l.label}
            </div>
          ))}
        </>
      )}

      {/* ----------------------------------------------------------- reticle */}
      {!frameOn && (
        <>
          <div
            aria-hidden="true"
            data-sky-reticle
            style={{
              position: "absolute", left: cx, top: cy,
              width: LOCK_RADIUS_PX, height: LOCK_RADIUS_PX,
              border: `1px dashed ${model.reticle.color}`,
              transform: "translate(-50%,-50%)",
              pointerEvents: "none", opacity: 0.6, borderRadius: 4,
            }}
          />
          {model.reticle.haveOptics && (
            <div
              aria-hidden="true"
              data-sky-fov
              style={{
                position: "absolute", left: cx, top: cy,
                width: model.reticle.w, height: model.reticle.h,
                border: `1.5px solid ${model.reticle.color}`,
                boxShadow: `0 0 14px ${model.reticle.glow}`,
                transform: `translate(-50%,-50%) rotate(${model.reticle.rotationDeg}deg)`,
                pointerEvents: "none",
              }}
            />
          )}
        </>
      )}

      {/* -------------------------------------------------------------- wind */}
      {!frameOn && model.windArrows.length > 0 && (
        <svg
          viewBox={`0 0 ${W} ${H}`}
          preserveAspectRatio="none"
          aria-hidden="true"
          style={{ position: "absolute", inset: 0, width: "100%", height: "100%", display: "block", pointerEvents: "none" }}
        >
          {model.windArrows.map((a) => (
            <g key={a.key} transform={`translate(${a.x} ${a.y}) rotate(${a.rot.toFixed(1)})`} data-wind-arrow={a.rot.toFixed(1)}>
              <path
                d="M-9 0H9M4 -4l5 4-5 4"
                fill="none"
                style={{ stroke: `color-mix(in srgb, var(--text-dim) ${a.op * 100}%, transparent)` }}
                strokeWidth={1.4}
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </g>
          ))}
        </svg>
      )}
      {!frameOn && model.windLine && (
        <div
          data-wind-pill
          style={{
            position: "absolute", left: 10, top: 38,
            maxWidth: "calc(100% - 130px)",
            display: "flex", alignItems: "center", gap: 6,
            padding: "3px 8px", borderRadius: 999,
            background: "color-mix(in srgb, var(--bg) 70%, transparent)", border: "1px solid var(--line)",
            fontFamily: MONO, fontSize: 10, color: "var(--text-dim)",
            pointerEvents: "none", whiteSpace: "nowrap",
            overflow: "hidden", textOverflow: "ellipsis",
          }}
        >
          <NxIcon name="wind" size={12} />
          {model.windLine}
        </div>
      )}

      {/* ------------------------------------------------- funnel and layers */}
      <button
        type="button"
        data-sky-lens
        aria-label="filter which kinds of target are shown"
        onPointerDown={stop}
        onClick={() => onOpenLens?.()}
        style={{
          position: "absolute", right: 10, top: 38, width: 44, height: 44,
          borderRadius: "50%",
          border: `1px solid ${model.lensHiddenCount > 0 ? "var(--warn)" : "var(--accent)"}`,
          background: "color-mix(in srgb, var(--bg) 80%, transparent)",
          color: model.lensHiddenCount > 0 ? "var(--warn)" : "var(--accent)",
          display: "flex", alignItems: "center", justifyContent: "center",
          cursor: "pointer", boxShadow: "0 0 14px color-mix(in srgb, var(--accent) 18%, transparent)",
        }}
      >
        <NxIcon name="funnel" size={20} />
        {model.lensHiddenCount > 0 && (
          <span
            style={{
              position: "absolute", right: -4, top: -4,
              minWidth: 18, height: 18, padding: "0 5px", borderRadius: 9,
              background: "var(--warn)", color: "var(--bg)",
              fontFamily: MONO, fontSize: 10,
              display: "flex", alignItems: "center", justifyContent: "center",
            }}
          >
            {model.lensHiddenCount}
          </span>
        )}
      </button>

      <button
        type="button"
        data-sky-layers
        aria-label="overlays: cloud, horizon, wind"
        onPointerDown={stop}
        onClick={() => onOpenLayers?.()}
        style={{
          position: "absolute", right: 10, top: 90, width: 44, height: 44,
          borderRadius: "50%",
          border: "1px solid var(--line-bright)",
          background: "color-mix(in srgb, var(--bg) 80%, transparent)",
          color: "var(--text-dim)",
          display: "flex", alignItems: "center", justifyContent: "center",
          cursor: "pointer",
        }}
      >
        <NxIcon name="layers" size={20} />
      </button>

      {/* ---------------------------------------------------------- readouts */}
      <div
        data-sky-readout
        style={{
          position: "absolute", left: 10, bottom: 8,
          padding: "4px 8px", borderRadius: 8, background: "color-mix(in srgb, var(--bg) 70%, transparent)",
          fontFamily: MONO, fontSize: 10, color: "var(--text-dim)",
          letterSpacing: ".06em", pointerEvents: "none",
        }}
      >
        az {model.azStr} · alt {model.altStr}
      </div>
      <div
        data-sky-locknote
        style={{
          position: "absolute", right: 10, bottom: 8,
          padding: "4px 8px", borderRadius: 8, background: "color-mix(in srgb, var(--bg) 70%, transparent)",
          fontFamily: MONO, fontSize: 10, color: model.reticle.color,
          letterSpacing: ".06em", pointerEvents: "none",
        }}
      >
        {model.lockNote}
      </div>
    </div>
  );
}
