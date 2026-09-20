import { useState } from "react";
import { useStore } from "../store";
import type { ViewName } from "../types";
import { Icon, type IconName } from "../components/icons";
import { Segmented } from "../components/Segmented";
import { useIsViewer } from "../lib/caps";
import { useExperience } from "./experience";
import { guidedModel, type Change } from "./model";
import { WIZARD_STEPS, type WizardStep } from "./wizard";
import { stepBlocker, useGuidedSetup } from "./setup";

const STEP_ICONS: IconName[] = ["rig", "mount", "atlas", "focus", "align", "capture"];

export default function GuidedHome({ onOpen, onStart }: { onOpen: (view: ViewName) => void; onStart: (step: WizardStep) => void }) {
  const status = useStore((s) => s.status);
  const site = useStore((s) => s.site);
  const fresh = useStore((s) => s.wsPhase === "up" && !s.telemetryStale && s.status !== null);
  const sequence = useStore((s) => s.sequence);
  const focus = useStore((s) => s.focus);
  const polar = useStore((s) => s.polar);
  const isViewer = useIsViewer();
  const journey = useExperience((s) => s.journey);
  const setJourney = useExperience((s) => s.setJourney);
  const [change, setChange] = useState<Change>("same");
  const chooseChange=(next:Change)=>{
    setChange(next);
    if(next==="moved")useGuidedSetup.getState().invalidate("location");
    if(next==="equipment")useGuidedSetup.getState().invalidate("horizon");
  };
  const model = guidedModel({ status, site, fresh, sequence, focus, polar }, journey, change);
  const setMode = useExperience((s) => s.setMode);
  const pausedStep = useExperience((s) => s.wizard);
  const primary = model.operations[0];
  useGuidedSetup();
  const requestedStep = pausedStep ?? (journey === "first" ? "equipment" : model.recommended.id as WizardStep);
  const resumeStep = stepBlocker(requestedStep) ? [...WIZARD_STEPS].reverse().find(s=>!stepBlocker(s.id))!.id : requestedStep;
  return <main id="guided-content" tabIndex={-1} className="guided-home">
    <div className="guided-content">
      <div className="guided-heading">
        <div><span className="guided-eyebrow">Your observatory</span><p>Set up for tonight</p></div>
        <Segmented options={[{ value: "first", label: "First light" }, { value: "returning", label: "Another night" }]}
          value={journey} onChange={setJourney} ariaLabel="Your observing journey" />
      </div>
      <section className="guided-hero" aria-labelledby="guided-title">
        <div className="guided-hero-copy">
          <span className="guided-eyebrow">{status?.mode === "sim" ? "Simulator · practice without moving real equipment" : "Guided setup · early access"}</span>
          <h1 id="guided-title">{primary ? fresh ? "Your telescope has work in progress." : "Check your telescope's connection." : journey === "first" ? "Let's set up your telescope." : "Let's get ready for tonight."}</h1>
          <p>{primary ? fresh ? "Open the controls to see how it's going." : "The last update showed work in progress. Check the connection before starting anything else." : journey === "first"
            ? "Start with your camera and mount. We'll walk through the setup, then help you choose something to photograph."
            : "Your saved setup is still here. Check what's changed since last time before taking another image."}</p>
          {!fresh && <p className="guided-warning" role="status">We're not receiving live updates. The readings shown may be out of date.</p>}
          {isViewer && <p className="guided-warning">You can look through the steps. To control equipment, sign in with an operator account.</p>}
          <button className="guided-primary" onClick={() => primary ? onOpen(primary.view) : onStart(resumeStep)}>
            {primary ? "Follow current operation" : pausedStep ? "Resume walkthrough" : "Start walkthrough"}<Icon name="arrow-right" size={18} />
          </button>
          <span className="guided-action-note">Opening a step won't move the telescope or start an exposure.</span>
        </div>
        <div className="guided-orbit" aria-hidden="true">
          <div className="guided-orbit-ring guided-orbit-ring-outer"/><div className="guided-orbit-ring"/>
          <span className="guided-orbit-star guided-orbit-star-one"/><span className="guided-orbit-star guided-orbit-star-two"/>
          <Icon name="mount" size={58}/>
        </div>
      </section>
      <div className="guided-status" aria-label="Current setup summary">
        {[ ["Camera", !fresh ? "Status unavailable" : model.camera ? "Connected" : "Not connected"],
           ["Mount", !fresh ? "Status unavailable" : model.mount ? "Connected" : "Not connected"],
           ["Location", site == null ? "Status unavailable" : model.siteSaved ? "Saved · confirm tonight" : "Not set"] ].map(([label, value]) =>
          <div key={label}><span>{label}</span><strong>{value}</strong></div>)}
      </div>
      {model.operations.length > 0 && <section className="guided-operation-list" aria-label="Operations to follow">
        {model.operations.map((op) => <button key={op.view} onClick={() => onOpen(op.view)}><Icon name="clock" size={18}/>{!fresh ? "Last reported: " : ""}{op.title}<Icon name="arrow-right" size={16}/></button>)}
      </section>}
      {journey === "returning" && <section className="guided-returning" aria-labelledby="returning-title">
        <div><h2 id="returning-title">What changed since last time?</h2><p>We'll suggest where to start. You'll still need to check the mount's alignment at the telescope.</p></div>
        <div role="group" aria-label="Changes to tonight's setup">
          {([{ id: "same", label: "Same place and equipment" }, { id: "moved", label: "I moved the telescope" }, { id: "equipment", label: "I changed equipment" }] as const).map((choice) =>
            <button key={choice.id} aria-pressed={change === choice.id} onClick={() => chooseChange(choice.id)}>{choice.label}</button>)}
        </div>
      </section>}
      <section aria-labelledby="guided-path-title">
        <div className="guided-section-heading"><h2 id="guided-path-title">{journey === "first" ? "From setup to your first image" : "Your checks for tonight"}</h2><span>Each step checks what the next one needs</span></div>
        <div className="guided-steps">
          {model.steps.map((step, i) => <article key={step.id} className={`guided-step ${step.id === model.recommended.id ? "guided-step-next" : ""}`}>
            <div className="guided-step-top"><Icon name={STEP_ICONS[i]} size={23}/><span>{step.id === model.recommended.id ? "Suggested next" : `0${i + 1}`}</span></div>
            <h3>{step.title}</h3><p>{step.description}</p><span className="guided-evidence">{step.evidence}</span>
            <button onClick={() => onStart(step.id as WizardStep)}>Review this step<Icon name="arrow-right" size={16}/></button>
          </article>)}
        </div>
      </section>
      <section className="guided-shortcuts" aria-label="More ways to explore">
        <button onClick={() => onOpen("capture")}><Icon name="capture" size={22}/><span><strong>Capture a frame</strong><small>Open the camera controls</small></span><Icon name="arrow-right" size={16}/></button>
        <button onClick={() => onOpen("flows")}><Icon name="bridge" size={22}/><span><strong>Build an AstroFlow</strong><small>Arrange the tasks you want to run</small></span><Icon name="arrow-right" size={16}/></button>
        <button onClick={() => onOpen("report")}><Icon name="download" size={22}/><span><strong>Review your night</strong><small>Results, frames and reports</small></span><Icon name="arrow-right" size={16}/></button>
      </section>
      <footer className="guided-footer"><p>Guided setup checks the saved horizon and suggests an alignment field. You confirm each move and make the physical adjustments at the mount.</p><button onClick={() => setMode("pro")}>Open Pro workspace</button></footer>
    </div>
  </main>;
}
