// FlowHeader.tsx — the 54px view-local toolbar (MILESTONE2-CONTRACT §C.3).
//   References: 02-editor-m16-full-service.png (idle), 07-run-cloud-dodge-hold.png (running).
//
// ⚠ §G-5 IS OPEN AND IT SHAPES THIS FILE. The design specifies a 54px header
// owning the logo, the two-line wordmark and the night toggle. The app already
// renders a 48px header (App.tsx:492) with a logo, a wordmark, and — in
// HeaderControls.tsx:114-118 — the night toggle, which is the ONE night
// mechanism in the product (`:root.night` + localStorage + the pre-paint script
// in index.html). Rendered as a routed view, Flows would produce two of each.
// This file implements §C.3's stated least-invasive reading: a view-local
// toolbar UNDER the app header, with the duplicated chrome dropped. Every drop
// is marked `[DROPPED pending §G-5]` at the point it would have gone, so the
// diff is one line each if the user rules the other way.
//
// WHAT THE HEADER IS NOT ALLOWED TO INVENT. Two numbers on this row have no
// server behind them and it matters which:
//   * ETA — `run.etaS` has NO source (§G-1: there is no `flow.node` / `flow.log`
//     publisher and no run-state GET). FlowRunState's own comment forbids a
//     client-side countdown from an assumed total, because "an ETA the client
//     invented looks identical to one the rig computed." So a null reads `—`.
//   * The validation chip — `GRAPH VALID` is only printed once the compiler has
//     actually answered. A chip that went green before the first compile
//     returned would be the exact green-while-wrong defect this project keeps
//     paying for.
import type { JSX } from "react";
import { useShallow } from "zustand/react/shallow";

import { useStore } from "../../store";
import { HonestButton, Tooltip } from "../ui";
import { accessPhrase, useCapability } from "../../lib/caps";
import { backendBadge, backendBadgeIsSim } from "../../lib/equipment";
import type { FlowIssue } from "../../lib/flowsApi";
import type { FlowTier } from "./geometry";
import { useFlowRunControls } from "./flowRunControls";

// RUN's honest-disabled sentence moved to `flowRunControls.tsx` when the phone
// MONITOR tab needed the same button. Re-exported from here because that is
// where `__tests__/flowHeaderText.test.ts` imports it from, and the four
// formatters it asserts belong together as "the strings this row could print
// untruthfully".
export { runBlockedReason } from "./flowRunControls";

/** A fresh `[]` in a selector defeats `useShallow` on every first render, so the
 *  empty case is one frozen module-level value. */
const EMPTY_ISSUES: readonly FlowIssue[] = Object.freeze([]);

// ─────────────────────────────────────────────────────────── pure formatters
// Exported so they can be asserted without a DOM — these are the four places
// this row could quietly print something untrue.

/** `m:ss`, or `—` when the rig has not said.
 *
 *  Minutes are unbounded (`73:05`): the design only ever shows a sub-hour ETA
 *  and there is no specified hour form, so the least-committal rendering is the
 *  one that never truncates a real number. */
