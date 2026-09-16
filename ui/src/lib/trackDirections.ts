/** Screen-space chevrons following the ordered, future-directed path. */
export function trackDirections(points: readonly {x:number;y:number}[], spacing=64) {
  const out:{x:number;y:number;angle:number}[]=[];
  let remaining=spacing/2;
  for(let i=1;i<points.length;i++) {
    const a=points[i-1], b=points[i];
    const dx=b.x-a.x,dy=b.y-a.y,len=Math.hypot(dx,dy);
    if(!Number.isFinite(len)||len>400) {remaining=spacing/2;continue;}
    if(len<.01)continue;
    let along=remaining;
    for(;along<len;along+=spacing) out.push({x:a.x+dx*along/len,y:a.y+dy*along/len,angle:Math.atan2(dy,dx)*180/Math.PI});
    remaining=along-len;
  }
  return out;
}
