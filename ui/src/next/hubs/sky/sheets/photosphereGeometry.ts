// One projection for the live dome and the saved panorama. Earth axes are
// east, north, up; camera axes are right, up, and rear-camera forward.
export type V3 = [number, number, number];
export type CameraBasis = { right: V3; up: V3; forward: V3 };
export const DEG = Math.PI / 180;
export const dot = (a: V3, b: V3) => a[0]*b[0] + a[1]*b[1] + a[2]*b[2];
export const unit = (v: V3): V3 => { const n = Math.hypot(...v); return v.map(x => x/n) as V3; };
const cross = (a: V3, b: V3): V3 => [a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]];
export function skyVector(az: number, alt: number): V3 {
  return [Math.sin(az*DEG)*Math.cos(alt*DEG), Math.cos(az*DEG)*Math.cos(alt*DEG), Math.sin(alt*DEG)];
}
export function skyAngles(v: V3): { az: number; alt: number } {
  return { az: (Math.atan2(v[0],v[1])/DEG+360)%360, alt: Math.asin(Math.max(-1,Math.min(1,v[2])))/DEG };
}
export function orientationBasis(alpha: number, beta: number, gamma: number, screenAngle = 0, compassCorrection = 0): CameraBasis {
  const a=alpha*DEG,b=beta*DEG,g=gamma*DEG,s=screenAngle*DEG;
  const ca=Math.cos(a),sa=Math.sin(a),cb=Math.cos(b),sb=Math.sin(b),cg=Math.cos(g),sg=Math.sin(g);
  const x: V3=[ca*cg-sa*sb*sg,sa*cg+ca*sb*sg,-cb*sg];
  const y: V3=[-sa*cb,ca*cb,sb];
  const f: V3=[-ca*sg-sa*sb*cg,-sa*sg+ca*sb*cg,-cb*cg];
  const correct=(v: V3): V3 => {
    const c=Math.cos(compassCorrection*DEG),t=Math.sin(compassCorrection*DEG);
    return [c*v[0]+t*v[1],-t*v[0]+c*v[1],v[2]];
  };
  return {
    right: correct(x.map((v,i)=>v*Math.cos(s)-y[i]*Math.sin(s)) as V3),
    up: correct(x.map((v,i)=>v*Math.sin(s)+y[i]*Math.cos(s)) as V3), forward:correct(f),
  };
}
export function lookBasis(az: number, alt: number): CameraBasis {
  return { forward:skyVector(az,alt), right:skyVector(az+90,0), up:skyVector(az,alt+90) };
}
/** Small rigid rotations preserve the spherical camera geometry. */
export function rotateBasis(basis:CameraBasis,yaw=0,pitch=0,roll=0):CameraBasis {
  const turn=(b:CameraBasis,axis:V3,angle:number):CameraBasis=>{
    const c=Math.cos(angle*DEG),s=Math.sin(angle*DEG);
    const rotate=(v:V3):V3=>{const k=cross(axis,v),d=dot(axis,v);return v.map((n,i)=>n*c+k[i]*s+axis[i]*d*(1-c)) as V3;};
    return {right:rotate(b.right),up:rotate(b.up),forward:rotate(b.forward)};
  };
  let b=turn(basis,[0,0,1],-yaw);b=turn(b,b.right,pitch);return turn(b,b.forward,roll);
}
export function transferBasis(basis:CameraBasis,from:CameraBasis,to:CameraBasis):CameraBasis {
  const map=(v:V3)=>v.map((_,i)=>to.right[i]*dot(v,from.right)+to.up[i]*dot(v,from.up)+to.forward[i]*dot(v,from.forward)) as V3;
  return {right:map(basis.right),up:map(basis.up),forward:map(basis.forward)};
}
// Use a consistent 60° short-axis estimate. Browsers do not expose calibrated
// camera intrinsics. The same estimate must be used for capture AND overlay.
export function cameraLens(width: number, height: number, shortAxisFov=60) {
  const short=Math.tan(shortAxisFov/2*DEG), ratio=width/height;
  return { tanX: ratio>=1 ? short*ratio : short, tanY:ratio>=1 ? short : short/ratio };
}
export function projectRay(ray: V3, basis: CameraBasis, width: number, height: number, lens=cameraLens(width,height)) {
  const z=dot(ray,basis.forward); if(z<=.05) return null;
  const {tanX,tanY}=lens;
  return { x:(.5+dot(ray,basis.right)/(2*z*tanX))*width, y:(.5-dot(ray,basis.up)/(2*z*tanY))*height };
}

