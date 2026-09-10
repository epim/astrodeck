// PlanPreflight.tsx - the readiness check and the button it gates.
//
// The rebuild of `components/PreflightStrip.tsx` and `components/
// PreflightModal.tsx`, COLLAPSED INTO ONE SURFACE. The modal existed because
// the strip was a dense inline row with no room to explain itself, and it was
// measured at x=-10 on a tablet with its footer buttons off the bottom of a
// 900 px desktop viewport. In a 420 px sheet there is nothing to overlay: the
// full checklist fits, every row carries its own detail and its own fix, and
// the verdict sits on the button. Nothing is lost - `usePreflight` is the same
// shared derivation, so the strip, the button and the run agree on one verdict
// exactly as they did.
//
// THE DERIVATION IS NOT RE-DERIVED. `usePreflight` (exported by the legacy
// module, hook not component) owns the de-duped altitude poller: three
// consumers used to mount three 30 s intervals against
// `/api/sequence/preflight`. Re-implementing it here would put that back.
//
// A FIX WITH NOTHING BEHIND IT IS NOT RENDERED. `buildPreflight` attaches
// `{label:"Start cooling now", onClick: actions.startCooling}` whether or not
// the caller passed that action, and this editor passes none - so the legacy
// strip drew a live-looking button whose press did nothing at all. A fix is
// rendered here only when it has a route or a callback.
//
// START IS TWO TAPS, NOT A MODAL. `arm` is the design's confirm for exactly
// this: an action that commits the rig for the next six hours, on a screen
// where a modal would have to be dismissed before the checklist beneath it
// could be read.

import { type JSX } from "react";

import type { PreflightVerdict } from "../../../../components/PreflightStrip";
import type { CheckItem, CheckStatus, SequencePlan } from "../../../../types";
import { LEGACY_VIEW_ROUTE } from "../../../legacyBridge";
import { nav } from "../../../router";
import { NxIcon, type NxIconName } from "../../../icons";
import { explainLock } from "../../../shell/explain";
import { ActionButton, Card, Label, LockNote, Mono, Pill } from "../../../ui";
import type { Tone } from "../../../ui/types";

const TONE: Record<CheckStatus, Tone> = {
  ok: "good",
  warn: "warn",
  blocked: "bad",
  checking: "dim",
  skipped: "dim",
  disabled: "warn",
};

/** Shape, not hue: each status is a distinct silhouette, so the row survives
 *  grayscale and the red-light palette where every tone is the same red. */
const GLYPH: Record<CheckStatus, NxIconName> = {
  ok: "check",
  warn: "info",
  blocked: "x",
  checking: "clock",
  skipped: "minus",
  disabled: "lock",
};

/** Plain-language one-liners for the warnings, verbatim from `warnLines`
 *  (`PreflightStrip.tsx:172-183`) - two of them say something the row's own
 *  label cannot. */
export function warnLine(i: CheckItem): string {
  if (i.id === "horizon" && i.status === "disabled") {
    return "Altitude unverified - set your location in Settings.";
  }
  if (i.id === "guiding") return "Will run unguided.";
  if (i.detail?.value) return `${i.label}: ${i.detail.value}`;
  return `${i.label} needs attention.`;
}

function CheckRow({ item }: { item: CheckItem }): JSX.Element {
  const tone = TONE[item.status];
  const fix = item.fix;
  const href = fix?.view ? LEGACY_VIEW_ROUTE[fix.view] : null;
  const live = fix != null && (href != null || fix.onClick != null);

  return (
    <div className="nx-plan-check" data-tone={tone} data-testid={`plan-check-${item.id}`}>
      <span className="nx-plan-check-glyph" aria-hidden="true">
        <NxIcon name={GLYPH[item.status]} size={14} />
      </span>
      <span className="nx-plan-check-text">
        <Mono size={11}>
          {item.label} · {item.word}
        </Mono>
        {item.detail?.value && (
          <Mono size={10} tone="dim">
            {item.detail.value}{item.detail.unit ? ` ${item.detail.unit}` : ""}
          </Mono>
        )}
      </span>
      {live && fix && (
        <ActionButton
          kind="ghost"
          onPress={() => {
            if (fix.inPlace && fix.onClick) { void fix.onClick(); return; }
            if (href) { nav.go(href); return; }
            void fix.onClick?.();
          }}
          data-testid={`plan-check-fix-${item.id}`}
        >
          {fix.label.toUpperCase()}
        </ActionButton>
      )}
    </div>
  );
}

