import {rotateBasis,type CameraBasis,type SkyPanorama,type OverlapCheck,cameraLens} from './photosphereGeometry';

export interface Registration {basis:CameraBasis;overlap:OverlapCheck;adjusted:boolean;evaluations:number}
/** Direct visual registration against the fixed panorama. Only small rigid
 * rotations are fitted; the lens and existing image are never stretched to
 * manufacture a match. Featureless/insufficient overlap retains sensor pose.
 * Work is bounded, and runs only for a held camera, not at display frequency. */
export function registerFrame(panorama:SkyPanorama,data:Uint8ClampedArray,width:number,height:number,basis:CameraBasis,lens=cameraLens(width,height)):Registration {
  const initial=panorama.checkOverlap(data,width,height,basis,lens);
  if(initial.result==='unknown' || initial.featureCorrelation==null || initial.featureCorrelation>.85)
    return {basis,overlap:initial,adjusted:false,evaluations:1};
  let evaluations=1;
  const score=(check:OverlapCheck)=>check.result==='unknown'||check.featureCorrelation==null||check.samples<initial.samples*.8
    ? -Infinity : .8*check.featureCorrelation+.2*(check.correlation??0);
  let best={basis,overlap:initial,angles:[0,0,0],score:score(initial)};
  const visit=(angles:number[])=>{
    if(Math.abs(angles[0])>6||Math.abs(angles[1])>4||Math.abs(angles[2])>2)return;
    const candidate=rotateBasis(basis,...angles as [number,number,number]);
    const overlap=panorama.checkOverlap(data,width,height,candidate,lens);evaluations++;
    const value=score(overlap);
    if(value>best.score)best={basis:candidate,overlap,angles,score:value};
  };
  for(let yaw=-6;yaw<=6;yaw+=2)for(let pitch=-4;pitch<=4;pitch+=2)visit([yaw,pitch,0]);
  for(const step of [1,.5,.25])for(let pass=0;pass<2;pass++)for(let axis=0;axis<3;axis++){
    const centre=best.angles;
    for(const sign of [-1,1])visit(centre.map((n,i)=>n+(i===axis?sign*step:0)));
  }
  // Small accidental improvements in repeated or moving textures are not a
  // reason to move the map. Require agreement in both edges and brightness.
  if(best.overlap.result!=='agree'||(best.overlap.featureCorrelation??0)<.55||(best.overlap.correlation??0)<.65||best.score<score(initial)+.08)
    return {basis,overlap:initial,adjusted:false,evaluations};
  return {basis:best.basis,overlap:best.overlap,adjusted:true,evaluations};
}
