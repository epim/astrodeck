// TonightPanel.tsx — "what is this flow actually going to do tonight?", in
// three readings of the same answer: the night as a picture, the night as
// sentences, and the plan the engine will literally run.
//
// THE REFUSAL IS THE PRODUCT. `GET /api/flows/{id}/tonight` can legitimately
// answer `{ok: false, reason: "No observatory site is set, so there is no night
// to resolve — nothing below would be about where you are."}`, and tonight.py's
// own docstring explains why it refuses instead of guessing: "the alternative —
// a plausible dusk from a default site — is the failure this whole panel exists
// to avoid: an operator planning an evening around a number nobody measured."
// A UI that folded that into a generic error state would throw away the one
// sentence that tells the operator what to go and fix. So `reason` is rendered
// as prose, never as an error chrome, and it is never merged with a TRANSPORT
// failure (the request did not arrive), which is a different thing and says so.
//
// The route is gated on CAP_VIEW_SITE_DERIVED rather than CAP_VIEW_STATUS —
// "an audit of this codebase recovered the observatory to 2.9 km from three
// viewer-legal requests" (app.py). The HonestButton that gates opening this
// panel for a viewer belongs to the header (§E.5); this component assumes it
// was allowed to open and reports whatever the server said.

import {
  useEffect, useMemo,
  type CSSProperties, type JSX, type KeyboardEvent as RKeyboardEvent,
  type ReactNode,
} from "react";
import { Overlay } from "../Overlay";
import { useStore } from "../../store";
import { radioNextIndex } from "../../lib/radiogroup";
import type { TonightTab } from "./flowsTypes";
import TonightTimeline, {
  type TonightFlats, type TonightMoon, type TonightNight, type TonightTarget,
} from "./TonightTimeline";
import TonightStory, { type TonightStoryRow } from "./TonightStory";
import TonightPlan from "./TonightPlan";

/** In this order and verbatim (§C.11); TIMELINE is the default tab. */
const TABS: TonightTab[] = ["timeline", "story", "plan"];
const TAB_LABEL: Record<TonightTab, string> = {
  timeline: "TIMELINE", story: "STORY", plan: "PLAN",
};

// ───────────────────────────────────────────────────── reading the payload
// `flows.tonight` is `Record<string, unknown>` at the store boundary on
// purpose (flowsSlice): the payload is large and only this panel reads it. The
// readers below are total — a field the server did not send comes back null and
// draws nothing, rather than defaulting to something that would render.

const num = (v: unknown): number | null =>
  typeof v === "number" && Number.isFinite(v) ? v : null;
const str = (v: unknown): string => (typeof v === "string" ? v : "");
const rec = (v: unknown): Record<string, unknown> | null =>
  v !== null && typeof v === "object" && !Array.isArray(v)
    ? (v as Record<string, unknown>) : null;
const arr = (v: unknown): unknown[] => (Array.isArray(v) ? v : []);

interface TonightRead {
  ok: boolean;
  reason: string;
  night: TonightNight;
  flats: TonightFlats | null;
  moon: TonightMoon | null;
  targets: TonightTarget[];
  story: TonightStoryRow[];
}

