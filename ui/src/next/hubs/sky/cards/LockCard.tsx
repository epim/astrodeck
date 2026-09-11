// LockCard.tsx - what the reticle is on, and the three things you can do with it
// (hub-sky plan A.7).
//
// The card is the whole point of the finder: everything above it exists to put
// one object in the middle of the reticle, and this is where that object becomes
// a night. Three actions, in the order the design puts them:
//
//   IMAGE <name>    a quick session - the flow that runs until dawn
//   Single frame    one exposure, aimed here, no flow
//   + PLAN          add to tonight's pool. Once THIS target is in a pool of two
//                   or more the button stops toggling and opens the multi-target
//                   sheet instead, so an x appears beside it to take it back out
//                   (see `planLabel` / `showRemove` for the argument).
//
// EVERY ONE OF THEM IS HONEST-DISABLED RATHER THAN HIDDEN. A viewer sees the
// same three buttons an operator does, dimmed, each carrying the reason. That is
// non-negotiable 6, and it is also the only way a shared rig is legible: the
// person who cannot press IMAGE still needs to know what would happen if they
// could.
//
// The primary CTA is a bespoke two-line button rather than `ActionButton`
// because the design gives it a label AND a plan summary ("2h · 4 filters ·
// finishes 00:14"), and a button that says what it will queue is the difference
// between pressing it and guessing. It uses the library's own honest-disabled
// idiom (`lockedAttrs`/`lockedClass`/`honestPress`), so the aria and title
// contract is identical to every primitive's.

import type { JSX } from "react";
import { Card, Mono, honestPress, lockedAttrs, lockedClass } from "../../../ui";
import { NxIcon } from "../../../icons";
import { SkyGlyph } from "./glyphs";
import { KIND_ICON, windowLabel, type SkyTarget } from "../finder";
import { lockCta, type LockCta } from "./lockCta";

const MONO = "'IBM Plex Mono', ui-monospace, monospace";
const DISPLAY = "'Chakra Petch', system-ui, sans-serif";

/** Border / fill / text for the five CTA cases. `danger` is the obstructed
 *  outline, `secondary` covers both CONNECT and CLOUDED - the primitives library
 *  has no warn kind, and the amber is not what carries the meaning: the label
 *  "CLOUDED NOW · IMAGE ANYWAY" is. */
function ctaSkin(cta: LockCta): { border: string; bg: string; color: string; glow?: string } {
  if (cta.button === "danger") return { border: "var(--bad)", bg: "transparent", color: "var(--bad)" };
  if (cta.kind === "clouded") {
    return {
      border: "var(--warn)",
      bg: "color-mix(in srgb, var(--warn) 14%, transparent)",
      color: "var(--warn)",
    };
  }
  if (cta.button === "secondary") {
    return { border: "var(--line-bright)", bg: "var(--bg-raise)", color: "var(--text)" };
  }
  return {
    border: "var(--accent)",
    bg: "var(--accent)",
    color: "var(--bg)",
    glow: "0 0 18px rgba(0,210,255,.35)",
  };
}

export interface LockCardProps {
  lock: SkyTarget;
  equipConnected: boolean;
  /** "2h · 4 filters · finishes 00:14" - what IMAGE would queue. */
  planSummary: string;
  /** Kept framing for THIS target, or null. */
  framed: string | null;
  onAdjustFrame: () => void;
  onClearFrame: () => void;
  onPrimary: (cta: LockCta) => void;
  onInfo: () => void;
  onSingleFrame: () => void;
  onPlan: () => void;
  inPool: boolean;
  poolCount: number;
  /** Take THIS target back out of the pool. Rendered as its own control only
   *  when the button beside it has stopped being a toggle - see `planLabel`. */
  onRemoveFromPool: () => void;
  primaryReason: string | null;
  singleReason: string | null;
  /** Why + PLAN cannot be pressed, when it cannot (a satellite). */
  planReason?: string | null;
  onExplain: (reason: string) => void;
  /** Passes found for a locked SATELLITE, or null when nobody has answered
   *  yet. Only `lockCta` reads it, and only for a satellite. */
  passCount?: number | null;
}

