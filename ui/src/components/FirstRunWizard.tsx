// FirstRunWizard.tsx — the docked first-run BAR (NOV-2-3/4; first-run wizard
// design spec §3, Tasks 4 + 5). Builds a plain snapshot from narrow store
// selectors, hands it to the tested `computeWizard`, and renders ONE step at a
// time plus a deep-link into the ALREADY-BUILT panels (Settings/SitePanel,
// Equipment, Atlas, Capture). This component never re-implements
// connect/site/capture — it points at them and watches the store for
// completion.
//
// PHONE FEEDBACK THAT RESHAPED THIS (first-hand, from a user driving the real
// app on a phone on the LAN — treated as ground truth, then re-measured here):
//
//   "It looks like it would be a wizard that would walk me through something
//    step by step. But what's actually happening is I think it's just a list of
//    steps to do. As I select each line of text (which I would not expect to be
//    selectable)... this view is blocking about half the screen... I'm as far as
//    'Pick a target'. I loaded the atlas, but I can't see it because the modal
//    is in the way."
//
// Both halves reproduced on this tree. The old shape rendered all six steps as
// a rail of selectable rows above the active step's body, and MEASURED 386px
// tall on every phone: 45.7% of a 390x844 iPhone, 42.1% of a 412x915 S25 Ultra,
// 40.3% of a 440x956 Pro Max. It asked the user to operate the Atlas while
// covering it. So:
//
//   * ONE STEP, not a checklist. The bar shows only the current step's title +
//     instruction. The full checklist still has value (it shows what is already
//     configured) so it stays reachable behind the progress chip — but it is no
//     longer the default state, and nothing is a selectable row until the user
//     deliberately opens it.
//   * THREE SHORT LINES: title row (with progress + close), instruction + the
//     gate, button row. Measured after the change, walking ALL SIX steps on all
//     three phones (the tallest step is whichever body wraps to a third line):
//       390x844   164px on five steps, 181px on "Connect a rig"  -> 19.4-21.4%
//       412x915   164px on every step                            -> 17.9%
//       440x956   164px on every step                            -> 17.2%
//     Against 386px / 45.7% / 42.1% / 40.3% before. The bar's bottom edge
//     measures 788 against a nav top of 787 on the 390 phone (859/858 and
//     900/899 on the other two): docked ON the nav, not over it.
//   * NEXT IS HONEST. It is inert until the step is genuinely finished, and it
//     is inert the house way — dim + aria-disabled + a STATED, VISIBLE reason
//     ("Next unlocks once a target is in your plan") — never the native
//     `disabled` attribute, and never with `title` as the only channel (title
//     does not fire on touch, which is the only input this user has). The gate
//     is `step.done` straight out of `computeWizard`/`isDone`, i.e. exactly the
//     live signal, not a second source of truth: it ungreys the moment the task
//     is really done.
//   * WHERE, NOT JUST WHAT. Step bodies (lib/firstRunWizard.ts) now name the
//     view AND the place on it, because the same user got stuck twice on
//     navigation rather than on the task: "the next thing is to cool the
//     camera, to do this I need to scroll to the bottom of the page, which is
//     not easy for a user to know about unless we tell them to scroll."
//
// CRITICAL: unlike ConfirmDialog (a focus-trapping modal this borrows its
// token *look* from), this bar must NEVER trap focus or block pointer events
// over the rest of the screen — the user has to be able to keep operating
// SitePanel / the Equipment dropdowns / the cooler input while it is up. There
// is no full-viewport scrim element here at all. That is why it renders through
// <Overlay variant="corner" modal={false} scrim={false} dismissOnOutside={false}
// trapFocus={false} autoFocus={false}> — every one of those flags is
// load-bearing, not a default being restated.
//
// REVIEW #4 (the novice's blocker, root cause CONFIRMED by measurement — the
// review filed the culprit as NEEDS-REPRO and it was right): this card carried
// `fixed bottom-0 inset-x-0` AND `panel`. `.panel { position: relative }` is
// UNLAYERED authored CSS and Tailwind's `fixed` lives in @layer utilities, and
// unlayered always beats layered regardless of specificity or order — so the
// card computed `position: relative` and laid out in normal flow at the BOTTOM
// OF THE DOCUMENT. Measured on this tree before the fix: tablet {x:-16, y:1164}
// in vh 1180, phone {x:0, y:844} in vh 844, desktop {x:-16, y:884} in vh 900 —
// with `body { overflow: hidden }` and scrollHeight 1561 > 1180, so no gesture
// on any viewport could reveal it. The 5-step checklist that teaches a
// first-time user what to do was invisible on first load on every device, and
// stayed invisible when they explicitly pressed "OPEN THE SETUP GUIDE".
// The fix is not "add !important": it is to stop putting a positioned overlay
// inside a class that owns `position`. Overlay's surface is `.overlay-surface`,
// which does not — and the re-shape below does NOT reintroduce the trap: it
// adds no positioning utility of its own, it keeps rendering through Overlay,
// and the computed `position` of the wrapper/surface is re-measured on a real
// page every time this file changes (wrapper `absolute` with `bottom: 56px`
// from `.overlay-above-nav`, surface `relative` inside it — i.e. docked to the
// viewport by the portal host, sitting on top of the bottom nav's 3.5rem).

