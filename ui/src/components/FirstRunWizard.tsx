// FirstRunWizard.tsx — NOV-2-3/4: the non-modal docked first-run overlay
// (first-run wizard design spec §3, Tasks 4 + 5). Builds a plain snapshot
// from narrow store selectors, hands it to the tested `computeWizard`, and
// renders progress + the active step + a single primary CTA that deep-links
// into the ALREADY-BUILT panels (Settings/SitePanel, Equipment, Atlas,
// Capture) — this component never re-implements connect/site/capture, it
// points at them and watches the store for completion.
//
// CRITICAL: unlike ConfirmDialog (a focus-trapping modal this borrows its
// scrim/token *look* from), this card must NEVER trap focus or block pointer
// events over the rest of the screen — the user has to be able to keep
// operating SitePanel / the Equipment dropdowns / the cooler input while the
// card is up. There is no full-viewport scrim element here at all: the card
// is just a small overlay docked in a corner (bottom sheet on phone). That is
// why it renders through <Overlay variant="corner" modal={false}
// dismissOnOutside={false} autoFocus={false}> — every one of those flags is
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
// which does not.

import { useEffect, useRef, useState } from "react";
import {
  useStore,
  useWizardOpen,
  useWizardStepId,
  useHasSeen,
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
import { Overlay } from "./Overlay";

export default function FirstRunWizard(): JSX.Element | null {
  const open = useWizardOpen();
  const manualId = useWizardStepId();
  const site = useSite();
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

  const goBack = () => {
    const i = view.activeIndex;
    if (i > 0) setWizardStep(view.steps[i - 1].id);
  };
  const goSkip = () => {
    const i = view.activeIndex;
    if (i < view.steps.length - 1) setWizardStep(view.steps[i + 1].id);
  };
  const revisit = (id: WizardStepId) => setWizardStep(id);

  const finish = () => {
    closeWizard();
    useStore.getState().enqueueToast({ level: "success", title: "Setup complete — clear skies!" });
  };

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
      bodyClassName="p-4 flex flex-col gap-3"
      head={
        /* header: progress + Later/X. Dismiss marks the wizard permanently
           seen (closeWizard) — the interstitial's "Open the setup guide" is
           the deliberate way back in. */
        <div className="flex items-center justify-between gap-3 px-4 py-2">
          <div className="flex items-center gap-2 min-w-0">
            <h2 className="panel-title !mb-0 truncate">Get set up</h2>
            <span className="text-[11px] text-dim mono shrink-0">
              {view.doneCount}/{view.total}
            </span>
          </div>
          <button
            type="button"
            aria-label="Later"
            title="Later"
            className="btn btn-touch inline-flex items-center justify-center p-0 shrink-0"
            onClick={closeWizard}
          >
            <Icon name="x" size={14} />
          </button>
        </div>
      }
    >
      {/* compact step rail — done steps are revisitable, always enabled (free
          navigation, so no honest-disabled treatment is needed here). */}
      <ol className="flex flex-col gap-1">
        {view.steps.map((st) => {
          const isActive = st.id === view.activeId;
          return (
            <li key={st.id}>
              <button
                type="button"
                onClick={() => revisit(st.id)}
                className={`w-full flex items-center gap-2 text-left text-xs rounded px-2 py-1.5
                  ${isActive ? "bg-raise text-ink" : "text-dim hover:text-ink"}`}
              >
                <span
                  aria-hidden
                  className={`shrink-0 inline-flex items-center justify-center w-4 h-4 rounded-full text-[10px]
                    ${st.done ? "text-good" : "text-faint"}`}
                >
                  {st.done ? <Icon name="check" size={12} /> : "◦"}
                </span>
                <span className="truncate">{st.title}</span>
              </button>
            </li>
          );
        })}
      </ol>

      {/* active step body / finish state */}
      {view.complete ? (
        <div className="flex flex-col gap-3">
          <p className="text-sm text-ink leading-relaxed">You're all set — clear skies!</p>
          <button type="button" className="btn btn-accent min-h-11 self-end" onClick={finish}>
            Finish
          </button>
        </div>
      ) : (
        <div className="flex flex-col gap-3">
          <div>
            <h3 className="text-sm font-semibold text-ink mb-1">{active.title}</h3>
            <p className="text-xs text-dim leading-relaxed">{active.body}</p>
          </div>
          <div className="flex items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              {/* #24: `title` is never the sole channel for a state a user can
                  hit — it does not fire on touch. These two are no-ops at the
                  ends of the rail, so say so in the accessible name and dim
                  them; never the native `disabled` attribute. */}
              <button
                type="button"
                className={`btn min-h-11 ${view.activeIndex === 0 ? "opacity-50" : ""}`}
                onClick={goBack}
                aria-disabled={view.activeIndex === 0 || undefined}
                aria-label={view.activeIndex === 0 ? "Back — already at the first step" : "Back"}
                title={view.activeIndex === 0 ? "Already at the first step" : "Back"}
              >
                Back
              </button>
              <button
                type="button"
                className={`btn min-h-11 ${view.activeIndex === view.steps.length - 1 ? "opacity-50" : ""}`}
                onClick={goSkip}
                aria-disabled={view.activeIndex === view.steps.length - 1 || undefined}
                aria-label={view.activeIndex === view.steps.length - 1 ? "Skip — already at the last step" : "Skip"}
                title={view.activeIndex === view.steps.length - 1 ? "Already at the last step" : "Skip"}
              >
                Skip
              </button>
            </div>
            <button
              type="button"
              className="btn btn-accent min-h-11"
              onClick={() => setView(active.view)}
            >
              {active.cta}
            </button>
          </div>
        </div>
      )}
    </Overlay>
  );
}

// Re-export the step-def list for anything (tests, future coach marks) that
// wants the raw copy without pulling in the whole component tree.
export { WIZARD_STEPS };
