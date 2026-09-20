import { strict as assert } from "node:assert";
import { alignmentSky } from "../focusSky";
import { altAzOf, lstHours } from "../../lib/altaz";
import { projectAltAz } from "../../lib/domeProjection";

let passed=0,failed=0;
function test(name:string,fn:()=>void){try{fn();passed++;}catch(error){failed++;console.error(name,error);}}
const time=1758000000,lat=35,lon=-115;
const field={ra_hours:lstHours(lon,time)-2,dec_deg:30};
const sky=alignmentSky(field,24,lat,lon,time)!;
const close=(a:number,b:number)=>assert.ok(Math.abs(a-b)<1e-8,`${a} != ${b}`);
test("field is at the current site and time, not stored planning alt/az",()=>{
 const expected=altAzOf(field.ra_hours,field.dec_deg,lat,lon,time);close(sky.field.alt,expected.altDeg);close(sky.field.az,expected.azDeg);
 const later=alignmentSky(field,24,lat,lon,time+3600)!;assert.ok(Math.abs(later.field.az-sky.field.az)>1);
});
test("both native alignment directions start at the same field",()=>{
 assert.equal(sky.arcs.length,2);for(const arc of sky.arcs)assert.deepEqual(arc[0],sky.field);
});
test("measurement dots sit at half and full RA span, not altitude increments",()=>{
 for(const [i,direction] of [-1,1].entries())for(const sample of [12,24]){
  const expected=altAzOf(field.ra_hours+direction*24/15*sample/24,field.dec_deg,lat,lon,time);
  close(sky.arcs[i][sample].alt,expected.altDeg);close(sky.arcs[i][sample].az,expected.azDeg);
 }
});
test("RA wrap through zero gives continuous finite arcs",()=>{
 const wrapped=alignmentSky({ra_hours:.05,dec_deg:20},24,lat,lon,time)!;
 for(const arc of wrapped.arcs)for(const p of arc){assert.ok(Number.isFinite(p.alt));assert.ok(p.az>=0&&p.az<360);}
});
test("missing span does not invent a measurement path for restored field",()=>assert.equal(alignmentSky(field,0,lat,lon,time)!.arcs.length,0));
test("invalid coordinates never produce an apparent target",()=>assert.equal(alignmentSky({...field,ra_hours:NaN},24,lat,lon,time),null));
test("field rotates in the same projection as horizon and cloud layer",()=>{
 const a=projectAltAz(sky.field.alt,sky.field.az,200,180,140,32,60);
 const b=projectAltAz(sky.field.alt,sky.field.az-60,200,180,140,32,0);
 close(a.x,b.x);close(a.y,b.y);
});
console.log(`guided focus sky: ${passed}/${passed+failed} passed`);
export const result={passed,failed,total:passed+failed};if(failed)process.exitCode=1;