export function LockCard({
  lock,
  equipConnected,
  planSummary,
  framed,
  onAdjustFrame,
  onClearFrame,
  onPrimary,
  onInfo,
  onSingleFrame,
  onPlan,
  inPool,
  poolCount,
  onRemoveFromPool,
  primaryReason,
  singleReason,
  planReason = null,
  onExplain,
  passCount = null,
}: LockCardProps): JSX.Element {
  const cta = lockCta({ ...lock, passCount }, equipConnected);
  const skin = ctaSkin(cta);

  /**
   * WHAT THE SECOND BUTTON SAYS, AND WHAT PRESSING IT DOES - the same thing.
   *
   * The label used to come from `poolCount` alone, so a target that was NOT in
   * a pool of three still read "PLAN 3 TARGETS" while `SkyHub`'s `pressPlan`
   * added it to the pool: a button naming an action it does not perform. The
   * open-the-sheet case is `inPool && poolCount >= 2` - the same condition
   * `pressPlan` branches on - and every other case is the toggle.
   *
   * Which leaves a hole the old label hid: once that condition holds, the
   * button no longer removes anything, so a member of a pool of two or more had
   * no way out of it except the quick sheet. Hence `showRemove` and the x
   * beside it.
   */
  const opensPool = inPool && poolCount >= 2;
  const planLabel = opensPool ? `PLAN ${poolCount} TARGETS` : inPool ? "IN TONIGHT'S PLAN" : "+ PLAN";
  const planSub = opensPool
    ? "queued for one flow"
    : inPool
      ? "tap to remove"
      : poolCount > 0
        ? `${poolCount} already queued`
        : "add to tonight's pool";

  /**
   * AN x, NOT A LONG PRESS.
   *
   * A long press is already spoken for on this screen - "hold any control for a
   * plain-language explanation" is the design's own idiom and `honestPress`
   * implements it on every locked control - so binding removal to the same
   * gesture would make one hold mean two different things depending on state.
   * An x is also the affordance this very card already uses for the kept
   * framing row three rows up, so there is one way to take something off this
   * card rather than two.
   */
  const showRemove = opensPool;

  return (
    <Card tone="accent" className="nx-sky-lock" data-testid="sky-lock">
      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 10 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10, minWidth: 0 }}>
            <div
              aria-hidden="true"
              style={{
                width: 34, height: 34, borderRadius: 10,
                border: "1px solid color-mix(in srgb, var(--accent) 40%, transparent)",
                display: "flex", alignItems: "center", justifyContent: "center",
                color: "var(--accent)", flexShrink: 0,
              }}
            >
              {/* The finder's own table, not a ternary chain: a kind added to
                  `SKY_KINDS` without a case here used to fall through to the
                  nebula glyph, which is how a comet would have been drawn as a
                  nebula on the one card that names what you are pointing at. */}
              <NxIcon name={KIND_ICON[lock.kind]} size={18} />
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 2, minWidth: 0 }}>
              <div data-testid="sky-lock-name" style={{ fontFamily: DISPLAY, fontWeight: 600, fontSize: 15, letterSpacing: ".1em" }}>
                {lock.name}
              </div>
              <div style={{ fontSize: 12, color: "var(--text-dim)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                {lock.full}
              </div>
            </div>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 8, flexShrink: 0 }}>
            <div style={{ fontFamily: MONO, fontSize: 10, color: lock.color, textAlign: "right", letterSpacing: ".06em", whiteSpace: "nowrap" }}>
              {lock.statusTxt}
            </div>
            <button
              type="button"
              aria-label="about this target"
              data-testid="sky-lock-info"
              onClick={onInfo}
              style={{
                width: 30, height: 30, borderRadius: "50%",
                border: "1px solid var(--line-bright)", background: "var(--bg)",
                color: "var(--text-dim)", fontFamily: MONO, fontSize: 12,
                fontStyle: "italic", cursor: "pointer", flexShrink: 0,
              }}
            >
              i
            </button>
          </div>
        </div>

        {framed && (
          <div
            data-testid="sky-lock-framed"
            style={{
              display: "flex", alignItems: "center", gap: 8,
              padding: "6px 10px", borderRadius: 10,
              border: "1px solid color-mix(in srgb, var(--accent) 35%, transparent)",
              background: "color-mix(in srgb, var(--accent) 6%, transparent)",
            }}
          >
            <span style={{ color: "var(--accent)", display: "flex" }}>
              <SkyGlyph name="frame" size={14} />
            </span>
            <span style={{ fontFamily: MONO, fontSize: 10.5, color: "var(--text)", flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              {framed}
            </span>
            <button
              type="button"
              onClick={onAdjustFrame}
              data-testid="sky-frame-adjust"
              style={{
                height: 26, padding: "0 9px", borderRadius: 8,
                border: "1px solid var(--line-bright)", background: "var(--bg-raise)",
                color: "var(--text)", fontFamily: DISPLAY, fontWeight: 600,
                fontSize: 10, letterSpacing: ".12em", cursor: "pointer",
              }}
            >
              ADJUST
            </button>
            <button
              type="button"
              aria-label="remove framing"
              data-testid="sky-frame-clear"
              onClick={onClearFrame}
              style={{
                width: 26, height: 26, borderRadius: 8,
                border: "1px solid var(--line-bright)", background: "var(--bg-raise)",
                color: "var(--text-dim)", fontFamily: MONO, fontSize: 12, cursor: "pointer",
              }}
            >
              ×
            </button>
          </div>
        )}

        <div style={{ display: "flex", gap: 14, flexWrap: "wrap", fontFamily: MONO, fontSize: 10.5, color: "var(--text-dim)" }}>
          <span>alt <span style={{ color: "var(--text)" }}>{Math.round(lock.altNow)}°</span></span>
          <span>window <span style={{ color: "var(--text)" }}>{windowLabel(lock.windowMinutes)}</span></span>
          <span>transit <span style={{ color: "var(--text)" }}>{lock.transitLabel}</span></span>
          <Mono size={10.5}>{lock.palette}</Mono>
        </div>

        <button
          type="button"
          data-testid="sky-cta"
          data-cta={cta.kind}
          className={lockedClass(primaryReason, "nx-sky-cta")}
          onClick={honestPress(primaryReason, onExplain, () => onPrimary(cta))}
          {...lockedAttrs(primaryReason)}
          style={{
            height: 56, borderRadius: 12,
            border: `1px solid ${skin.border}`,
            background: skin.bg,
            color: skin.color,
            boxShadow: skin.glow,
            display: "flex", flexDirection: "column",
            alignItems: "center", justifyContent: "center", gap: 3,
            cursor: "pointer", padding: "0 10px", minWidth: 0,
          }}
        >
          <span style={{ fontFamily: DISPLAY, fontWeight: 600, fontSize: 12, letterSpacing: ".14em" }}>
            {cta.label}
          </span>
          {/* Rendered only when there IS one. A satellite has no plan to
              summarise - it is not a night, it is a five-minute pass - and an
              empty second line would leave the label floating off centre. */}
          {planSummary !== "" && (
            <span style={{ fontFamily: MONO, fontSize: 10, opacity: 0.8, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", maxWidth: "100%" }}>
              {planSummary}
            </span>
          )}
        </button>

        <div style={{ display: "flex", gap: 8 }}>
          <button
            type="button"
            data-testid="sky-single"
            className={lockedClass(singleReason, "nx-sky-secondary")}
            onClick={honestPress(singleReason, onExplain, onSingleFrame)}
            {...lockedAttrs(singleReason)}
            style={{
              flex: 1, minWidth: 0, height: 50, borderRadius: 12,
              border: "1px solid var(--line-bright)", background: "var(--bg-raise)",
              color: "var(--text)", display: "flex", alignItems: "center", gap: 10,
              padding: "0 12px", cursor: "pointer", textAlign: "left",
            }}
          >
            <span style={{ flexShrink: 0, display: "flex" }}><NxIcon name="camera" size={18} /></span>
            <span style={{ display: "flex", flexDirection: "column", gap: 2, minWidth: 0 }}>
              <span style={{ fontFamily: DISPLAY, fontWeight: 600, fontSize: 10, letterSpacing: ".12em" }}>SINGLE FRAME</span>
              <span style={{ fontFamily: MONO, fontSize: 10, color: "var(--text-dim)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                one exposure, aimed here
              </span>
            </span>
          </button>

          <button
            type="button"
            data-testid="sky-plan"
            data-plan-opens={opensPool ? "sheet" : "toggle"}
            className={lockedClass(planReason)}
            onClick={honestPress(planReason, onExplain, onPlan)}
            {...lockedAttrs(planReason)}
            style={{
              flex: 1, minWidth: 0, height: 50, borderRadius: 12,
              border: `1px solid ${inPool ? "var(--accent)" : "var(--line-bright)"}`,
              background: inPool ? "color-mix(in srgb, var(--accent) 12%, transparent)" : "var(--bg-raise)",
              color: "var(--text)", display: "flex", alignItems: "center", gap: 10,
              padding: "0 12px", cursor: "pointer", textAlign: "left",
            }}
          >
            <span style={{ flexShrink: 0, display: "flex" }}><SkyGlyph name="plan" size={18} /></span>
            <span style={{ display: "flex", flexDirection: "column", gap: 2, minWidth: 0 }}>
              <span style={{ fontFamily: DISPLAY, fontWeight: 600, fontSize: 10, letterSpacing: ".12em" }}>{planLabel}</span>
              <span style={{ fontFamily: MONO, fontSize: 10, color: "var(--text-dim)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                {planSub}
              </span>
            </span>
          </button>

          {showRemove && (
            <button
              type="button"
              data-testid="sky-plan-remove"
              aria-label={`take ${lock.name} out of tonight's plan`}
              title={`take ${lock.name} out of tonight's plan`}
              onClick={onRemoveFromPool}
              style={{
                width: 44, height: 50, borderRadius: 12, flexShrink: 0,
                border: "1px solid var(--line-bright)", background: "var(--bg-raise)",
                color: "var(--text-dim)", fontFamily: MONO, fontSize: 14,
                cursor: "pointer", display: "flex", alignItems: "center", justifyContent: "center",
              }}
            >
              ×
            </button>
          )}
        </div>
      </div>
    </Card>
  );
}