/** Rec. 601 luma from an 8-bit RGB triple. */
export const pixelLuminance=(r:number,g:number,b:number)=>.299*r+.587*g+.114*b;
/** How far the blue channel sits from that same pixel's luma: exactly 0 for
 *  any grey, positive for a blue sky, negative for the warm surfaces most
 *  buildings, soil and bark are. It is what separates a wall from a sky of the
 *  SAME brightness (issue #58), which no luma can.
 *
 *  It is not exposure invariant - the same colour photographed darker reads
 *  smaller - so it is never read as an absolute colour. The tracer reads it
 *  against the sky's OWN blueness, with a tolerance, the same way it reads
 *  luminance against the sky's own level. */
export const pixelBlueness=(r:number,g:number,b:number)=>b-pixelLuminance(r,g,b);

export interface DomeCell { id: number; center: V3; vertices: V3[]; az: number; alt: number }
/** Dual of a subdivided icosahedron: hexagons with the necessary pentagons.
 * Their positions never depend on the phone pose. */
export function makeDome(): DomeCell[] {
  const t=(1+Math.sqrt(5))/2;
  let vertices: V3[] = [[-1,t,0],[1,t,0],[-1,-t,0],[1,-t,0],[0,-1,t],[0,1,t],[0,-1,-t],[0,1,-t],[t,0,-1],[t,0,1],[-t,0,-1],[-t,0,1]].map(v=>unit(v as V3));
  let faces = [[0,11,5],[0,5,1],[0,1,7],[0,7,10],[0,10,11],[1,5,9],[5,11,4],[11,10,2],[10,7,6],[7,1,8],[3,9,4],[3,4,2],[3,2,6],[3,6,8],[3,8,9],[4,9,5],[2,4,11],[6,2,10],[8,6,7],[9,8,1]];
  for(let step=0;step<2;step++) {
    const mids=new Map<string,number>();
    const mid=(a:number,b:number)=>{const key=[a,b].sort((x,y)=>x-y).join(':'); let index=mids.get(key);
      if(index===undefined){index=vertices.length;vertices.push(unit(vertices[a].map((v,i)=>v+vertices[b][i]) as V3));mids.set(key,index);}return index;};
    faces=faces.flatMap(([a,b,c])=>{const ab=mid(a,b),bc=mid(b,c),ca=mid(c,a);return [[a,ab,ca],[b,bc,ab],[c,ca,bc],[ab,bc,ca]];});
  }
  // Place a cell exactly at the zenith for the overhead capture.
  const pole=vertices[5], east=unit(cross([0,0,1],pole)), north=cross(pole,east);
  vertices=vertices.map(v=>[dot(v,east),dot(v,north),dot(v,pole)]);
  const centres=faces.map(f=>unit([0,1,2].map(i=>f.reduce((n,j)=>n+vertices[j][i],0)) as V3));
  return vertices.flatMap((center,id)=>{
    const angles=skyAngles(center); if(angles.alt < -1e-6) return [];
    const right=unit(cross(Math.abs(center[2])>.99 ? [0,1,0] : [0,0,1],center));
    const up=cross(center,right);
    const corners=faces.flatMap((f,i)=>f.includes(id)?[centres[i]]:[]);
    corners.sort((a,b)=>Math.atan2(dot(a,up),dot(a,right))-Math.atan2(dot(b,up),dot(b,right)));
    return [{id,center,vertices:corners,...angles}];
  });
}
export const DOME_CELLS=makeDome();

