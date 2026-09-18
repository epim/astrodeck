import { useEffect, useRef } from 'react';
import type { PhotosphereSweep } from './photosphere';
import { projectRay, skyVector, type V3 } from './photosphereGeometry';

/** Canvas lives in the video panel. Pose is read each animation frame instead
 * of making React rebuild the sheet at sensor frequency. */
export function PhotosphereDome({ sweep, active }: { sweep: PhotosphereSweep | null; active: boolean }) {
  const ref=useRef<HTMLCanvasElement>(null);
  useEffect(()=>{
    if(!sweep || !active || !ref.current)return;
    const canvas=ref.current,ctx=canvas.getContext('2d'); if(!ctx)return;
    const reduced=window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
    let raf=0,last=0;
    const seen=new Set<number>();
    let sparks: {x:number;y:number;at:number}[]=[];
    const draw=(now:number)=>{
      raf=requestAnimationFrame(draw); if(now-last<30)return;last=now;
      const box=canvas.getBoundingClientRect(); if(!box.width||!box.height)return;
      const ratio=Math.min(window.devicePixelRatio||1,2),w=box.width,h=box.height;
      if(canvas.width!==Math.round(w*ratio)||canvas.height!==Math.round(h*ratio)){canvas.width=Math.round(w*ratio);canvas.height=Math.round(h*ratio);}
      ctx.setTransform(ratio,0,0,ratio,0,0);ctx.clearRect(0,0,w,h);
      const basis=sweep.cameraBasis;if(!basis)return;
      const aspect=sweep.aspectRatio, vw=Math.min(w,h*aspect),vh=vw/aspect,ox=(w-vw)/2,oy=(h-vh)/2;
      // Optical centre, shared by projection and image capture. A ring near
      // the bottom looked like an aiming target but left cells outside the image.
      const aimX=ox+vw/2,aimY=oy+vh/2;
      const at=(ray:V3)=>{const p=projectRay(ray,basis,vw,vh,sweep.lens);return p?{x:p.x+ox,y:p.y+oy}:null;};
      ctx.save();ctx.beginPath();ctx.rect(ox,oy,vw,vh);ctx.clip();
      // True level follows pitch and roll. It is not a fixed screen decoration.
      ctx.beginPath();let drawing=false;
      for(let az=0;az<=360;az+=2){const p=at(skyVector(az,0));if(!p){drawing=false;continue;}if(drawing)ctx.lineTo(p.x,p.y);else ctx.moveTo(p.x,p.y);drawing=true;}
      ctx.strokeStyle='#f4dea2aa';ctx.lineWidth=1.4;ctx.setLineDash([5,7]);ctx.stroke();ctx.setLineDash([]);
      for(const cell of sweep.cells){
        // Tilt alone locates the zenith, but cannot locate azimuth cells.
        if(!sweep.compassReady && cell.alt<89)continue;
        const center=at(cell.center),corners=cell.vertices.map(at);
        if(!center||corners.some(p=>!p))continue;
        if(center.x<ox-80||center.x>ox+vw+80||center.y<oy-80||center.y>oy+vh+80)continue;
        const colour=!sweep.isRecording?'#9aaebc88':cell.captured?'#78edb9':'#74bfff';
        ctx.beginPath();corners.forEach((p,i)=>i?ctx.lineTo(p!.x,p!.y):ctx.moveTo(p!.x,p!.y));ctx.closePath();
        ctx.fillStyle=cell.captured?'#43cc881a':'#489be80c';ctx.fill();
        ctx.strokeStyle=colour;ctx.lineWidth=cell.captured?1.4:1.1;ctx.stroke();
        ctx.beginPath();ctx.arc(center.x,center.y,3.3,0,Math.PI*2);ctx.fillStyle=colour;ctx.fill();
        ctx.beginPath();ctx.arc(center.x,center.y,7,0,Math.PI*2);ctx.strokeStyle=cell.captured?'#78edb944':'#74bfff66';ctx.lineWidth=1;ctx.stroke();
        if(cell.captured&&!seen.has(cell.id)){
          seen.add(cell.id);
          if(!reduced&&center.x>=ox&&center.x<=ox+vw&&center.y>=oy&&center.y<=oy+vh)sparks.push({...center,at:now});
        }
      }
      sparks=sparks.filter(s=>now-s.at<700);
      for(const s of sparks){
        const p=Math.min(1,(now-s.at)/700),t=p*p,ex=aimX,ey=aimY;
        const x=s.x+(ex-s.x)*t,y=s.y+(ey-s.y)*t-35*Math.sin(p*Math.PI);
        ctx.beginPath();ctx.arc(x,y,4*(1-p)+1,0,Math.PI*2);ctx.fillStyle=`rgba(128,255,195,${1-p*.75})`;ctx.shadowBlur=12;ctx.shadowColor='#72ffc0';ctx.fill();ctx.shadowBlur=0;
      }
      const target=sweep.aimTarget;
      const ready=sweep.isRecording&&target&&!target.captured;
      ctx.beginPath();ctx.arc(aimX,aimY,19,0,Math.PI*2);
      ctx.strokeStyle='#06121acc';ctx.lineWidth=6;ctx.stroke();
      ctx.strokeStyle=sweep.justCaptured?'#9affca':ready?'#74bfff':'#edf6fc';ctx.lineWidth=2;ctx.stroke();
      ctx.beginPath();ctx.moveTo(aimX-5,aimY);ctx.lineTo(aimX+5,aimY);ctx.moveTo(aimX,aimY-5);ctx.lineTo(aimX,aimY+5);ctx.lineWidth=1.3;ctx.stroke();
      const label=!sweep.isRecording?'Tap Start scan':sweep.justCaptured?'Captured ✓':target?.captured?'Captured':ready?'Hold here':'Aim at a blue dot';
      ctx.font='600 13px system-ui, sans-serif';ctx.textAlign='center';
      const labelWidth=ctx.measureText(label).width;
      ctx.fillStyle='#07121cdb';ctx.fillRect(aimX-labelWidth/2-8,aimY+27,labelWidth+16,24);
      ctx.fillStyle=sweep.justCaptured?'#9affca':'#edf6fc';ctx.fillText(label,aimX,aimY+44);
      ctx.restore();
    };
    raf=requestAnimationFrame(draw);
    return()=>cancelAnimationFrame(raf);
  },[sweep,active]);
  return <canvas ref={ref} className="photosphere-dome" aria-hidden="true" data-testid="photosphere-dome"/>;
}