function readTonight(payload: Record<string, unknown> | null): TonightRead | null {
  if (!payload) return null;
  const night = rec(payload.night);
  const flats = rec(payload.flats);
  const moon = rec(payload.moon);
  return {
    // Absent `ok` is treated as a refusal, not as success: this panel's whole
    // job is to not show a night nobody computed.
    ok: payload.ok === true,
    reason: str(payload.reason),
    night: {
      dusk_unix: night ? num(night.dusk_unix) : null,
      dawn_unix: night ? num(night.dawn_unix) : null,
      dark_start_unix: night ? num(night.dark_start_unix) : null,
      dark_end_unix: night ? num(night.dark_end_unix) : null,
    },
    flats: flats
      ? { start_unix: num(flats.start_unix), end_unix: num(flats.end_unix) }
      : null,
    moon: moon
      ? {
          illumination: num(moon.illumination),
          rise_unix: num(moon.rise_unix),
          set_unix: num(moon.set_unix),
        }
      : null,
    targets: arr(payload.targets).map((raw): TonightTarget => {
      const t = rec(raw) ?? {};
      const w = rec(t.window);
      const start = w ? num(w.start_unix) : null;
      const end = w ? num(w.end_unix) : null;
      return {
        label: str(t.label) || str(t.name),
        window: start !== null && end !== null
          ? { start_unix: start, end_unix: end } : null,
        curve: arr(t.curve).flatMap((p): [number, number][] => {
          if (!Array.isArray(p)) return [];
          const ts = num(p[0]);
          const alt = num(p[1]);
          return ts !== null && alt !== null ? [[ts, alt]] : [];
        }),
        meridian_flip_unix: num(t.meridian_flip_unix),
      };
    }),
    story: arr(payload.story).map((raw): TonightStoryRow => {
      const r = rec(raw) ?? {};
      return {
        t_unix: num(r.t_unix),
        label: str(r.label),
        msg: str(r.msg),
        tone: str(r.tone),
      };
    }),
  };
}

// ─────────────────────────────────────────────────────────────── the head
/** Pill tabs.
 *
 *  Plain `role="tab"` buttons and NOT the house `SegmentedControl`: that
 *  primitive emits `role="radio"` inside a `role="radiogroup"`, and the parity
 *  harness resolves this step as `get_by_role("tab") .or_ get_by_role("button")`
 *  — an explicit `role="radio"` matches neither, so states 04/05/06 could never
 *  be captured. §C.11 anticipates exactly this and says to fall back to plain
 *  buttons if the primitive does not emit one of the two. Its keyboard MODEL is
 *  still shared, via lib/radiogroup's key map.
 *
 *  The sizes here are §C.11's verbatim (px-2.5 py-1.5, and 40×34 for ✕), which
 *  lands them under the 44px touch floor. That tension is §G-20's open
 *  question, covering every sub-44 control in this surface; it is reported, not
 *  closed here. */
function TonightHead({ tab, name, onTab, onClose }: {
  tab: TonightTab;
  name: string;
  onTab: (t: TonightTab) => void;
  onClose: () => void;
}): JSX.Element {
  const onKeyDown = (e: RKeyboardEvent<HTMLButtonElement>, i: number) => {
    const next = radioNextIndex(e.key, i, TABS.length);
    if (next === null) return;
    e.preventDefault();
    onTab(TABS[next]);
    const list = e.currentTarget.closest("[role=tablist]");
    const tabs = list?.querySelectorAll<HTMLElement>("[role=tab]");
    tabs?.[next]?.focus?.();
  };

  return (
    // No `border-b` here: `.overlay-head` already draws the --line rule, and a
    // second one would double it.
    <div className="px-4 py-3 flex items-center gap-2 flex-wrap">
      <span className="font-display font-semibold text-[11px] tracking-[0.22em] text-ink">
        ◷ TONIGHT
      </span>
      <span className="font-mono text-[10px] text-faint">{name}</span>
      <div className="flex-1" />
      <div role="tablist" aria-label="Tonight view" className="flex items-center gap-2">
        {TABS.map((t, i) => (
          <button
            key={t}
            type="button"
            role="tab"
            id={`flows-tonight-tab-${t}`}
            aria-selected={t === tab}
            aria-controls="flows-tonight-panel"
            tabIndex={t === tab ? 0 : -1}
            onClick={() => onTab(t)}
            onKeyDown={(e) => onKeyDown(e, i)}
            className={`font-display font-semibold text-[9.5px] tracking-[0.14em]
              px-2.5 py-1.5 rounded-lg bg-transparent border ${
                t === tab ? "border-accent text-accent" : "border-line2 text-dim"
              }`}
          >
            {TAB_LABEL[t]}
          </button>
        ))}
      </div>
      <button
        type="button"
        onClick={onClose}
        aria-label="Close Tonight"
        className="min-w-[40px] min-h-[34px] rounded-lg border border-line2 text-dim"
      >
        ✕
      </button>
    </div>
  );
}

