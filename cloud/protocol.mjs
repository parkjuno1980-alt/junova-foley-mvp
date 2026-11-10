export const PART_BYTES=16*1024*1024;
export async function hash(value){return Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(value))),x=>x.toString(16).padStart(2,'0')).join('');}
export function validFile(name,size,max){return typeof name==='string'&&name.length>0&&name.length<=180&&!/[\\/\x00-\x1f]/.test(name)&&Number.isSafeInteger(size)&&size>0&&size<=max;}
export function partSize(size,number){if(!Number.isInteger(number)||number<1||number>Math.ceil(size/PART_BYTES))return 0;return Math.min(PART_BYTES,size-(number-1)*PART_BYTES);}
