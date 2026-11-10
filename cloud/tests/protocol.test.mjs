import {test} from 'node:test';
import assert from 'node:assert/strict';
import {validFile,partSize,PART_BYTES,hash} from '../protocol.mjs';
test('large video sizes are allowed while paths and oversized files are rejected',()=>{
 assert.equal(validFile('새 영상.mov',5e9,5e9),true);
 assert.equal(validFile('../video.mov',100,5e9),false);
 assert.equal(validFile('video.mov',5e9+1,5e9),false);
 assert.equal(validFile('video.mov',0,5e9),false);
});
test('multipart plan preserves every byte across the 5GB boundary',()=>{
 for(const n of [1,PART_BYTES,PART_BYTES+1,5e9]){
  const total=Array.from({length:Math.ceil(n/PART_BYTES)},(_,i)=>partSize(n,i+1)).reduce((a,b)=>a+b,0);
  assert.equal(total,n);assert.equal(partSize(n,0),0);assert.equal(partSize(n,Math.ceil(n/PART_BYTES)+1),0);
 }
});
test('only hashed invitation secrets are stored',async()=>{
 const a=await hash('local-test-only');assert.equal(a.length,64);assert.notEqual(a,await hash('another-invite'));
});