/** How far a cell's centre may sit from the camera's forward ray and still be
 *  the patch this aim is capturing. ONE cone for every cell, the zenith cap
 *  included, and one number for the aim dot and for the capture.
 *
 *  Measured on the dome above (2026-09-18, and re-derived against this file):
 *  91 cells, on rings at altitude 90, 74.1, 63.4, 58.3, 46.4, 42.4 and lower;
 *  the first ring below the zenith cap is five cells at azimuth 0, 72, 144,
 *  216 and 288 and altitude 74.1. That leaves a gap the old cones could not
 *  reach across: a hold at azimuth 180, altitude 85 is 5.00 degrees from the
 *  pole - equality against the pole's old 5 degree cone, so refused - and
 *  12.17 degrees from the nearest ring-1 cell, which is why such a hold could
 *  never capture (issue #57). It was not one unlucky direction. On a 0.5
 *  degree azimuth-altitude grid over the visible hemisphere (not area
 *  weighted, so high altitudes count for more than their share of sky), the
 *  fraction of directions with no cell in reach was 23.5 percent at the old 8
 *  degree cone with the pole at 5, 20.1 percent at 8 everywhere, 5.8 percent
 *  at 9, 0.4 percent at 10, and 0 at 11.
 *
 *  Eleven is not a round number chosen for comfort: the largest distance from
 *  any direction above the horizon to its NEAREST cell centre is 10.8123
 *  degrees, at the corners where three cells meet. It is attained at ten
 *  points, at azimuth 36, 108, 180, 252 and 324 and at BOTH altitude 52.62 and
 *  altitude 10.81, so the low rings are as exposed as the mid-altitude
 *  hexagons; a 0.5 degree grid samples that peak as 10.75 and a 0.1 degree
 *  grid as 10.80, which is why it is refined rather than sampled. The zenith
 *  cap is not the worst case at all - its pentagon's corners are only 8.60
 *  degrees from its centre - it was only the worst case for the OLD cones,
 *  which gave the smallest cell the smallest reach. So 11 degrees leaves no
 *  direction above the horizon without a target, with 0.19 degrees to spare,
 *  and a smaller cone does not.
 *
 *  It still fits the picture. A cell's far corner is at most 10.81 degrees
 *  from its centre, so a cell accepted at the edge of this cone lies within
 *  21.8 degrees of the image centre, against the 30 degrees of half-width the
 *  60 degree short-axis estimate gives. It is not a guarantee for every lens,
 *  and it never was: the 35 degree minimum `setCameraViewAngle` accepts has
 *  17.5 degrees of half-width, which the old 8 degree cone already spilled
 *  cells past (8 + 10.81 = 18.8). */
export const AIM_CONE_DEG=11;
/** The cell this direction is aiming at: the NEAREST cell centre within the
 *  aim cone, or null when the direction has no target (below the horizon, or
 *  pointing away from the dome). Nearest and not first-in-list, because
 *  `DOME_CELLS` is in subdivision order and the first cell whose cone contains
 *  a ray is frequently not the one the aim dot draws - the dot and the capture
 *  used to be able to disagree about which patch was being captured. */
export function targetCell(forward: V3): DomeCell | null {
  let best: DomeCell|null=null, similarity=Math.cos(AIM_CONE_DEG*DEG);
  for(const cell of DOME_CELLS){
    const alignment=dot(cell.center,forward);
    if(alignment>similarity){similarity=alignment;best=cell;}
  }
  return best;
}

export interface OverlapCheck { result:'agree'|'conflict'|'unknown'; samples:number; correlation:number|null; featureCorrelation?:number|null }

/** A bounded colour mosaic; frames are projected then discarded. No growing
 * collection of full-resolution phone photographs is retained. */