import { useEffect, useRef, useState } from "react";
import {
  useStore,
  useWizardOpen,
  useWizardStepId,
  useHasSeen,
  useView,
  useSite,
  useEquipConnected,
  useConfig,
  usePlan,
  usePreviews,
} from "../store";
import { computeWizard, WIZARD_STEPS, type WizardStepId } from "../lib/firstRunWizard";
import { WIZARD_SEEN_KEY } from "../lib/coach";
import { listProfiles } from "../api/backends";
import { Icon } from "./icons";
import { LOCKED_CLASS } from "./ui";
import { Overlay } from "./Overlay";

export default function FirstRunWizard(): JSX.Element | null {
  const open = useWizardOpen();
  const manualId = useWizardStepId();
  const site = useSite();
  const currentView = useView();
  const config = useConfig();
  const equipConnected = useEquipConnected();
  const targetCount = usePlan().targets.length;
  const previewCount = usePreviews().length;
  const hasCooler = useStore((s) => !!s.status?.camera?.can_cool);
  const coolerActive = useStore((s) => !!s.status?.camera?.cooler?.on);
  const setView = useStore((s) => s.setView);
  const setWizardStep = useStore((s) => s.setWizardStep);
  const closeWizard = useStore((s) => s.closeWizard);
  const openWizard = useStore((s) => s.openWizard);
  const seenWizard = useHasSeen(WIZARD_SEEN_KEY);

  // The full checklist is OPT-IN. This is the single line that encodes the
  // headline fix: a first-time user gets one step, not six selectable rows.
  const [listOpen, setListOpen] = useState(false);

  const [profileCount, setProfileCount] = useState(0);
  useEffect(() => {
    if (!open) return;
    void listProfiles()
      .then((r) => setProfileCount(r.length))
      .catch(() => {
        /* best-effort; leave prior count */
      });
  }, [open, equipConnected]);

  const siteIsDefault = site?.is_default ?? config?.site?.is_default ?? true;

  const view = computeWizard(
    { siteIsDefault, equipConnected, profileCount, targetCount, hasCooler, coolerActive, frameCount: previewCount },
    manualId,
  );

  // Auto-advance: clear a manual override once the doneCount rises (a step
  // just completed), so the machine falls back to the next first-incomplete
  // step instead of leaving the user parked on a step they already finished.
  const prevDone = useRef(view.doneCount);
  useEffect(() => {
    if (view.doneCount > prevDone.current && manualId) setWizardStep(null);
    prevDone.current = view.doneCount;
  }, [view.doneCount, manualId, setWizardStep]);

  // Auto-open: genuine blank slate only (never-seen + default site + no rig),
  // so a returning/already-set-up user (or astrotown) is never interrupted.
  // seenWizard guards a user who has already dismissed it once.
  useEffect(() => {
    if (!seenWizard && !open && siteIsDefault && !equipConnected) openWizard();
  }, [seenWizard, open, siteIsDefault, equipConnected, openWizard]);

  if (!open) return null;

  const active = view.steps[view.activeIndex];
  const atFirst = view.activeIndex === 0;
  const atLast = view.activeIndex === view.steps.length - 1;

  const goBack = () => {
    if (!atFirst) setWizardStep(view.steps[view.activeIndex - 1].id);
  };
  const goNext = () => {
    if (!atLast) setWizardStep(view.steps[view.activeIndex + 1].id);
  };
  const revisit = (id: WizardStepId) => {
    setWizardStep(id);
    setListOpen(false);
  };

  const finish = () => {
    closeWizard();
    useStore.getState().enqueueToast({ level: "success", title: "Setup complete — clear skies!" });
  };

  // Next is live only when the CURRENT step is genuinely done — `active.done`
  // is `isDone(id, snapshot)`, the same computation the checklist ticks render.
  const nextLive = active.done && !atLast;
  // Two renderings of ONE reason: the visible line under the instruction reads
  // as a sentence, the accessible name reads after the button's own name (so it
  // must not repeat "Next"). Neither is a tooltip — `title` does not fire on
  // touch, and touch is the only input the user who reported this had.
  const nextClause = !active.done
    ? `unlocks once ${active.need}`
    : "this is the last step; open the list above to finish the rest";
  const nextReasonLine = !active.done
    ? `Next unlocks once ${active.need}.`
    : "Last step — open the list above to finish the rest.";

  return (
    <Overlay
      open
      label="First-run setup guide"
      variant="corner"
      modal={false}
      scrim={false}
      dismissOnOutside={false}
      trapFocus={false}
      autoFocus={false}
      onClose={closeWizard}
      // `!pb-2`: Overlay adds `.overlay-safe-b` (padding-bottom:
      // env(safe-area-inset-bottom)) to a footer-less body, but this variant's
      // wrapper is `.overlay-above-nav`, whose `bottom: calc(3.5rem +
      // env(safe-area-inset-bottom))` has ALREADY paid the home-indicator inset
      // once — the bar's bottom edge sits on the nav, which sits on the inset.
      // Paying it twice would waste ~34px of a phone screen on exactly the
      // surface this whole change is about shrinking. The `!` is required and
      // is not cargo: index.css is unlayered, so a plain `pb-2` utility would
      // lose to `.overlay-safe-b`; `!important` wins across layers.
      bodyClassName="!p-0 !pb-2"
    >
      {/* ---- opt-in full checklist. NOT the default state; it exists because
              seeing what is already configured is genuinely useful, and it is
              the only place step rows are selectable — which is now a thing the
              user asked for rather than a thing that happened to them. ---- */}
      {listOpen && (
        <ol className="flex flex-col border-b border-line px-1 py-1">
          {view.steps.map((st) => {
            const isActive = st.id === view.activeId;
            return (
              <li key={st.id}>
                <button
                  type="button"
                  onClick={() => revisit(st.id)}
                  aria-current={isActive ? "step" : undefined}
                  // The row you are on is marked by a SHAPE (left rule) and a
                  // WORD ("now"), not by a background tint — the list sits on
                  // --bg-raise, so a `bg-raise` highlight was invisible, and in
                  // night mode every hue collapses to red anyway.
                  className={`w-full flex items-center gap-2 text-left text-xs px-2 min-h-[44px] border-l-2
                    ${isActive ? "border-accent text-ink font-medium" : "border-transparent text-dim"}`}
                >
                  {/* status by GLYPH + WORD, never colour alone */}
                  <span
                    aria-hidden
                    className={`shrink-0 inline-flex items-center justify-center w-4 h-4
                      ${st.done ? "text-good" : "text-faint"}`}
                  >
                    {st.done ? <Icon name="check" size={12} /> : "◦"}
                  </span>
                  <span className="truncate">{st.title}</span>
                  <span className={`ml-auto shrink-0 text-[10px] tracking-wider uppercase
                    ${st.done ? "text-good" : isActive ? "text-accent" : "text-faint"}`}>
                    {st.done ? "done" : isActive ? "now" : "to do"}
                  </span>
                </button>
              </li>
            );
          })}
        </ol>
      )}

      <div className="px-3 pt-1.5 flex flex-col gap-1">
        {/* ---- row 1: which step you are on, progress/expand, dismiss ---- */}
        <div className="flex items-center gap-2">
          <h2 className="text-sm font-semibold text-ink truncate min-w-0 flex-1">
            {view.complete ? "You're all set" : active.title}
          </h2>
          <button
            type="button"
            className="btn !px-2 min-h-11 inline-flex items-center gap-1 shrink-0"
            aria-expanded={listOpen}
            aria-label={
              listOpen
                ? `Hide the full setup list — ${view.doneCount} of ${view.total} done`
                : `Show the full setup list — ${view.doneCount} of ${view.total} done`
            }
            onClick={() => setListOpen((v) => !v)}
          >
            <span className="mono text-[11px] text-dim">
              {view.doneCount}/{view.total}
            </span>
            <Icon name={listOpen ? "arrow-down" : "arrow-up"} size={12} />
          </button>
          <button
            type="button"
            aria-label="Later — hide the setup guide"
            className="btn btn-touch inline-flex items-center justify-center p-0 shrink-0"
            onClick={closeWizard}
          >
            <Icon name="x" size={14} />
          </button>
        </div>

        {/* ---- row 2: the instruction (WHERE + WHAT), then the gate ---- */}
        {view.complete ? (
          <p className="text-xs text-dim leading-snug">
            Every step is done — clear skies.
          </p>
        ) : (
          <>
            <p className="text-xs text-dim leading-snug">{active.body}</p>
            {/* The gate, stated in words next to a glyph — never colour alone,
                never a tooltip. It tracks `nextLive` exactly, so it can never
                say "tap Next" while Next is dim. */}
            <p
              className={`flex items-center gap-1.5 text-[11px] leading-snug
                ${nextLive ? "text-good" : "text-faint"}`}
            >
              <Icon name={nextLive ? "check" : "lock"} size={11} aria-hidden />
              <span>{nextLive ? "Done — tap Next." : nextReasonLine}</span>
            </p>
          </>
        )}

        {/* ---- row 3: Back (only when there is one) · Skip · go there · Next ---- */}
        <div className="flex items-center gap-1.5">
          {/* Back is OMITTED at the first step rather than dimmed: a control
              that can never do anything on this screen is noise, and omitting
              it buys ~44px of width back on a 390px row. */}
          {!atFirst && (
            <button
              type="button"
              className="btn btn-touch inline-flex items-center justify-center p-0 shrink-0"
              onClick={goBack}
              aria-label={`Back to ${view.steps[view.activeIndex - 1].title}`}
            >
              <Icon name="arrow-left" size={14} />
            </button>
          )}
          {view.complete ? (
            <button type="button" className="btn btn-accent min-h-11 ml-auto" onClick={finish}>
              Finish
            </button>
          ) : (
            <>
              {/* Skip: leave this step unfinished and move on. At the last step
                  there is nowhere to go, so it is dim + aria-disabled + a
                  spoken reason — not `disabled`. */}
              <button
                type="button"
                className={`btn min-h-11 !px-2.5 shrink-0 ${atLast ? LOCKED_CLASS : ""}`}
                onClick={atLast ? undefined : goNext}
                aria-disabled={atLast || undefined}
                aria-label={atLast ? "Skip — this is the last step" : "Skip this step"}
              >
                Skip
              </button>
              {/* The deep link. Exactly ONE control in this row is accented at
                  a time, and which one is the whole "what do I do now?" signal:
                  while the step is unfinished the accent is on the door to the
                  view that finishes it; the moment it IS finished the accent
                  moves to Next (below). It also drops the accent when the user
                  is ALREADY on that view — an accented "ATLAS" while standing
                  in the Atlas is the bar shouting at a door they came through.
                  Never hidden and never locked: pressing it is harmless, and a
                  control that vanishes and returns is worse than a quiet one. */}
              <button
                type="button"
                className={`btn min-h-11 !px-2.5 ml-auto min-w-0 truncate
                  ${nextLive || currentView === active.view ? "text-dim" : "btn-accent"}`}
                aria-label={
                  currentView === active.view
                    ? `You're already on ${active.cta}`
                    : `Open ${active.cta}`
                }
                onClick={() => setView(active.view)}
              >
                {active.cta}
              </button>
              <button
                type="button"
                className={`btn min-h-11 !px-2.5 shrink-0 inline-flex items-center gap-1
                  ${nextLive ? "btn-accent" : LOCKED_CLASS}`}
                onClick={nextLive ? goNext : undefined}
                aria-disabled={nextLive ? undefined : true}
                aria-label={nextLive ? "Next step" : `Next — ${nextClause}`}
              >
                {!nextLive && <Icon name="lock" size={11} aria-hidden />}
                Next
              </button>
            </>
          )}
        </div>
      </div>
    </Overlay>
  );
}

// Re-export the step-def list for anything (tests, future coach marks) that
// wants the raw copy without pulling in the whole component tree.
export { WIZARD_STEPS };
