import { useEffect, useRef, useState } from "react";

// A small software 3D renderer keeps this lesson usable on devices without
// WebGL. Meshes are shaded in world space, projected and sorted back to front.
// It is an illustration only: no rig coordinates or commands enter this file.
type V = [number, number, number];
type Face = { vertices: V[]; color: string; opacity: number };
const add = (a:V,b:V):V => [a[0]+b[0],a[1]+b[1],a[2]+b[2]];
const sub = (a:V,b:V):V => [a[0]-b[0],a[1]-b[1],a[2]-b[2]];
const mul = (a:V,s:number):V => [a[0]*s,a[1]*s,a[2]*s];
const dot = (a:V,b:V) => a[0]*b[0]+a[1]*b[1]+a[2]*b[2];
const cross = (a:V,b:V):V => [a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]];
const unit = (a:V) => mul(a,1/Math.max(.0001,Math.hypot(...a)));
const rotate = (p:V,axis:V,angle:number):V => add(add(mul(p,Math.cos(angle)),mul(cross(axis,p),Math.sin(angle))),mul(axis,dot(axis,p)*(1-Math.cos(angle))));
const pivot:V=[0,1.55,0];
const pole:V=unit([0,1,-1]);

export function AlignmentScene({step,playing,south,compact=false,loop=false}:{step:number;playing:boolean;south:boolean;compact?:boolean;loop?:boolean}) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const progress = useRef({step,elapsed:6000});
  const [side,setSide]=useState(false);
  useEffect(()=>{
    const el=canvas.current;
    if(!el)return;
    const ctx=el.getContext("2d");
    if(!ctx)return;
    const reduced=window.matchMedia?.("(prefers-reduced-motion: reduce)");
    if(progress.current.step!==step)progress.current={step,elapsed:playing?0:6000};
    else if(playing && progress.current.elapsed>=6000)progress.current.elapsed=0;
    const elapsed=progress.current.elapsed;
    let frame=0,last=0; const start=performance.now();
    const render=(now:number)=>{
      if(now-last<32 && playing && !reduced?.matches){frame=requestAnimationFrame(render);return;}
      last=now;
      const w=el.clientWidth || 560,h=el.clientHeight || 360,dpr=Math.min(window.devicePixelRatio||1,2);
      if(el.width!==Math.round(w*dpr)||el.height!==Math.round(h*dpr)){el.width=Math.round(w*dpr);el.height=Math.round(h*dpr);}
      ctx.setTransform(dpr,0,0,dpr,0,0);ctx.clearRect(0,0,w,h);
      if(playing&&!reduced?.matches)progress.current.elapsed=(loop?(elapsed+now-start)%6000:Math.min(6000,elapsed+now-start));
      const raw=reduced?.matches?1:progress.current.elapsed/6000;
      const t=loop?(1-Math.cos(raw*Math.PI*2))/2:raw*raw*(3-2*raw);
      const rough=step<2?.44:step===2?.44-.32*t:.12;
      const fine=step<4?0:step===4?-.12*t:-.12;
      const tilt=step<5?.14:step===5?.14*(1-t):0;
      const eye:V=side?[7,3.7,1.5]:[6,3.8,-6];
      const forward=unit(sub(compact&&step>=4?[0,1.65,0]:[0,1.45,0],eye));
      const right=unit(cross(forward,[0,1,0])),up=cross(right,forward);
      const project=(p:V)=>{const v=sub(p,eye),depth=dot(v,forward),scale=Math.min(w*1.9,h*2.1)*(compact&&step>=4?2.4:1)/depth;return {x:w*.5+dot(v,right)*scale,y:h*.49-dot(v,up)*scale,depth};};
      const colors=getComputedStyle(el);
      const accent=colors.getPropertyValue("--accent").trim()||"#8edaca";
      const text=colors.getPropertyValue("--text").trim()||"#e1eaf0";
      const line=(points:V[],color:string,width=1,dashed=false,alpha=1)=>{ctx.beginPath();points.forEach((p,i)=>{const q=project(p);if(i)ctx.lineTo(q.x,q.y);else ctx.moveTo(q.x,q.y);});ctx.strokeStyle=color;ctx.lineWidth=width;ctx.globalAlpha=alpha;ctx.setLineDash(dashed?[5,5]:[]);ctx.stroke();ctx.setLineDash([]);ctx.globalAlpha=1;};
      const label=(p:V,value:string,dx=0,dy=0)=>{if(compact)return;const q=project(p);ctx.font="12px 'IBM Plex Sans', sans-serif";ctx.fillStyle=text;ctx.globalAlpha=.88;ctx.fillText(value,Math.max(8,Math.min(w-ctx.measureText(value).width-8,q.x+dx)),Math.max(18,Math.min(h-12,q.y+dy)));ctx.globalAlpha=1;};
      // A fixed ground grid makes movement of all three feet unambiguous.
      for(let i=-3;i<=3;i++){line([[i*.5,0,-1.5],[i*.5,0,1.5]],text,1,false,.07);line([[-1.5,0,i*.5],[1.5,0,i*.5]],text,1,false,.07);}
      const ring:V[]=Array.from({length:65},(_,i)=>[Math.cos(i*Math.PI/32)*1.3,0,Math.sin(i*Math.PI/32)*1.3]);
      line(ring,accent,1,false,.22);
      label([0,0,-1.65],south?"TRUE SOUTH":"TRUE NORTH",-30,0);
      const faces:Face[]=[];
      let transform=(p:V)=>p;
      let opacity=1;
      // Array.map also passes an index. rigTransform's optional second argument
      // is an angle, so forwarding the callback directly twists each vertex.
      const face=(vertices:V[],color:string)=>faces.push({vertices:vertices.map(p=>transform(p)),color,opacity});
      const cylinder=(a:V,b:V,r:number,color:string,cap=color)=>{
        const axis=unit(sub(b,a)),u=unit(cross(axis,Math.abs(axis[1])>.9?[1,0,0]:[0,1,0])),v=cross(axis,u);
        const ends=[a,b].map(center=>Array.from({length:18},(_,i)=>add(center,add(mul(u,r*Math.cos(i*Math.PI/9)),mul(v,r*Math.sin(i*Math.PI/9))))));
        for(let i=0;i<18;i++)face([ends[0][i],ends[0][(i+1)%18],ends[1][(i+1)%18],ends[1][i]],color);
        face([...ends[0]].reverse(),color);face(ends[1],cap);
      };
      const box=(c:V,size:V,color:string)=>{
        const vertices:V[]=[[-1,-1,-1],[1,-1,-1],[1,1,-1],[-1,1,-1],[-1,-1,1],[1,-1,1],[1,1,1],[-1,1,1]].map(p=>[c[0]+p[0]*size[0]/2,c[1]+p[1]*size[1]/2,c[2]+p[2]*size[2]/2]);
        [[0,3,2,1],[4,5,6,7],[0,1,5,4],[3,7,6,2],[1,2,6,5],[0,4,7,3]].forEach(indices=>face(indices.map(i=>vertices[i]),color));
      };
      const rigTransform=(p:V,angle=rough):V=>rotate(p,[0,1,0],angle);
      const upper=(p:V):V=>rigTransform(add(pivot,rotate(rotate(sub(p,pivot),[1,0,0],tilt),[0,1,0],fine)));
      const tripod=()=>{
        transform=rigTransform;
        for(let i=0;i<3;i++){
          const a=i*Math.PI*2/3+.3;
          const foot:V=[Math.cos(a)*1.08,.045,Math.sin(a)*1.08];
          cylinder([Math.cos(a)*.17,1.3,Math.sin(a)*.17],foot,.052,"#8c9ca9");
          cylinder([foot[0],0,foot[2]],[foot[0],.08,foot[2]],.1,"#30404c");
          cylinder([0,.63,0],mul(add(foot,[0,.65,0]),.52),.018,"#5b6b77");
        }
        cylinder([0,1.2,0],[0,1.36,0],.25,"#4c606e");
      };
      
      tripod();opacity=1;
      transform=rigTransform;
      cylinder([0,1.36,0],[0,1.5,0],.2,"#293d4b");
      // Azimuth bolts stay at the base. Tilt screw is on the side of the head.
      cylinder([-.36,1.43,.13],[.36,1.43,.13],.037,"#9ca7ac");
      cylinder([-.4,1.43,.13],[-.32,1.43,.13],.085,step===4?"#87d9c0":"#536875");
      cylinder([.32,1.43,.13],[.4,1.43,.13],.085,step===4?"#87d9c0":"#536875");
      transform=upper;
      cylinder([0,1.5,.08],[0,1.96,-.35],.17,"#587381");
      cylinder([.22,1.55,.1],[.22,1.78,-.13],.035,"#8b9ca6");
      cylinder([.22,1.5,.15],[.22,1.56,.1],.095,step===5?"#87d9c0":"#536875");
      cylinder([0,1.98,-.12],[0,1.23,-.73],.035,"#8999a5");
      cylinder([0,1.42,-.57],[0,1.23,-.73],.16,"#334652");
      box([0,2.02,-.2],[.34,.15,.45],"#314653");
      const measureAngle=step===1?(t<.34?-.22:t<.67?0:.22):0;
      transform=p=>upper(add([0,2,-.2],rotate(sub(p,[0,2,-.2]),pole,measureAngle)));
      // The OTA is a long, broad hollow tube. A small lateral block is the
      // focuser; there is no oversized camera train to confuse the silhouette.
      const rear:V=[0,2.02,.46],front:V=[0,2.75,-.98];
      const tubeAxis=unit(sub(front,rear));
      cylinder(rear,front,.24,"#becdd3","#233c4c");
      cylinder(add(front,mul(tubeAxis,-.06)),add(front,mul(tubeAxis,.035)),.27,"#738f9e","#0d202d");
      cylinder(add(front,mul(tubeAxis,.037)),add(front,mul(tubeAxis,.04)),.205,"#172f40","#172f40");
      for(const fraction of [.22,.65]){const c=add(rear,mul(sub(front,rear),fraction));cylinder(c,add(c,mul(tubeAxis,.065)),.253,"#4a697a");}
      box([.3,2.62,-.73],[.23,.18,.2],"#526d7b");
      cylinder([.43,2.62,-.73],[.55,2.62,-.73],.07,"#304857");
      cylinder([.36,2.49,-.73],[.36,2.48,-.73],.095,"#91a9b5");
      // Painter's algorithm at face level, sufficient for these separated
      // convex parts. Each face gets directional lighting in world space.
      faces.sort((a,b)=>b.vertices.reduce((s,v)=>s+project(v).depth,0)/b.vertices.length-a.vertices.reduce((s,v)=>s+project(v).depth,0)/a.vertices.length);
      for(const f of faces){
        const normal=unit(cross(sub(f.vertices[1],f.vertices[0]),sub(f.vertices[2],f.vertices[0])));
        const light=.57+.43*Math.abs(dot(normal,unit([-2,4,3])));
        const rgb=f.color.match(/\w\w/g)!.map(c=>Math.round(parseInt(c,16)*light));
        ctx.beginPath();f.vertices.forEach((v,i)=>{const q=project(v);if(i)ctx.lineTo(q.x,q.y);else ctx.moveTo(q.x,q.y);});ctx.closePath();ctx.globalAlpha=f.opacity;ctx.fillStyle=`rgb(${rgb.join(",")})`;ctx.fill();ctx.strokeStyle=`rgba(6,20,30,${f.opacity*.15})`;ctx.lineWidth=.5;ctx.stroke();
      }
      ctx.globalAlpha=1;
      if(step===4||step===5){
        const c:V=step===4?[.41,1.43,.13]:[.22,1.5,.15];
        const a=t*Math.PI*1.4, tr=step===4?rigTransform:upper;
        line([tr(add(c,[0,Math.cos(a)*.07,Math.sin(a)*.07])),tr(add(c,[0,-Math.cos(a)*.07,-Math.sin(a)*.07]))],accent,3);
      }
      line([pivot,add(pivot,mul(pole,2.25))],accent,1.5,true,.8);
      label(add(pivot,mul(pole,2.3)),"Celestial pole",8,-2);
      if(step===0||step===1){label(transform(front),"Telescope tube",-108,14);label(transform([.52,2.62,-.73]),"Side focuser",12,14);}
      const arrow=(points:V[])=>{line(points,accent,3);const a=project(points[points.length-2]),b=project(points[points.length-1]);const angle=Math.atan2(b.y-a.y,b.x-a.x);ctx.beginPath();ctx.moveTo(b.x,b.y);ctx.lineTo(b.x-11*Math.cos(angle-.45),b.y-11*Math.sin(angle-.45));ctx.moveTo(b.x,b.y);ctx.lineTo(b.x-11*Math.cos(angle+.45),b.y-11*Math.sin(angle+.45));ctx.strokeStyle=accent;ctx.lineWidth=3;ctx.stroke();};
      if(step===2){arrow(Array.from({length:24},(_,i)=>{const a=.25+i*.055;return [Math.cos(a)*1.45,.035,Math.sin(a)*1.45] as V;}));label([-.9,.02,1.15],"Move the whole tripod",-18,24);}
      if(step===4){arrow(Array.from({length:20},(_,i)=>rigTransform([Math.cos(i*.075)*.52,1.44,Math.sin(i*.075)*.52])));label(rigTransform([.4,1.43,.13]),"Azimuth bolts",12,18);}
      if(step===5){arrow(Array.from({length:20},(_,i)=>upper([.32,1.65+Math.sin(i*.08)*.42,.05+Math.cos(i*.08)*.42])));label(upper([.22,1.55,.1]),"Altitude bolt",12,20);}
      if(playing&&!reduced?.matches)frame=requestAnimationFrame(render);
    };
    const redraw=()=>{cancelAnimationFrame(frame);render(performance.now());};
    const resize=typeof ResizeObserver!=="undefined"?new ResizeObserver(redraw):null;
    resize?.observe(el);reduced?.addEventListener("change",redraw);render(start);
    return()=>{cancelAnimationFrame(frame);resize?.disconnect();reduced?.removeEventListener("change",redraw);};
  },[step,playing,south,side,compact,loop]);
  return <div className={`guided-alignment-scene ${compact?"guided-alignment-inset":""}`}>
    <canvas ref={canvas} role="img" aria-label={step===2?"3D example: the telescope, mount and all three tripod legs rotate together against a fixed ground grid.":step===4||step===5?"3D example: adjustment bolts turn the mount head while the tripod feet stay fixed.":"3D equatorial telescope: a broad optical tube with a small side focuser, on a mount and three-legged tripod."}/>
    {!compact && <div className="guided-scene-caption"><span>3D example · not live telescope position</span><button className="guided-text-button" onClick={()=>setSide(v=>!v)}>{side?"Three-quarter view":"Side view"}</button></div>}
  </div>;
}
