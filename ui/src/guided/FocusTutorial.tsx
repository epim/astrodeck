import { useEffect, useState } from "react";
import CameraDial from "../components/ui/CameraDial";
import { cameraDialCategories } from "../components/ui/CameraPickers";
import FocusPod, { POD_BOTTOM_PX } from "../components/focus/FocusPod";
import { LessonActions } from "./LessonActions";

const tips: [string,string][] = [
  ["Try the camera dial", "Tap the lower-left dial, choose a setting, then a value. Watch this practice sky change. These controls only affect the example; your telescope stays put."],
  ["Start with a short exposure", "Exposure is how long the camera gathers light. Try 2 seconds, then 5: faint stars become easier to see. Too much light makes bright stars saturate and lose detail. Narrowband filters may need longer."],
  ["Keep the star detail", "Binning combines neighboring pixels into larger image pixels. At 2×2, the image has half as many pixels across and down. Start at 1×1 for focusing so small changes in star size are easier to measure."],
  ["Take an image, then adjust", "Open the lower-right dial. Take a frame, start a loop, or try autofocus. The + and − controls nudge a motorized focuser; the step setting changes how far it moves. With a manual focuser, turn its knob and take another image. Aim for the smallest stars."],
];
const stars = Array.from({length:55},(_,i)=>({x:22+(i*137)%490,y:40+(i*83)%245,r:.7+(i%5)*.35}));
export function FocusTutorial({onDefaults, disabled, onComplete}: {onDefaults:()=>void; disabled:boolean; onComplete:()=>void}) {
  const [tip,setTip]=useState(0);
  const [exposure,setExposure]=useState(2),[gain,setGain]=useState(120),[bin,setBin]=useState(1),[offset,setOffset]=useState(30),[filter,setFilter]=useState<string|null>("L");
  const [position,setPosition]=useState(450),[step,setStep]=useState(100),[frames,setFrames]=useState(0);
  const [action,setAction]=useState<"capture"|"loop"|"focus"|null>(null),[progress,setProgress]=useState(0);
  const [explanation,setExplanation]=useState<[string,string]|null>(null),[curve,setCurve]=useState<{position:number;size:number}[]>([]);
  const radius=1.3+Math.abs(position)/180;
  const explain=(title:string,body:string)=>setExplanation([title,body]);
  useEffect(()=>{document.getElementById("main-content")?.scrollTo?.({top:0});},[tip]);
  useEffect(()=>{
    if(!action)return;
    let tick=0;
    const timer=setInterval(()=>{
      tick++;
      if(action==="focus") {
        const p=[600,450,300,150,0,-150,0][Math.min(tick-1,6)];
        setPosition(p);setCurve(c=>[...c,{position:p,size:1.3+Math.abs(p)/180}]);setFrames(n=>n+1);
        if(tick===7){setAction(null);explain("Focus found", "The smallest stars sit at the bottom of the curve. Autofocus returns to that position. On your telescope it measures real stars, checks the fit, and reports a failure if it cannot find a reliable focus.");}
      } else {
        setProgress(tick%4/4);
        if(tick%4===0){setFrames(n=>n+1);if(action==="capture")setAction(null);}
      }
    },action==="focus"?650:250);
    return()=>clearInterval(timer);
  },[action]);
  const start=(mode:"capture"|"loop"|"focus")=>{
    setProgress(0);setAction(mode);
    if(mode==="focus"){setCurve([]);explain("Autofocus is trying several positions", "Watch the stars shrink and the curve dip. Autofocus changes the focuser position and measures star size in pixels, then returns to the smallest reliable result. This example runs faster than a real sweep.");}
    else explain(mode==="loop"?"A new frame, again and again":"Taking a practice frame", "Capture takes one image. Loop keeps taking fresh images so you can watch focus change. Stop ends the loop. This practice scene responds immediately to settings; the real camera shows changes in its next frame.");
  };
  const categories=cameraDialCategories({values:{exposure_s:exposure,gain,binning:bin,offset,filter},currentFilter:filter,filters:["L","R","G","B","Ha"],
    onExposure:v=>{setExposure(v);explain(...tips[1]);},
    onGain:v=>{setGain(v);explain("Gain boosts the signal", "Higher gain makes the stars and background fluctuations brighter. It does not gather more light, and bright stars can saturate sooner. Keep your camera's current gain as a starting point; the numbers vary by camera.");},
    onBinning:v=>{setBin(v);explain(...tips[2]);},
    onOffset:v=>{setOffset(v);explain("Offset keeps the darkest pixels above zero", "Offset adds a small baseline to the recorded values. Too little can clip faint detail to black; more does not reveal extra light. Use your camera's recommended value. The lifted background here exaggerates the effect.");},
    onFilter:v=>{setFilter(v);explain("A filter chooses which light reaches the camera", "L lets a broad range through. R, G and B select parts of that light; Ha selects a narrow band, so stars look fainter at the same exposure. These example colors illustrate the bands. A mono camera still records a grayscale image.");},
  });
  const color=filter==="R"||filter==="Ha"?"#ffb6aa":filter==="G"?"#b5e6cb":filter==="B"?"#adceff":"#e7eef9";
  const brightness=Math.min(1,.13+Math.sqrt(exposure/2)*(.25+gain/600)*(filter==="Ha"?.18:filter==="L"?1:.55));
  const text=explanation??tips[tip];
  return <section className="guided-focus-tutorial guided-lesson-page" aria-label="Camera dial tutorial">
    <div className="guided-practice-stage">
      <span className="guided-example-label">Practice sky · no equipment moves</span>
      <svg className="guided-practice-stars" viewBox={`0 0 ${540/bin} ${340/bin}`} aria-label={`Simulated stars: ${exposure}s, gain ${gain}, binning ${bin} by ${bin}, filter ${filter}`} role="img" style={{background:`rgb(${5+Math.min(offset,300)/7},${10+Math.min(offset,300)/7},${19+Math.min(offset,300)/7})`}}>
        {stars.map((s,i)=><g key={i} fill={color} opacity={brightness}>
          <circle cx={Math.round(s.x/bin)} cy={Math.round(s.y/bin)} r={Math.max(1,Math.round(s.r*radius/bin))} opacity=".3"/>
          <circle cx={Math.round(s.x/bin)} cy={Math.round(s.y/bin)} r={Math.max(.5,Math.round(s.r*radius/bin/2))}/>
        </g>)}
        {gain>120&&stars.slice(0,30).map((s,i)=><rect key={i} x={(s.x+11)/bin} y={(s.y+13)/bin} width={1} height={1} fill={color} opacity={Math.min(.6,gain/1000)}/>)}
      </svg>
      <span className="guided-practice-readout" role="status">{action==="focus"?"Trying focus positions…":action?"Capturing…":frames?`Practice frame ${frames}`:"Try either dial"} · star radius {radius.toFixed(1)} px</span>
      <CameraDial label="Practice camera settings" summary={`${exposure}s g${gain}`} side="left" bottom={POD_BOTTOM_PX} categories={categories}/>
      <FocusPod hfr={radius} prevHfr={null} hfrState="measured" exposureProgress={action&&action!=="focus"?progress:null} exposureNote={null} exposureNoteTone={null} captureBlocked={null}
        stepValues={[10,50,100,250]} onStep={v=>{setStep(v);explain("How far each nudge moves", "A larger step moves the focuser farther with each + or − tap. Use smaller steps as the stars sharpen. These counts depend on the focuser; they are not a universal distance.");}}
        looping={action==="loop"} starting={null} exposing={action==="capture"} step={step}
        shootReason={action?"Stop the practice run first":null} loopReason={action?"Stop the practice run first":null} stopReason={null} focuserReason={action==="focus"?"Autofocus is running":null} autofocusReason={action?"Stop the practice run first":null}
        onShoot={()=>start("capture")} onLoop={()=>start("loop")} onStop={()=>{setAction(null);explain("Practice stopped", "The last frame stays on screen. You can change a setting or take another image whenever you're ready.");}}
        onNudge={v=>{setPosition(p=>Math.max(-900,Math.min(900,p+v)));explain("Watch the star size", "One direction makes the stars smaller until you pass through focus; then they grow again. Reverse direction if they get bigger. On the telescope, take a fresh frame or use a loop after each adjustment.");}}
        onAutofocus={()=>start("focus")}/>
    </div>
    <div className="guided-practice-explanation" aria-live="polite"><small>Try it here · {tip+1} of {tips.length}</small><h3>{text[0]}</h3><p>{text[1]}</p>
      {curve.length>0&&<svg className="guided-practice-curve" viewBox="0 0 280 100" role="img" aria-label="Practice autofocus curve: smaller stars are lower"><path d="M15 10V85H265" fill="none" stroke="currentColor" opacity=".3"/><polyline points={[...curve].sort((a,b)=>a.position-b.position).map(v=>`${25+(v.position+150)/750*230},${90-v.size*16}`).join(" ")} fill="none" stroke="var(--accent)" strokeWidth="2"/>{curve.map((v,i)=><circle key={i} cx={25+(v.position+150)/750*230} cy={90-v.size*16} r="3" fill="var(--accent)"/>)}</svg>}
      <small className="guided-practice-note">Illustration, not a camera calibration. Practice settings stay separate from your real camera.</small>
    </div>
    <LessonActions><div className="guided-button-row">
      {tip===1&&<button className="btn" disabled={disabled} onClick={onDefaults}>Use 2s / 1×1 on my camera</button>}
      <button className="btn" disabled={tip===0} onClick={()=>{setTip(tip-1);setExplanation(null);}}>Previous</button>
      {tip<tips.length-1&&<button className="btn" onClick={()=>{setTip(tip+1);setExplanation(null);}}>Next tip</button>}
    </div><button className="btn btn-accent" onClick={onComplete}>Open camera view →</button></LessonActions>
  </section>;
}
