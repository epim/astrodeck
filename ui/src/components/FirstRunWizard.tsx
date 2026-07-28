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
//   * AND THE OPT-IN LIST IS CAPPED. Opt-in was not enough: opening it handed
//     the user back the very thing they complained about — 393px of an 844px
//     iPhone (46.6%) on five steps and 437px (51.8%) on the six-step cooler
//     rig, growing with every step ever added. It is now a fixed WINDOW of
//     two-and-a-half rows with its own scroll region, so the expanded height no
//     longer depends on the step count at all. Re-measured on a live rig,
//     collapsed / expanded, five-step and six-step:
//       390x844   164px 19.4%  ->  276px 32.7%   (was 393/46.6 and 437/51.8)
//       412x915   164px 17.9%  ->  276px 30.2%
//       440x956   164px 17.2%  ->  276px 28.9%
//     Collapsed — the state the user is expected to operate the screen
//     underneath in — is unchanged and inside the protocol's ~25% budget. The
//     expanded state cannot also fit inside 25%: the step detail alone is 164px
//     (19.4%), which leaves 47px, one row, for the list. That is a deliberate
//     trade and the reason the window is a HALF row: the cut edge is the scroll
//     affordance, and the active row is scrolled into view on open so a
//     part-configured rig never opens the list on rows it has already finished.
//     Verified with a real finger (CDP touch drag): the list scrolls 25 -> 98
//     -> 161 of 161, and tapping a row scrolled into view selects that step and
//     collapses the list.
//   * THREE SHORT LINES: title row (with progress + close), instruction + the
//     gate, button row. Measured after the change, walking ALL SIX steps on all
//     three phones. "Connect a rig" was the one step that wrapped to a third
//     line at 390px and stood 181px (21.4%) instead of 164px; its nav hint is
//     now the short form (lib/firstRunWizard.ts) and every step measures the
//     same, with the "scroll down to Rig Actions" hint kept verbatim:
//       390x844   164px on every step  -> 19.4%   (was 181px / 21.4% on connect)
//       412x915   164px on every step  -> 17.9%
//       440x956   164px on every step  -> 17.2%
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
  const listRef = useRef<HTMLOListElement>(null);

  // Profiles are the ONE step whose signal is not in the store: it is a fetch.
  // Fetching it only on [open, equipConnected] left the step permanently
  // unticked — MEASURED on a live rig: POST /api/profiles/capture returned 200
  // and the bar sat on "Save a profile · 3/6" with Next locked for the rest of
  // the session, while the cooler and first-frame steps ticked within seconds
  // of their real signals. The user does exactly what the step says and the bar
  // calls them a liar; nothing about "keep scrolling to Profiles" tells them a
  // reload is what unsticks it. So poll — but ONLY inside the window where it
  // can change anything: the guide is open and no profile exists yet. The
  // moment one does, `noProfileYet` flips, this effect re-runs and clears the
  // interval, so a configured rig makes exactly one request.
  const [profileCount, setProfileCount] = useState(0);
  const noProfileYet = profileCount === 0;
  useEffect(() => {
    if (!open) return;
    let alive = true;
    const load = () =>
      void listProfiles()
        .then((r) => {
          if (alive) setProfileCount(r.length);
        })
        .catch(() => {
          /* best-effort; leave prior count */
        });
    load();
    if (!noProfileYet) return () => { alive = false; };
    const id = window.setInterval(load, 3000);
    return () => {
      alive = false;
      window.clearInterval(id);
    };
  }, [open, equipConnected, noProfileYet]);

  // `site` is the live/status mirror, `config.site` the persisted record — the
  // same six fields from one stored truth, which is why either answers this.
  // The mirrors are kept in lock-step by store.loadConfig(), and that is
  // load-bearing here: SitePanel's save calls loadConfig(), and before it also
  // refreshed `site` this line kept reading the stale bootstrap value and left
  // "Set your location" un-ticked with Next locked after a genuine save (a
  // phone user hit it: "I just set my location manually annnnd... it doesn't
  // show as having been set. However when I skipped ahead then it showed it" —
  // skipping ahead to connect a rig is what restarted the `status` stream that
  // refreshed the other mirror). If a future writer adds a THIRD way to persist
  // the site, it has to funnel through loadConfig() too, or this un-ticks again.
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

  // The checklist is a WINDOW now (see the <ol>), so on a part-configured rig
  // the row marked "now" can start below its fold — the user would open the
  // list and see only steps they had already finished. Pull the active row into
  // the window whenever the list opens or the active step moves.
  // `block: "nearest"` is the non-destructive form: shortest scroll, and no
  // movement at all when the row is already in view.
  useEffect(() => {
    if (!listOpen) return;
    listRef.current
      ?.querySelector<HTMLElement>('[aria-current="step"]')
      ?.scrollIntoView({ block: "nearest" });
  }, [listOpen, view.activeId]);

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
        <ol
          ref={listRef}
          // CAPPED + INTERNALLY SCROLLED. The list is opt-in, but opening it
          // used to hand the user back the thing they complained about: 393px
          // of an 844px iPhone (46.6%) on five steps, 437px (51.8%) on the
          // six-step cooler rig, and growing with every step ever added. The
          // cap is a WINDOW of two-and-a-half 44px rows: the half row is the
          // scroll affordance (a flush cut looks like the end of the list), and
          // the 26dvh arm takes over in landscape, where 7rem would be a third
          // of the screen. Because the window is fixed, the bar's expanded
          // height no longer depends on how many steps exist.
          //
          // This has to be the <ol>'s own scroll region, not the Overlay body's:
          // .overlay-body already scrolls at 70dvh, so without this the list
          // pushes the instruction and the Skip/Next row out of the clamped
          // surface and the user has to scroll the bar to reach its buttons —
          // worst in landscape, where 70dvh is 273px and the buttons fall off.
          className="flex flex-col border-b border-line px-1 py-1
            max-h-[min(7rem,26dvh)] overflow-y-auto overscroll-contain"
        >
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
              it buys ~44px of width back on a 390px row.
              The complete branch is the same case, MEASURED: with every step
              done, this whole card renders off `view.complete` and ignores
              `activeIndex`, so pressing Back walked its own aria-label back a
              step ("Back to Cool the camera" -> "Back to Pick a target") and
              changed NOTHING else on screen — same title, same body, same
              height, same list. A control whose only effect is to relabel
              itself is worse than absent: the user presses it expecting to
              review a step and concludes the bar is broken. */}
          {!atFirst && !view.complete && (
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
