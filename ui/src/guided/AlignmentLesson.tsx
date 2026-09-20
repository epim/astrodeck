import { useEffect, useState } from "react";
import { useStore } from "../store";
import { AlignmentScene } from "./AlignmentScene";
import { LessonActions } from "./LessonActions";

export function AlignmentLesson({onContinue}: {onContinue:()=>void}) {
  const south = (useStore(s=>s.site)?.latitude ?? 0) < 0;
  const [step,setStep] = useState(0);
  const [playing,setPlaying] = useState(false);
  useEffect(()=>{document.getElementById("main-content")?.scrollTo?.({top:0});},[step]);
  const lessons = [
    ["Line up the axis that tracks the sky", `Earth turns, and the stars appear to move. Polar alignment points the mount's tracking axis toward the celestial pole so it can follow them. Start with the mount facing true ${south?"south":"north"} and its latitude scale set for your location.`],
    ["Let AstroDeck measure", "Three-point polar alignment takes images at three positions as the mount rotates in right ascension (RA). Keep clear while it moves. These images reveal how far the mount's axis misses the celestial pole."],
    ["For a large error, reposition the whole setup", "Stop alignment and wait for the mount to stop. Reposition the tripod as your mount's manual describes, supporting or unloading the equipment as needed. Watch the feet against the ground grid: the tripod, mount and telescope all move together."],
    ["Measure again after a rough move", "Start a fresh alignment after repositioning the tripod. The old measurements no longer describe its position. A large-error warning means the live estimate needs a new measurement; there isn't one restart threshold that suits every mount."],
    ["Adjust sideways at the mount base", "Once the live adjustment view appears, use the mount's azimuth adjusters to reduce the sideways error. Don't turn the RA clutch or drive axis to correct polar alignment. Follow your mount's instructions for loosening and securing its adjusters."],
    ["Adjust the tilt", "Use the altitude adjuster to reduce the up-and-down error. Turn just one adjustment at a time and wait for a new reading. As you get close, make smaller turns. Follow the live directions; the drawing here is only an example."],
    ["Secure it and check once more", "Tighten the locks as your mount's manual describes, then wait for another measurement: tightening can shift the alignment. Guided setup aims for two arcminutes or less. An arcminute is 1/60 of a degree."],
    ["Finish with a fresh measurement", "When the error is within two arcminutes, the live view offers a final check. If the session is still measuring, secure the locks and wait for a new reading. If it has finished, confirm the locks were already secure for that reading, or run alignment again after securing them. Then choose your first target."],
  ];
  useEffect(()=>{if(!playing)return;const timer=setInterval(()=>setStep(s=>{if(s===7){setPlaying(false);return s;}return s+1;}),8000);return()=>clearInterval(timer);},[playing]);
  return <section className="guided-alignment-lesson guided-lesson-page guided-choice-card">
    <AlignmentScene step={step} playing={playing} south={south}/>
    <div className="guided-lesson-copy"><small>How alignment works · {step+1} of 8</small><h2>{lessons[step][0]}</h2><p>{lessons[step][1]}</p>
    <LessonActions><div className="guided-button-row">
      <button className="btn" disabled={step===0} onClick={()=>{setPlaying(false);setStep(s=>s-1);}}>Previous</button>
      <button className="btn" onClick={()=>{if(step===7)setStep(0);setPlaying(p=>!p);}}>{playing?"Pause example":"Play example"}</button>
      {step<7 && <button className="btn btn-accent" onClick={()=>{setPlaying(false);setStep(s=>s+1);}}>Next</button>}
    </div><button className={`btn ${step===7?"btn-accent":""}`} onClick={onContinue}>{step===7?"Open alignment controls →":"Skip lesson →"}</button></LessonActions><small>Opening the alignment controls won't start the mount.</small></div>
  </section>;
}