export class SkyPanorama {
  readonly pixels: Uint8ClampedArray;
  private weights: Float32Array;
  private rays: Float32Array;
  constructor(readonly width=1080, readonly height=300) {
    this.pixels=new Uint8ClampedArray(width*height*4);
    this.weights=new Float32Array(width*height);
    this.rays=new Float32Array(width*height*3);
    for(let y=0;y<height;y++)for(let x=0;x<width;x++)this.rays.set(skyVector((x+.5)/width*360,90-y/(height-1)*100),(y*width+x)*3);
  }
  /** A conservative rejection check, not image registration. Compare textured
   * overlap after projection, allowing a global exposure/brightness change.
   * Blank sky or too little overlap cannot certify (or reject) alignment. */
  checkOverlap(data:Uint8ClampedArray,width:number,height:number,basis:CameraBasis,lens=cameraLens(width,height)):OverlapCheck {
    if(data.length!==width*height*4)throw new Error('Camera image is incomplete');
    const pairs:number[][]=[],gradients:number[][]=[];let visible=0;
    const lum=(p:Uint8ClampedArray,i:number)=>.299*p[i]+.587*p[i+1]+.114*p[i+2];
    const rayAt=(sx:number,sy:number)=>unit(basis.forward.map((v,i)=>v+basis.right[i]*(sx*2-1)*lens.tanX+basis.up[i]*(1-sy*2)*lens.tanY) as V3);
    const oldAt=(sx:number,sy:number):number|null=>{
      const {az,alt}=skyAngles(rayAt(sx,sy));if(alt < -10 || alt > 85)return null;
      const px=Math.min(this.width-1,Math.floor(az/360*this.width)),py=Math.round((90-alt)/100*(this.height-1)),i=(py*this.width+px)*4;
      return this.pixels[i+3]===255?lum(this.pixels,i):null;
    };
    const newAt=(sx:number,sy:number)=>lum(data,(Math.floor(sy*height)*width+Math.floor(sx*width))*4);
    for(let row=0;row<24;row++)for(let col=0;col<32;col++){
      const sx=(col+.5)/32,sy=(row+.5)/24;
      const ray=unit(basis.forward.map((v,i)=>v+basis.right[i]*(sx*2-1)*lens.tanX+basis.up[i]*(1-sy*2)*lens.tanY) as V3);
      const {az,alt}=skyAngles(ray);if(alt < -10 || alt > 85)continue;
      visible++;
      const px=Math.min(this.width-1,Math.floor(az/360*this.width));
      const py=Math.round((90-alt)/100*(this.height-1)),dest=(py*this.width+px)*4;
      if(this.pixels[dest+3]!==255)continue;
      const source=(Math.floor(sy*height)*width+Math.floor(sx*width))*4;
      pairs.push([lum(data,source),lum(this.pixels,dest)]);
      // Shared brown walls can correlate despite displaced doorframes. Compare
      // local edges too, in the same projected coordinates, before accepting.
      const step=3;
      for(const [dx,dy] of [[step/width,0],[0,step/height]]){
        if(sx-dx<0 || sx+dx>=1 || sy-dy<0 || sy+dy>=1)continue;
        const left=oldAt(sx-dx,sy-dy),right=oldAt(sx+dx,sy+dy);
        if(left!==null&&right!==null)gradients.push([newAt(sx+dx,sy+dy)-newAt(sx-dx,sy-dy),right-left]);
      }
    }
    const samples=pairs.length;
    const unknown:OverlapCheck={result:'unknown',samples,correlation:null};
    if(samples<100 || samples<visible*.25)return unknown;
    const mean=pairs.reduce((a,p)=>[a[0]+p[0]/samples,a[1]+p[1]/samples],[0,0]);
    let aa=0,bb=0,ab=0;
    for(const p of pairs){const a=p[0]-mean[0],b=p[1]-mean[1];aa+=a*a;bb+=b*b;ab+=a*b;}
    if(aa/samples<100 || bb/samples<100)return unknown;
    const correlation=ab/Math.sqrt(aa*bb);
    let ga=0,gb=0,gab=0;
    for(const [a,b] of gradients){ga+=a*a;gb+=b*b;gab+=a*b;}
    const featureCorrelation=gradients.length>=100&&ga/gradients.length>25&&gb/gradients.length>25?gab/Math.sqrt(ga*gb):null;
    return {result:correlation<.35 || (featureCorrelation!==null && featureCorrelation<.4)?'conflict':'agree',samples,correlation,featureCorrelation};
  }
  add(data: Uint8ClampedArray, width: number, height: number, basis: CameraBasis, lens=cameraLens(width,height)): void {
    if(data.length!==width*height*4) throw new Error('Camera image is incomplete');
    const {tanX,tanY}=lens,f=basis.forward,r=basis.right,u=basis.up;
    for(let i=0;i<this.weights.length;i++) {
      const x=this.rays[i*3],y=this.rays[i*3+1],z=this.rays[i*3+2];
      const depth=x*f[0]+y*f[1]+z*f[2];
      if(depth<.35 || depth<this.weights[i]+.015)continue;
      const px=(x*r[0]+y*r[1]+z*r[2])/(depth*tanX),py=(x*u[0]+y*u[1]+z*u[2])/(depth*tanY);
      if(Math.abs(px)>.94 || Math.abs(py)>.94)continue;
      const sx=Math.min(width-1,Math.max(0,Math.floor((px+1)*.5*width)));
      const sy=Math.min(height-1,Math.max(0,Math.floor((1-py)*.5*height)));
      const source=(sy*width+sx)*4, dest=i*4;
      this.pixels[dest]=data[source];this.pixels[dest+1]=data[source+1];this.pixels[dest+2]=data[source+2];this.pixels[dest+3]=255;
      this.weights[i]=depth;
    }
  }
  /** With tilt but no compass, zenith is the only shared identifiable point.
   * Project it to its actual image position instead of assuming centre=zenith. */
  addZenith(data:Uint8ClampedArray,width:number,height:number,basis:CameraBasis|null,lens=cameraLens(width,height)):void {
    if(data.length!==width*height*4)throw new Error('Camera image is incomplete');
    let x=width/2,y=height/2;
    if(basis){
      const depth=basis.forward[2];if(depth<=0)throw new Error('Point the camera overhead.');
      x=(.5+basis.right[2]/(2*depth*lens.tanX))*width;
      y=(.5-basis.up[2]/(2*depth*lens.tanY))*height;
      if(x<0||x>=width||y<0||y>=height)throw new Error('The overhead point is outside the camera image.');
    }
    const source=(Math.min(height-1,Math.floor(y))*width+Math.min(width-1,Math.floor(x)))*4;
    for(let col=0;col<this.width;col++){
      this.pixels.set([data[source],data[source+1],data[source+2],255],col*4);
      this.weights[col]=2; // An identified zenith must not be overwritten by a side view.
    }
  }
  has(ray: V3): boolean {
    const {az,alt}=skyAngles(ray);
    const x=Math.min(this.width-1,Math.floor(az/360*this.width)),y=Math.max(0,Math.min(this.height-1,Math.round((90-alt)/100*(this.height-1))));
    return this.pixels[(y*this.width+x)*4+3]===255;
  }
  covered(cell: DomeCell): boolean {
    return this.has(cell.center) && cell.vertices.every(v=>this.has(v));
  }
  /** One pixel column per bin, read at the bin's CENTRE azimuth, sampled at
   *  101 rows from the zenith down: row 0 is altitude 90, row 90 the horizon
   *  and row 100 the raster's floor at -10, so one row is one degree of
   *  altitude whatever the raster's height. An unpainted pixel is NaN, which
   *  is unknown and never open sky.
   *
   *  `channel` is the only difference between `columns` and `blueColumns`, so
   *  there is ONE definition of where a row is read from: a second copy of
   *  this mapping is a second chance for the two channels of the same row to
   *  come from different pixels. */
  private sampled(bins: number, channel:(r:number,g:number,b:number)=>number): number[][] {
    return Array.from({length:bins},(_,bin)=>Array.from({length:101},(_,row)=>{
      const x=Math.min(this.width-1,Math.floor((bin+.5)/bins*this.width));
      const y=Math.round(row/100*(this.height-1)),i=(y*this.width+x)*4;
      return this.pixels[i+3] ? channel(this.pixels[i],this.pixels[i+1],this.pixels[i+2]) : NaN;
    }));
  }
  columns(bins: number): number[][] { return this.sampled(bins,pixelLuminance); }
  /** The same rows' blueness, for a boundary a luminance cannot see. */
  blueColumns(bins: number): number[][] { return this.sampled(bins,pixelBlueness); }
  toDataURL(): string {
    const canvas=document.createElement('canvas');canvas.width=this.width;canvas.height=this.height;
    const ctx=canvas.getContext('2d');if(!ctx)throw new Error('Could not prepare the panorama.');
    const image=ctx.createImageData(this.width,this.height); image.data.set(this.pixels);ctx.putImageData(image,0,0);
    // PNG preserves unscanned areas as transparency for the editor's checkerboard.
    return canvas.toDataURL('image/png');
  }
}