/** The verdict word. Exported so the footer button and the card cannot invent
 *  two different summaries of one `items` array. */
export function preflightWord(items: CheckItem[], verdict: PreflightVerdict): {
  word: string; tone: Tone;
} {
  const blocked = items.filter((i) => i.status === "blocked").length;
  if (verdict === "blocked") {
    return { word: blocked === 1 ? "1 TO FIX" : `${blocked} TO FIX`, tone: "bad" };
  }
  return verdict === "warn" ? { word: "WARN", tone: "warn" } : { word: "READY", tone: "good" };
}

export function PlanPreflight({ plan, items, verdict }: {
  plan: SequencePlan;
  /** From the root's ONE `usePreflight(plan)` call. Passed rather than
   *  re-derived so the checklist, the START lock and the `force` flag threaded
   *  into the start body cannot come from three different snapshots. */
  items: CheckItem[];
  verdict: PreflightVerdict;
}): JSX.Element {
  const totalFrames = plan.targets.reduce(
    (a, t) => a + t.steps.reduce((b, s) => b + s.count, 0), 0);

  const warnings = items.filter((i) => i.status === "warn" || i.status === "disabled");
  const { word, tone } = preflightWord(items, verdict);

  // Blocked rows first, then warnings, then the rest - the same stable order
  // the modal pinned, so a row does not move under the finger as checks flip.
  const rank = (i: CheckItem) =>
    i.status === "blocked" ? 0 : (i.status === "warn" || i.status === "disabled") ? 1 : 2;
  const ordered = [...items].sort((a, b) => rank(a) - rank(b));

  return (
    <Card data-testid="plan-preflight">
      <div className="nx-plan-stack">
        <div className="nx-plan-row">
          <Label size={11}>PRE-FLIGHT</Label>
          <span style={{ marginLeft: "auto" }}>
            <Pill tone={tone} data-testid="plan-preflight-verdict">{word}</Pill>
          </span>
        </div>

        {/* The checklist is only meaningful once the plan asks for a frame. */}
        {totalFrames === 0 ? (
          <p className="nx-plan-note">
            Nothing to check yet - this plan asks for no frames. Add a target and a step
            and the readiness checks appear here.
          </p>
        ) : (
          <div className="nx-plan-stack" data-testid="plan-preflight-list">
            {ordered.map((i) => <CheckRow key={i.id} item={i} />)}
          </div>
        )}

        {warnings.length > 0 && verdict !== "blocked" && (
          <div className="nx-plan-stack">
            {warnings.map((i) => (
              <p key={i.id} className="nx-plan-note" data-tone="warn">{warnLine(i)}</p>
            ))}
          </div>
        )}

      </div>
    </Card>
  );
}

/** START, in the sheet's sticky footer.
 *
 *  The footer rather than the body because this editor is a long scroll and the
 *  legacy layout put Run at the bottom of a second column: on a tablet that
 *  meant scrolling past twenty automation controls to reach the button, and on
 *  the night the run has to start again that is the wrong 30 seconds. The
 *  verdict rides beside it so the button is never pressed blind. */
export function PlanStartFooter({ items, verdict, onStart, startLockedReason, viewOnly }: {
  items: CheckItem[];
  verdict: PreflightVerdict;
  /** The root's single writer of `POST /api/sequence/start`. */
  onStart: () => void;
  /** Why START cannot fire: no capability, a run already going, an empty plan,
   *  every step zeroed, or a blocked check. Derived once in the root so this
   *  footer and the run header cannot disagree. */
  startLockedReason: string | null;
  /** True when the reason is a MISSING CAPABILITY rather than a fixable state -
   *  that is the only case that earns the VIEW ONLY badge. */
  viewOnly: boolean;
}): JSX.Element {
  const { word, tone } = preflightWord(items, verdict);
  return (
    <div className="nx-plan-stack" data-testid="plan-start-footer">
      <div className="nx-plan-row">
        <Pill tone={tone} data-testid="plan-footer-verdict">{word}</Pill>
        {viewOnly && <Pill tone="warn" data-testid="plan-view-only-badge">VIEW ONLY</Pill>}
      </div>
      <ActionButton
        kind="primary"
        size="lg"
        full
        arm={{ label: "TAP AGAIN TO START", ms: 3000 }}
        onPress={onStart}
        lockedReason={startLockedReason}
        onExplain={explainLock}
        data-testid="plan-start"
      >
        START SEQUENCE
      </ActionButton>
      <LockNote reason={startLockedReason} data-testid="plan-start-lock" />
    </div>
  );
}
