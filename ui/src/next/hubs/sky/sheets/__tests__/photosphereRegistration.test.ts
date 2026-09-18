import assert from 'node:assert/strict';
import {registerFrame} from '../photosphereRegistration';
import {ScanPoseSource,poseSeparation} from '../photospherePose';
import {SkyPanorama,cameraLens,lookBasis,rotateBasis,skyAngles,unit,type CameraBasis,type V3} from '../photosphereGeometry';
let passed=0;
function test(name:string,fn:()=>void){fn();passed++;console.log(`PASS ${name}`);}
const width=180,height=240,lens=cameraLens(width,height,41.1);
function scene(basis:CameraBasis,exposure=1):Uint8ClampedArray {
  const data=new Uint8ClampedArray(width*height*4);
  for(let y=0;y<height;y++)for(let x=0;x<width;x++){
    const xx=((x+.5)/width*2-1)*lens.tanX,yy=(1-(y+.5)/height*2)*lens.tanY;
    const ray=unit(basis.forward.map((v,i)=>v+basis.right[i]*xx+basis.up[i]*yy) as V3),p=skyAngles(ray);
    const a=Math.floor(p.az/3),b=Math.floor((p.alt+90)/3),v=30+((a*73856093^b*19349663)>>>0)%190;
    data.set([v*exposure,v*.8*exposure,v*.6*exposure,255],(y*width+x)*4);
  }
  return data;
}
test('Image matching recovers small yaw, pitch and roll errors without stretching the lens',()=>{
  const first=lookBasis(20,30),second=lookBasis(35,38),image=scene(second,.8);
  const panorama=new SkyPanorama();panorama.add(scene(first),width,height,first,lens);
  for(const offsets of [[3,1,0],[-3,-1,1]]){
    const wrong=rotateBasis(second,...offsets as [number,number,number]);
    assert.equal(panorama.checkOverlap(image,width,height,wrong,lens).result,'conflict');
    const before=panorama.pixels.slice(),result=registerFrame(panorama,image,width,height,wrong,lens);
    assert.equal(result.adjusted,true);assert.equal(result.overlap.result,'agree');
    assert.ok(poseSeparation(second,result.basis)<.8,`Correction missed by ${poseSeparation(second,result.basis)} degrees`);
    assert.ok(result.evaluations<=72);assert.deepEqual(panorama.pixels,before,'Registration changed the existing image');
  }
});
test('Blank sky does not fabricate an image-derived orientation',()=>{
  const panorama=new SkyPanorama(),basis=lookBasis(20,30),blank=new Uint8ClampedArray(width*height*4).fill(120);
  panorama.add(blank,width,height,basis,lens);
  const result=registerFrame(panorama,blank,width,height,basis,lens);
  assert.equal(result.adjusted,false);assert.equal(result.overlap.result,'unknown');assert.equal(result.basis,basis);
});
test('An unrelated image remains rejected after the bounded search',()=>{
  const panorama=new SkyPanorama(),basis=lookBasis(20,30);panorama.add(scene(basis),width,height,basis,lens);
  const unrelated=scene(lookBasis(160,20));
  const result=registerFrame(panorama,unrelated,width,height,basis,lens);
  assert.equal(result.adjusted,false);assert.equal(result.overlap.result,'conflict');
});
test('Relative motion keeps the initial north anchor despite later compass jumps',()=>{
  const source=new ScanPoseSource();
  assert.equal(source.accept(lookBasis(10,25),false,50),null);
  source.accept(lookBasis(120,25),true,100);
  const anchored=source.accept(lookBasis(10,25),false,120)!;
  assert.equal(anchored.changedSource,true);assert.ok(poseSeparation(anchored.basis,lookBasis(120,25))<.001);
  assert.equal(source.accept(lookBasis(150,25),true,150),null);
  const moved=source.accept(lookBasis(15,30),false,160)!;
  assert.ok(poseSeparation(moved.basis,lookBasis(125,30))<.001);
  assert.equal(moved.changedSource,false);
  source.clear();assert.equal(source.usesRelative,false);
});
test('Relative motion cannot anchor itself to a stale north reading',()=>{
  const source=new ScanPoseSource();source.accept(lookBasis(120,25),true,100);
  assert.equal(source.accept(lookBasis(10,25),false,1000),null);
  assert.equal(source.usesRelative,false);
});
console.log(`photosphereRegistration.test: ${passed}/${passed} passed`);
export const result={passed,failed:0,total:passed};
