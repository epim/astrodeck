import assert from 'node:assert/strict';
import { VisualStability, SETTLE_MS, STALE_FRAME_MS } from '../photosphereStability';

// The video is the only witness to a still phone: the orientation sensor goes
// silent when nothing moves, so silence proves nothing on its own. These cases
// pin what the witness may and may not say. Brightness drift is not motion; a
// one-pixel shift is; and a stopped stream is UNKNOWN, never still.
let passed=0,failed=0;
function test(name:string,fn:()=>void){
  try{fn();passed++;console.log(`PASS ${name}`);}
  catch(e){failed++;console.log(`FAIL ${name}\n     ${(e as Error).message.split('\n')[0]}`);}
}

const W=32,H=24;
/** A 32x24 luminance gradient with a bright block, so a one-pixel shift moves
 *  real edges. `shift` slides the scene right; `gain` scales the exposure. */
function scene(shift=0,gain=1):Uint8Array{
  const px=new Uint8Array(W*H);
  for(let y=0;y<H;y++)for(let x=0;x<W;x++){
    const sx=x-shift;
    const block=sx>=10&&sx<=17&&y>=8&&y<=15;
    const v=block?180:40+Math.round(Math.max(0,sx)*80/(W-1));
    px[y*W+x]=Math.min(255,Math.round(v*gain));
  }
  return px;
}
const feed=(s:VisualStability,from:number,to:number,frame=scene())=>{
  for(let at=from;at<=to;at+=100)s.observe(at,frame,W,H);
};

test('With no video frame observed at all, stability is unknown, not still',()=>{
  const s=new VisualStability();
  assert.equal(s.stableAt(0),null);
  assert.equal(s.stableAt(5000),null);
});

test('An unchanging view reads as still only once it has held for the settle time',()=>{
  const s=new VisualStability();
  feed(s,0,300);
  assert.equal(s.stableAt(300),false,`${SETTLE_MS} ms of stillness is not yet proven at 300 ms`);
  feed(s,400,600);
  assert.equal(s.stableAt(600),true);
});

test('A one-pixel shift of the scene reads as motion',()=>{
  const s=new VisualStability();
  feed(s,0,500);
  assert.equal(s.stableAt(500),true,'the setup frames should have settled');
  s.observe(600,scene(1),W,H);
  assert.equal(s.stableAt(600),false);
});

test('A 30 percent exposure change of the same scene is not motion',()=>{
  const s=new VisualStability();
  feed(s,0,500);
  s.observe(600,scene(0,1.3),W,H);
  assert.equal(s.stableAt(600),true);
});

test('A stopped video stream is unknown, not still',()=>{
  const s=new VisualStability();
  feed(s,0,600);
  assert.equal(s.stableAt(600),true);
  assert.equal(s.stableAt(600+STALE_FRAME_MS),true,'the last frame is still inside the freshness window');
  assert.equal(s.stableAt(1700),null);
});

console.log(`photosphereStability.test: ${passed}/${passed+failed} passed`);
export const result={passed,failed,total:passed+failed};
if(failed)process.exitCode=1;
