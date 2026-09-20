import { useState } from "react";
import { AlignmentScene } from "./AlignmentScene";

/** The illustration identifies the part to move. Real solve directions remain
 * beside the reticle; screw handedness depends on the user's mount. */
export function AlignmentCoach({total,az,alt,south,running,blocked}:{total:number;az:number;alt:number;south:boolean;running:boolean;blocked:boolean}) {
  const [axis,setAxis]=useState<"az"|"alt"|null>(null);
  const [paused,setPaused]=useState(false);
  const coarse=total>30;
  const selected=axis??(Math.abs(az)>=Math.abs(alt)?"az":"alt");
  return <aside className="guided-alignment-coach" aria-label="Adjustment example">
    <AlignmentScene step={coarse?2:selected==="az"?4:5} playing={!paused&&!blocked} south={south} compact loop/>
    <div><small>Movement example · exaggerated for clarity</small><h3>{coarse?"Reposition the whole setup":selected==="az"?"Turn the mount sideways":"Tilt the mount up or down"}</h3>
      <p>{blocked?"Wait for a reliable measurement before adjusting anything.":coarse?`${running?"Stop alignment and wait for all movement to stop. ":""}Support or unload the equipment as your mount's manual describes, then reposition the tripod. Start a new alignment after moving it.`:!running?"The measurement session has ended. If you make another adjustment, run alignment again to check it.":"Keep the tripod feet still. Turn one adjuster a little, then wait for the next image. Follow the live arrows beside the error readings; the example shows which part moves."}</p>
      {!coarse&&<div className="guided-coach-tabs"><button className="btn" aria-pressed={selected==="az"} onClick={()=>setAxis("az")}>Sideways · azimuth</button><button className="btn" aria-pressed={selected==="alt"} onClick={()=>setAxis("alt")}>Tilt · altitude</button></div>}
      {!coarse&&<small>Use the mount's polar adjustment bolts. The RA and Dec clutches stay locked.</small>}
      <button className="guided-text-button" onClick={()=>setPaused(v=>!v)}>{paused?"Play example":"Pause example"}</button>
    </div>
  </aside>;
}
