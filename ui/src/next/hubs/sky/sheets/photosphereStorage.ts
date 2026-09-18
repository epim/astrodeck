// Photographs stay in this browser. Only the separately saved horizon points
// are sent to the telescope. One image per site bounds repeated-scan storage.
async function database(): Promise<IDBDatabase> {
  return new Promise((resolve,reject)=>{
    if(typeof indexedDB==='undefined'){reject(new Error('Browser storage unavailable'));return;}
    const request=indexedDB.open('astrodeck-surroundings',1);
    request.onupgradeneeded=()=>request.result.createObjectStore('photos');
    request.onsuccess=()=>resolve(request.result);request.onerror=()=>reject(request.error);
  });
}
export async function readPanorama(key:string):Promise<string|null> {
  try {
    const db=await database();
    try { return await new Promise((resolve,reject)=>{
      const request=db.transaction('photos').objectStore('photos').get(key);
      request.onsuccess=()=>resolve(typeof request.result==='string'&&request.result.startsWith('data:image/png;base64,')?request.result:null);
      request.onerror=()=>reject(request.error);
    }); } finally {db.close();}
  } catch {return null;}
}
export async function writePanorama(key:string,image:string):Promise<boolean> {
  try {
    const db=await database();
    try { await new Promise<void>((resolve,reject)=>{
      const tx=db.transaction('photos','readwrite');tx.objectStore('photos').put(image,key);
      tx.oncomplete=()=>resolve();tx.onerror=()=>reject(tx.error);tx.onabort=()=>reject(tx.error);
    });return true;} finally {db.close();}
  } catch {return false;}
}
