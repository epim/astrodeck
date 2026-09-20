import { lazy, Suspense, useEffect, useRef } from "react";
import { Icon } from "../components/icons";
import { useExperience } from "./experience";
import { WIZARD_STEPS, wizardStep, type WizardStep } from "./wizard";
import type { ViewName } from "../types";
import { useStore } from "../store";
import { useGuidedSetup, useStepBlocker } from "./setup";
import { GuidedScrollCue } from "./GuidedScrollCue";

const SitePanel = lazy(() => import("./GuidedLocation"));
const FirstImage = lazy(() => import("./GuidedFirstImage"));
const Horizon = lazy(() => import("../next/hubs/sky/sheets/horizon").then(m => ({ default: m.HorizonSheet })));

export function WizardHeader({ step, onStep }: { step: WizardStep; onStep: (step: WizardStep) => void }) {
  const current = wizardStep(step);
  const index = WIZARD_STEPS.indexOf(current);
  const heading = useRef<HTMLHeadingElement>(null);
  const journey = useExperience(s => s.journey);
  const view = useStore(s => s.view);
  const title = step === "image" && journey === "returning" ? "Take tonight's first image" : current.title;
  useEffect(() => {
    // Let arrow keys keep working in the Guided/Pro radio group when a mode
    // switch reveals this header; step navigation focuses the new heading.
    if (!document.activeElement?.closest(".experience-switch")) heading.current?.focus({ preventScroll: true });
  }, [step, view]);
  return <header className="guided-wizard-head">
    <div className="guided-wizard-heading">
      <div><span className="guided-eyebrow">{journey === "first" ? "First light" : "Tonight's setup"} · Step {index + 1} of {WIZARD_STEPS.length}</span>
        <h1 ref={heading} tabIndex={-1}>{title}</h1></div>
      <button className="btn" aria-label="Pause walkthrough" onClick={() => useExperience.getState().showHome()}>Pause<span className="guided-pause-label"> walkthrough</span></button>
    </div>
    <nav aria-label="Setup steps" className="guided-wizard-progress">
      {WIZARD_STEPS.map((item, i) => <button key={item.id} aria-label={`${i + 1}. ${item.label}`} aria-current={step === item.id ? "step" : undefined} onClick={() => onStep(item.id)}>
        <span>{i + 1}</span><span>{item.label}</span>
      </button>)}
    </nav>
  </header>;
}

export function WizardIntro({ step }: { step: WizardStep }) {
  return <p className="guided-wizard-intro">{wizardStep(step).help}</p>;
}

/** Location drafts survive Back/Continue, pausing, and a presentation switch.
 * Camera/sensor editors mount only while visible so leaving releases devices. */
export function WizardPanels({ step, active, onStep }: { step: WizardStep | null; active: boolean; onStep: (step: WizardStep) => void }) {
  const siteId = useGuidedSetup(s=>s.siteId);
  const visitedLocation = useRef(false);
  if (active && step === "location") visitedLocation.current = true;
  const visible = active && (step === "location" || step === "horizon" || step === "image");
  return <main id="wizard-panel" tabIndex={-1} hidden={!visible} className="guided-wizard-panel">
    {visible && <WizardIntro step={step!}/>}
    <Suspense fallback={<p role="status">Opening setup controls…</p>}>
      {active && step === "image" && <FirstImage/>}
      {visitedLocation.current && <div hidden={!active || step !== "location"}><SitePanel active={active && step === "location"} onContinue={() => onStep("horizon")}/></div>}
      {active && step === "horizon" && <Horizon depth={0} params={{site:siteId ?? "current"}} guided onSaved={() => useGuidedSetup.getState().complete("horizon")} onDirty={() => useGuidedSetup.getState().invalidate("horizon")}/>}
    </Suspense>
  </main>;
}

export function WizardFooter({ step, onStep, onTool }: { step: WizardStep; onStep: (step: WizardStep) => void; onTool: (view: ViewName) => void }) {
  const footer = useRef<HTMLElement>(null);
  useEffect(() => {
    const node = footer.current;
    if (!node) return;
    const update = () => document.documentElement.style.setProperty("--guided-footer-height", `${Math.ceil(node.getBoundingClientRect().height)}px`);
    const resize = typeof ResizeObserver !== "undefined" ? new ResizeObserver(update) : null;
    resize?.observe(node);
    window.addEventListener("resize", update);
    update();
    return () => { resize?.disconnect(); window.removeEventListener("resize", update); document.documentElement.style.removeProperty("--guided-footer-height"); };
  }, []);
  const view = useStore(s => s.view);
  const index = WIZARD_STEPS.findIndex(item => item.id === step);
  const next = WIZARD_STEPS[index + 1];
  const blocker = useStepBlocker(next?.id ?? "image");
  return <footer ref={footer} className="guided-wizard-footer" aria-label="Walkthrough navigation">
    <GuidedScrollCue/>
    <div id="guided-lesson-actions"/>
    <button className="btn" onClick={() => index ? onStep(WIZARD_STEPS[index - 1].id) : useExperience.getState().showHome()}><Icon name="arrow-left" size={16}/>Back</button>
    <p id="guided-navigation-status" role="status" aria-atomic="true">{step === "location" ? "Save this site to use it for tonight and future visits." : blocker ?? "You control when equipment starts."}</p>
    {step === "location" ? <button type="submit" form="guided-location-form" className="btn btn-accent">Save site and continue<Icon name="arrow-right" size={16}/></button> : next ? <button className="btn btn-accent" disabled={!!blocker} aria-describedby={blocker ? "guided-navigation-status" : undefined} title={blocker ?? undefined} onClick={() => onStep(next.id)}>Continue<span className="guided-next-label"> to {next.label.toLowerCase()}</span><Icon name="arrow-right" size={16}/></button>
      : <div className="guided-wizard-last"><button className="btn" onClick={() => useExperience.getState().showHome()}>Return to your night</button><button className="btn btn-accent" onClick={() => onTool(view === "capture" ? "tonight" : "capture")}>{view === "capture" ? "Choose a target" : "Open camera controls"}<Icon name="arrow-right" size={16}/></button></div>}
  </footer>;
}