export function formatEta(secs: number | null | undefined): string {
  if (secs == null || !Number.isFinite(secs) || secs < 0) return "—";
  const s = Math.round(secs);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

/** The validation chip's word. §C.3 names all three strings. */
export function checksLabel(openChecks: number): string {
  if (openChecks <= 0) return "GRAPH VALID";
  return openChecks === 1 ? "1 OPEN CHECK" : `${openChecks} OPEN CHECKS`;
}

/** The library sub-line (§E.1: `Automation library · 5 saved flows`, count from
 *  `cards.length`). The singular form is not in any source — "1 saved flows" is
 *  simply wrong, so it is corrected here and reported. */
export function librarySubline(count: number): string {
  return `Automation library · ${count} saved flow${count === 1 ? "" : "s"}`;
}

/** The provider pill, derived from the rig's REAL backend rather than pinned to
 *  the design fixture's word.
 *
 *  §C.3 spells the markup as `SIMULATOR` because the prototype's rig is
 *  simulated; §E lists no source for it. Hard-coding it would re-create #129
 *  exactly — "a fully real, tracking rig was badged SIMULATOR on the one chip
 *  that says whether commands reach the sky" — so the two audited helpers in
 *  lib/equipment decide, and only the simulator gets the design's word. The
 *  shape rules come with the class: `.prov-sim` is a DASHED border and a HOLLOW
 *  dot, `.prov-ext` a solid muted one, so the distinction survives night mode,
 *  a red filter and colour-blindness. Null = the backend has not reported yet;
 *  the app header holds the same slot empty in that state. */
export function providerPill(
  mode: string | null | undefined,
): { label: string; variant: string } | null {
  const label = backendBadge(mode);
  if (!label) return null;
  return backendBadgeIsSim(mode)
    ? { label: "SIMULATOR", variant: "prov-sim" }
    : { label, variant: "prov-ext" };
}

// ──────────────────────────────────────────────────────────────── selectors
// Every one returns a primitive, so zustand's Object.is comparison is exact and
// a pan, a node-status tick or a log line re-renders none of this row. The one
// derived object (`issues`) goes through useShallow, like useCamera does.
const useScreen = () => useStore((s) => s.flows.ui.screen);
const useFlowName = () => useStore((s) => s.flows.record?.name ?? "");
const useCardCount = () => useStore((s) => s.flows.cards.length);
// (the run phase is read by useFlowRunControls, which owns RUN/STOP for both
//  this row and the phone MONITOR tab — one subscription, not two)
const useEtaS = () => useStore((s) => s.flows.run.etaS);
const useCompiled = () => useStore((s) => s.flows.compiled !== null);
const useIssues = () =>
  useStore(useShallow((s) => s.flows.compiled?.issues ?? EMPTY_ISSUES));
const useBackendMode = () => useStore((s) => s.status?.mode);

export default function FlowHeader({ tier }: { tier: FlowTier }): JSX.Element {
  const phone = tier === "phone";
  const screen = useScreen();
  const editor = screen === "editor";

  const name = useFlowName();
  const cardCount = useCardCount();
  const etaS = useEtaS();
  const checked = useCompiled();
  const issues = useIssues();
  const mode = useBackendMode();

  // RUN/STOP — one implementation, shared with the phone MONITOR tab's
  // full-width button. `running` counts `stopping`: the engine publishes
  // "aborting" the moment teardown starts and only says stopped once the rig
  // has, so a header that flipped back to ▶ RUN here would offer to start over
  // a moving mount.
  const { running, reason: runReason, explain, act: runAct } = useFlowRunControls();

  // Tonight is gated on view.site_derived, NOT view.status: an audit of this
  // codebase recovered the observatory to 2.9 km from three viewer-legal
  // requests, which is why /api/flows/{id}/tonight sits behind the derived-site
  // capability (§E.5).
  const canViewSiteDerived = useCapability("view.site_derived");

  const closeEditor = useStore((s) => s.flowsCloseEditor);
  const setUi = useStore((s) => s.flowsSetUi);

  const chipLabel = checksLabel(issues.length);
  // The compiler has not answered yet (a fresh open, or every compile so far
  // has failed — flowsCompile deliberately keeps the LAST GOOD result, so a
  // dropped request leaves this null rather than blanking the chip). Neither
  // designed state fits: "GRAPH VALID" would be a claim nobody made, and
  // "0 OPEN CHECKS" says the same thing in different words. UNSPECIFIED — this
  // is the least-committal third state and it is named in the summary.
  const chipText = checked ? chipLabel : "NOT CHECKED";
  const chipTone = !checked ? "text-dim" : issues.length ? "text-warn" : "text-good";

  const pill = providerPill(mode);

  // `showValid` requires `!running`, which is what frees the space for the ETA
  // (§C.3). Stage and frames are deliberately NOT here — they live on the phone
  // MONITOR panel only.
  const showValid = editor && !phone && !running;
  const showProv = !phone;
  const phoneTitle = phone && editor;

  return (
    // <header> inside <main> is NOT a banner landmark (its nearest sectioning
    // ancestor is main), so this does not create a second banner next to
    // App.tsx's.
    <header
      className="h-[54px] flex-none flex items-center gap-2.5 px-3 border-b border-line
        bg-raise/70 backdrop-blur-[10px] z-20 min-w-0"
    >
      {/* 1 — back. flowsCloseEditor saves first, then reloads the library, so a
          graph is never lost to leaving the screen. */}
      {editor && (
        <button
          type="button"
          className="btn !text-[10.5px] !tracking-[0.12em] !px-2.5 !py-[7px] shrink-0"
          onClick={() => { void closeEditor(); }}
        >
          <span aria-hidden="true">‹</span> LIBRARY
        </button>
      )}

      {/* 2 — phone title. README §5: the wordmark is replaced by a single
          truncating flow name, and nothing may overlap at 390px. */}
      {phoneTitle && (
        <span className="font-mono text-[11px] flex-1 min-w-0 truncate">{name}</span>
      )}

      {/* 3, 4 — logo + two-line wordmark: [DROPPED pending §G-5]. App.tsx:492-495
          already renders both, one row up. */}

      {/* 5 — sub-line. In the editor it is the flow name (capture 02: "M16 —
          full-service night"); in the library it is §E.1's count line. */}
      {!phone && (
        <span className="font-mono text-[10px] text-faint truncate min-w-0">
          {editor ? name : librarySubline(cardCount)}
        </span>
      )}

      {/* 6 — spacer. Skipped when the phone title already owns the slack, or the
          two flex-1 children would halve a title that has to truncate first. */}
      {!phoneTitle && <div className="flex-1 min-w-0" />}

      {/* 6b — PLAN EDITOR (#239 stage B). Plan left the nav rail; this is one of
          its two doors (the other is Settings > Safety > Imaging standards).
          Editor-only: from the library there is no compiled plan to go and look
          at. It is a plain destination switch, not a compile - the plan editor
          reads the stored plan, and what it is FOR now is the handful of
          settings that are still per-night intent and that Flows cannot yet
          say: guiding on or off, count mode, per-target meridian flip. */}
      {editor && (
        <button
          type="button"
          className="btn !text-[10.5px] !tracking-[0.12em] !px-2.5 !py-[7px] shrink-0"
          title="Open the plan editor - guiding, count mode and per-target flip
                 are not expressible in a flow yet"
          onClick={() => useStore.getState().setView("sequence")}
        >
          PLAN
        </button>
      )}

      {/* 7 — provider badge. */}
      {showProv && pill && (
        <span
          className={`prov ${pill.variant} shrink-0`}
          role="img"
          aria-label={`Rig backend: ${pill.label}`}
          title={`Backend: ${mode}`}
        >
          <span className="dot" aria-hidden="true" />
          <span aria-hidden="true">{pill.label}</span>
        </span>
      )}

      {/* 8 — validation chip. Never colour alone: the WORD changes with the
          state, and the border takes currentColor with it. */}
      {showValid && (
        <Tooltip
          side="bottom"
          label={chipText}
          triggerClassName="min-h-[44px] shrink-0"
          content={
            !checked
              ? "The graph checker has not answered for this flow yet."
              : issues.length === 0
                ? "The graph checker found nothing to flag."
                : (
                  <ul className="flex flex-col gap-1">
                    {issues.map((i, n) => <li key={`${i.text}-${n}`}>{i.text}</li>)}
                  </ul>
                )
          }
        >
          <span
            className={`font-mono text-[10px] tracking-[0.06em] px-2 py-1 rounded-[3px]
              border ${chipTone}`}
          >
            {chipText}
          </span>
        </Tooltip>
      )}

      {/* 9 — ETA. ⚠ §G-13 is OPEN: it is unresolved whether this renders on a
          phone at all (the prototype's markup would; README §5 does not list it;
          all four phone captures are idle). §C.3's condition column says
          `running` with no phone gate, so that is what is implemented — and
          `shrink-0` beside the truncating title is what keeps it from
          overlapping at 390px. */}
      {running && (
        <span
          className="font-mono text-[11px] tabular-nums shrink-0"
          // TODO(flows-handoff): `run.etaS` has no publisher — see §G-1. Under
          // settlement (b) the candidate source is the existing sequence event's
          // `progress.eta_s` (+ `eta_confident`), which a flow run really does
          // emit because it runs on the same engine. Do not wire it here without
          // the ruling: it changes what capture 07 shows.
          title={etaS == null
            ? "The rig has not reported a time remaining for this run."
            : undefined}
        >
          <span className="text-dim">ETA </span>
          <span className="text-ink">{formatEta(etaS)}</span>
        </span>
      )}

      {/* 10 — Tonight. */}
      {editor && (
        <HonestButton
          className="btn !text-[11px] !tracking-[0.12em] !px-2.5 !py-[7px] shrink-0"
          reason={canViewSiteDerived ? null
            : `Tonight is worked out from the observatory site, so it needs ${
              accessPhrase("view.site_derived")}.`}
          onExplain={explain}
          onClick={() => setUi({ tonightOpen: true })}
        >
          <span
            title="Tonight: timeline, plain-English brief, compiled plan"
            {...(phone ? { "aria-label": "TONIGHT" } : {})}
          >
            <span aria-hidden="true">◷</span>
            {!phone && " TONIGHT"}
          </span>
        </HonestButton>
      )}

      {/* 11 — RUN / STOP. The idle border is --accent-dim (purple), not
          --accent; index.css has no @layer wrapper, so `.btn`'s own
          background/colour/border beat an unprefixed utility and every override
          here has to be `!`.
          ■ STOP stays a plain single-tap — ui.tsx's own header: "Emergency
          motion stops (STOP/HALT/polar-STOP) stay single-tap — do NOT route
          them through HoldButton." */}
      {editor && (
        <HonestButton
          className={`btn shrink-0 ${running
            ? "!border-bad !text-bad !bg-[color-mix(in_srgb,var(--bad)_12%,transparent)]"
            : "!border-accent2 !bg-accent-fill !text-accent"}`}
          reason={runReason}
          onExplain={explain}
          onClick={runAct}
        >
          <span aria-hidden="true">{running ? "■" : "▶"}</span>
          {running ? " STOP" : " RUN"}
        </HonestButton>
      )}

      {/* 12 — night toggle ☾: [DROPPED pending §G-5]. There is exactly one night
          mechanism (`:root.night` + localStorage + index.html's pre-paint
          script) and HeaderControls.tsx:114-118 already drives it. A second
          visible toggle for one piece of state is how two truths appear. */}

      {/* 13 — design notes. */}
      <button
        type="button"
        className="btn !text-[11px] !px-2.5 !py-[7px] shrink-0"
        aria-label="Design notes"
        title="Design notes"
        // TODO(flows-handoff): `ui.notesOpen` is declared by the slice but §A.1's
        // file plan lists no component that reads it, so nothing renders yet.
        // Whoever owns the notes panel closes this; if the panel is out of
        // milestone-2 scope, this button goes with it rather than staying inert.
        onClick={() => setUi({ notesOpen: true })}
      >
        i
      </button>
    </header>
  );
}