/** A sentence where a chart would have been. Used for every state the design
 *  does not draw (§G-18(d)) plus the transport failure, so the panel never has
 *  a blank body that reads as "nothing is wrong yet". */
function TonightNote({ children }: { children: ReactNode }): JSX.Element {
  return (
    <p className="text-[13px] leading-[1.55] text-ink [text-wrap:pretty]">
      {children}
    </p>
  );
}

// ───────────────────────────────────────────────────────────── the panel
export function TonightPanel(): JSX.Element {
  const open = useStore((s) => s.flows.ui.tonightOpen);
  const tab = useStore((s) => s.flows.ui.tonightTab);
  const setUi = useStore((s) => s.flowsSetUi);
  const fetchTonight = useStore((s) => s.flowsFetchTonight);
  const flowId = useStore((s) => s.flows.record?.id ?? "");
  const flowName = useStore((s) => s.flows.record?.name ?? "");
  const payload = useStore((s) => s.flows.tonight);
  const loading = useStore((s) => s.flows.tonightLoading);
  const error = useStore((s) => s.flows.tonightError);

  // Re-resolved on every open, not cached: this is an answer about a specific
  // instant, and a panel reopened two hours later would otherwise show the
  // window that has since closed.
  useEffect(() => {
    if (!open || !flowId) return;
    void fetchTonight();
  }, [open, flowId, fetchTonight]);

  const read = useMemo(() => readTonight(payload), [payload]);

  const close = () => setUi({ tonightOpen: false });

  let body: JSX.Element;
  if (tab === "plan") {
    // PLAN does not touch /tonight at all (§E.5) — it renders the compile.
    body = <TonightPlan />;
  } else if (error) {
    body = (
      <TonightNote>
        Tonight could not be read from the rig: {error}. Nothing below would be
        about tonight, so nothing is drawn.
      </TonightNote>
    );
  } else if (!read) {
    body = (
      <TonightNote>
        {loading
          ? "Resolving tonight — dusk, astronomical dark, the moon, and each target's window."
          : "Tonight has not been resolved for this flow yet."}
      </TonightNote>
    );
  } else if (tab === "story") {
    // On a refusal the server already carries `reason` as a story row (label
    // "—", tone warn), so STORY needs no special case: the sentence arrives
    // through the same path as every other one.
    body = <TonightStory story={read.story} />;
  } else if (!read.ok) {
    body = <TonightNote>{read.reason}</TonightNote>;
  } else {
    body = (
      <TonightTimeline
        night={read.night}
        flats={read.flats}
        moon={read.moon}
        targets={read.targets}
      />
    );
  }

  return (
    <Overlay
      open={open}
      variant="center"
      label="Tonight"
      onClose={close}
      // Square corners: the design's panel has none, and the centre variant
      // hands the surface a `rounded-2xl` utility. Overridden here rather than
      // in index.css, which is shared by every overlay in the app.
      surfaceClassName="!rounded-none"
      surfaceStyle={{ "--ov-max-w": "880px", "--ov-max-h": "90dvh" } as CSSProperties}
      head={
        <TonightHead
          tab={tab}
          name={flowName}
          onTab={(t) => setUi({ tonightTab: t })}
          onClose={close}
        />
      }
    >
      {/* The design's 14px/16px body padding rides on THIS element, not on
          `bodyClassName`: `.overlay-body.overlay-safe-b` sets padding-bottom
          from the safe-area inset, and index.css is unlayered — an authored
          rule there beats any Tailwind `pb-*` on the same element no matter the
          order (the tooltip war story). Nesting keeps both. */}
      <div
        id="flows-tonight-panel"
        role="tabpanel"
        aria-labelledby={`flows-tonight-tab-${tab}`}
        data-flows-tonight={tab}
        className="px-4 py-3.5"
      >
        {body}
      </div>
    </Overlay>
  );
}

export default TonightPanel;
