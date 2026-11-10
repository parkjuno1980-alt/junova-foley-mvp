import {test} from 'node:test';
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {mkdtemp,rm} from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {PART_BYTES} from '../protocol.mjs';
const base=process.env.TEST_REMOTE_URL;
test('invitation → resumable video → outbound engine → private ranged AAF', {skip:!base,timeout:90000}, async()=>{
 const admin=process.env.TEST_ADMIN_KEY||'local-test-admin-only';
 async function req(route,key,method='GET',value,headers={}){
  const h={Authorization:'Bearer '+key,...headers};let body=value;
  if(value&&!(value instanceof Uint8Array)){h['Content-Type']='application/json';body=JSON.stringify(value);}
  return fetch(base+route,{method,headers:h,body});
 }
 const invitation=await (await req('/v1/admin/invites',admin,'POST',{label:'합성 연결 시험'})).json();
 const token=new URL(invitation.invite_url).hash.slice(8);
 assert.equal((await req('/v1/session',token)).status,200);
 assert.equal((await req('/v1/session','invalid')).status,401);
 const other=await (await req('/v1/admin/invites',admin,'POST',{label:'다른 고객 시험'})).json();
 const otherToken=new URL(other.invite_url).hash.slice(8);
 const create=await req('/v1/jobs',token,'POST',{name:'test.mp4',size:PART_BYTES+123,title:'합성 연결 시험',fps:24,start_tc:'01:00:00:00'});
 assert.equal(create.status,200,await create.clone().text());const j=await create.json();
 assert.equal((await req('/v1/jobs/'+j.job_id,otherToken)).status,404);
 assert.equal((await req('/v1/jobs/'+j.job_id+'/result',token)).status,409);
 assert.equal((await req('/v1/jobs',token,'POST',{name:'large.mp4',size:5e9,fps:24,start_tc:'01:00:00:00'})).status,409);
 const prefix='/v1/jobs/'+j.job_id+'/upload/'+j.file.id;
 assert.equal((await req(prefix+'/parts/1',token,'PUT',new Uint8Array(12))).status,400);
 assert.equal((await req(prefix+'/complete',token,'POST',{})).status,409);
 for(const [number,size] of [[1,PART_BYTES],[2,123]]){
  assert.equal((await req(prefix+'/parts/'+number,token,'PUT',new Uint8Array(size))).status,200);
 }
 const parts=await (await req(prefix+'/parts',token)).json();assert.equal(parts.length,2);
 // Retry an uploaded part without storing a second copy.
 assert.equal((await req(prefix+'/parts/2',token,'PUT',new Uint8Array(123))).status,200);
 assert.equal((await req(prefix+'/complete',token,'POST',{})).status,200);
 const temp=await mkdtemp(path.join(os.tmpdir(),'autof-remote-e2e-'));
 try{
  const python=process.env.TEST_PYTHON;assert.ok(python,'TEST_PYTHON is required');
  const child=spawn(python,['-m','server.remote_worker','--once'],{cwd:path.resolve(import.meta.dirname,'../..'),env:{...process.env,JUNOVA_REMOTE_API:base,JUNOVA_WORKER_KEY:'local-test-worker-only',JUNOVA_ENGINE_CMD:python+' -m server.mock_engine',JUNOVA_REMOTE_DATA_DIR:temp,MOCK_STAGE_SECONDS:'.05'},stdio:['ignore','pipe','pipe']});
  let stderr='',stdout='';child.stderr.on('data',b=>stderr+=b);child.stdout.on('data',b=>stdout+=b);
  const code=await new Promise(resolve=>child.on('exit',resolve));assert.equal(code,0,stderr);
  const resultResponse=await req('/v1/jobs/'+j.job_id+'/result',token);
  assert.equal(resultResponse.status,200,(await resultResponse.clone().text())+' '+stdout+' '+stderr);const result=await resultResponse.json();
  assert.equal(result.summary.foot_steps,6);const aaf=result.files.find(f=>f.kind==='aaf');assert.ok(aaf);
  const downloaded=await fetch(base+aaf.url);assert.equal(downloaded.status,200);
  const bytes=new Uint8Array(await downloaded.arrayBuffer());assert.deepEqual([...bytes.slice(0,4)],[208,207,17,224]);
  const ranged=await fetch(base+aaf.url,{headers:{Range:'bytes=0-31'}});assert.equal(ranged.status,206);assert.equal((await ranged.arrayBuffer()).byteLength,32);
  assert.equal((await fetch(base+aaf.url.replace(/ticket=.*/,'ticket=bad'))).status,401);
  assert.equal((await req('/v1/jobs/'+j.job_id+'/result',otherToken)).status,404);
  const next=await (await req('/v1/jobs',token,'POST',{name:'next.mp4',size:20,fps:24,start_tc:'01:00:00:00'})).json();
  const nextPrefix='/v1/jobs/'+next.job_id+'/upload/'+next.file.id;
  assert.equal((await req(nextPrefix+'/parts/1',token,'PUT',new Uint8Array(20))).status,200);
  assert.equal((await req(nextPrefix+'/complete',token,'POST',{})).status,200);
  const claims=await Promise.all([req('/v1/worker/claim','local-test-worker-only','POST',{}),req('/v1/worker/claim','local-test-worker-only','POST',{})]);
  assert.deepEqual(claims.map(r=>r.status).sort(),[200,204]);
  const claimed=await claims.find(r=>r.status===200).json();
  assert.equal((await req('/v1/jobs/'+next.job_id+'/cancel',token,'POST',{})).status,200);
  assert.equal((await req('/v1/worker/jobs/'+next.job_id+'/progress','local-test-worker-only','POST',{}, {'X-Lease':claimed.lease})).status,409);
 }finally{await rm(temp,{recursive:true,force:true});}
});
