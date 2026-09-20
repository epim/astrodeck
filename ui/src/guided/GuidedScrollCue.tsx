import { useEffect, useState } from "react";
import { useExperience } from "./experience";

/** Lives in the fixed wizard footer, so a short screen always reveals that
 * its working pane has more content. Re-measure on async chart/image changes. */
export function GuidedScrollCue() {
  const step=useExperience(s=>s.wizard);
  const [more,setMore]=useState(false);
  useEffect(()=>{
    const pane=document.getElementById(["location","horizon","image"].includes(step??"")?"wizard-panel":"main-content");
    if(!pane)return;
    const update=()=>setMore(pane.scrollHeight-pane.clientHeight-pane.scrollTop>28);
    const resize=typeof ResizeObserver!=="undefined"?new ResizeObserver(update):null;
    resize?.observe(pane);for(const child of pane.children)resize?.observe(child);
    const mutation=new window.MutationObserver(update);mutation.observe(pane,{childList:true,subtree:true,attributes:true});
    pane.addEventListener("scroll",update,{passive:true});window.addEventListener("resize",update);update();
    return()=>{resize?.disconnect();mutation.disconnect();pane.removeEventListener("scroll",update);window.removeEventListener("resize",update);};
  },[step]);
  if(!more)return null;
  return <button className="guided-scroll-cue" onClick={()=>{
    const pane=document.getElementById(["location","horizon","image"].includes(step??"")?"wizard-panel":"main-content");
    pane?.scrollBy({top:pane.clientHeight*.7,behavior:window.matchMedia?.("(prefers-reduced-motion: reduce)").matches?"auto":"smooth"});
  }}>More below <span aria-hidden="true">↓</span></button>;
}
