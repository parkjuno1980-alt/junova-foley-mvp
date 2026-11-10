import {PART_BYTES,hash,validFile,partSize} from './protocol.mjs';
// Invitation-scoped API. Files stream through R2; they never enter the DB or Vercel.
const time=()=>Math.floor(Date.now()/1000);
const json=(v,status=200)=>Response.json(v,{status,headers:{'Cache-Control':'no-store'}});
class Failure extends Error{constructor(status,message){super(message);this.status=status;}}
const fail=(n,s)=>{throw new Failure(n,s);};
const id=()=>crypto.randomUUID();
const random=()=>Array.from(crypto.getRandomValues(new Uint8Array(32)),x=>x.toString(16).padStart(2,'0')).join('');
async function body(request){if(Number(request.headers.get('Content-Length')||0)>16384)fail(413,'입력 내용이 너무 커요.');let raw='',size=0;const reader=request.body?.getReader(),decoder=new TextDecoder();if(reader)while(true){const {value,done}=await reader.read();if(done)break;size+=value.byteLength;if(size>16384){await reader.cancel();fail(413,'입력 내용이 너무 커요.');}raw+=decoder.decode(value,{stream:true});}try{return JSON.parse(raw);}catch{fail(400,'입력 내용을 확인해주세요.');}}
const bearer=r=>(r.headers.get('Authorization')||'').replace(/^Bearer /,'');
async function secret(request,expected){if(!expected||!bearer(request)||await hash(bearer(request))!==await hash(expected))fail(401,'접근 권한을 확인해주세요.');}
const stmt=(env,sql,...params)=>env.DB.prepare(sql).bind(...params);
async function first(env,sql,...p){return stmt(env,sql,...p).first();}
async function rows(env,sql,...p){return (await stmt(env,sql,...p).all()).results;}
async function invite(request,env){const token=bearer(request);if(!token)fail(401,'초대 링크로 접속해주세요.');const row=await first(env,'SELECT * FROM invites WHERE token_hash=? AND revoked=0 AND expires_at>?',await hash(token),time());if(!row)fail(401,'초대 링크가 만료됐거나 취소됐어요.');return row;}
async function job(env,jid,owner){const row=await first(env,'SELECT * FROM jobs WHERE id=?',jid);if(!row||owner&&row.invite_id!==owner.id)fail(404,'작업을 찾지 못했어요.');return row;}
function view(j){return {job_id:j.id,title:j.title,status:j.status,stage:j.stage,percent:j.percent,message:j.message,error:j.error,created_at:j.created_at,expires_at:j.expires_at,summary:JSON.parse(j.summary),engine_mode:j.engine_mode,config:JSON.parse(j.config)};}
async function touch(env){await stmt(env,'INSERT INTO worker_state(id,last_seen) VALUES(1,?) ON CONFLICT(id) DO UPDATE SET last_seen=excluded.last_seen',time()).run();}
async function lease(request,env,jid){await secret(request,env.WORKER_KEY);const j=await job(env,jid);if(j.lease!==request.headers.get('X-Lease')||j.lease_until<time()||!['running','needs_review','uploading_result'].includes(j.status))fail(409,'작업 배정이 만료됐거나 취소됐어요.');return j;}
async function newFile(env,j,kind,name,size){
 const fid=id(),key=j.id+'/'+kind+'/'+fid;
 const upload=await env.MEDIA.createMultipartUpload(key);
 try{await stmt(env,'INSERT INTO files(id,job_id,kind,name,object_key,upload_id,size) VALUES(?,?,?,?,?,?,?)',fid,j.id,kind,name,key,upload.uploadId,size).run();}
 catch(e){await upload.abort();throw e;}
 return {id:fid,name,size,kind};
}
async function file(env,jid,fid){const f=await first(env,'SELECT * FROM files WHERE id=? AND job_id=?',fid,jid);if(!f)fail(404,'파일을 찾지 못했어요.');return f;}
async function uploadPart(request,env,f,n){
 if(f.complete)fail(409,'이미 업로드를 마친 파일이에요.');
 const expected=partSize(f.size,n);if(!expected)fail(400,'파일 조각 번호를 확인해주세요.');
 // File/bytes requests have known Content-Length. Enforce before touching R2.
 if(Number(request.headers.get('Content-Length'))!==expected)fail(400,'파일 조각 크기가 맞지 않아요.');
 const saved=await first(env,'SELECT etag FROM parts WHERE file_id=? AND number=?',f.id,n);
 if(saved)return {partNumber:n,etag:saved.etag};
 const result=await env.MEDIA.resumeMultipartUpload(f.object_key,f.upload_id).uploadPart(n,request.body);
 await stmt(env,'INSERT INTO parts(file_id,number,etag,size) VALUES(?,?,?,?) ON CONFLICT(file_id,number) DO NOTHING',f.id,n,result.etag,expected).run();
 return result;
}
async function complete(env,f){
 if(f.complete)return;
 const parts=await rows(env,'SELECT number,etag,size FROM parts WHERE file_id=? ORDER BY number',f.id);
 if(parts.length!==Math.ceil(f.size/PART_BYTES)||parts.some((p,i)=>p.number!==i+1||p.size!==partSize(f.size,p.number)))fail(409,'파일 업로드가 아직 끝나지 않았어요.');
 try{await env.MEDIA.resumeMultipartUpload(f.object_key,f.upload_id).complete(parts.map(p=>({partNumber:p.number,etag:p.etag})));}
 catch(e){const obj=await env.MEDIA.head(f.object_key);if(!obj||obj.size!==f.size)throw e;}
 await stmt(env,'UPDATE files SET complete=1 WHERE id=?',f.id).run();
}
async function signed(env,payload){if(!env.DOWNLOAD_KEY)fail(503,'다운로드 연결을 준비 중이에요.');const data=JSON.stringify(payload);const key=await crypto.subtle.importKey('raw',new TextEncoder().encode(env.DOWNLOAD_KEY),{name:'HMAC',hash:'SHA-256'},false,['sign']);const sig=Array.from(new Uint8Array(await crypto.subtle.sign('HMAC',key,new TextEncoder().encode(data))),x=>x.toString(16).padStart(2,'0')).join('');return btoa(data).replaceAll('+','-').replaceAll('/','_').replaceAll('=','')+'.'+sig;}
async function readTicket(env,text){try{const [v]=text.split('.');const p=JSON.parse(atob(v.replaceAll('-','+').replaceAll('_','/')));if(!p||p.exp<time()||p.exp>time()+1000||await signed(env,p)!==text)fail(401,'다운로드 링크가 만료됐어요.');return p;}catch{fail(401,'다운로드 링크를 다시 받아주세요.');}}
async function download(request,env,f){
 const range=request.headers.get('Range');let options={};
 if(range){const m=/^bytes=(\d+)-(\d*)$/.exec(range);if(!m)fail(416,'다운로드 구간을 확인해주세요.');const offset=Number(m[1]);const end=m[2]?Math.min(Number(m[2]),f.size-1):f.size-1;if(offset>=f.size||end<offset)fail(416,'다운로드 구간을 확인해주세요.');options.range={offset,length:end-offset+1};}
 const object=await env.MEDIA.get(f.object_key,options);if(!object)fail(404,'파일 보관 기간이 지났어요.');
 const headers=new Headers({'Content-Type':'application/octet-stream','Accept-Ranges':'bytes','Cache-Control':'private, no-store','Content-Disposition':"attachment; filename*=UTF-8''"+encodeURIComponent(f.name),'Content-Length':String(object.range?.length??object.size)});
 if(range&&object.range)headers.set('Content-Range',`bytes ${object.range.offset}-${object.range.offset+object.range.length-1}/${f.size}`);
 return new Response(object.body,{status:range?206:200,headers});
}
async function route(request,env){
 const url=new URL(request.url),path=url.pathname.split('/').filter(Boolean),method=request.method;
 if(path[0]!=='v1')fail(404,'요청 주소를 확인해주세요.');
 if(path[1]==='health'&&method==='GET'){const w=await first(env,'SELECT last_seen FROM worker_state WHERE id=1');return json({ok:true,worker_online:!!w&&w.last_seen>time()-120,part_bytes:PART_BYTES,max_video_bytes:Number(env.MAX_VIDEO_BYTES),retention_hours:24});}
 if(path[1]==='admin'){
  await secret(request,env.ADMIN_KEY);
  if(path[2]==='invites'&&method==='POST'){
   const b=await body(request),token=random(),iid=id(),ttl=Math.min(30,Math.max(1,Number(b.days)||7));
   await stmt(env,'INSERT INTO invites(id,token_hash,label,expires_at,max_jobs,created_at) VALUES(?,?,?,?,?,?)',iid,await hash(token),String(b.label||'초대 고객').slice(0,80),time()+ttl*86400,Math.min(20,Math.max(1,Number(b.max_jobs)||5)),time()).run();
   return json({id:iid,invite_url:env.APP_URL+'#invite='+token,expires_in_days:ttl});
  }
  if(path[2]==='invites'&&path[3]&&method==='DELETE'){await stmt(env,'UPDATE invites SET revoked=1 WHERE id=?',path[3]).run();return json({revoked:true});}
  fail(404,'관리 요청을 확인해주세요.');
 }
 if(path[1]==='download'&&path[2]&&method==='GET'){
  const p=await readTicket(env,url.searchParams.get('ticket')||'');if(p.fid!==path[2])fail(401,'파일 접근 권한을 확인해주세요.');
  const f=await first(env,'SELECT f.* FROM files f JOIN jobs j ON j.id=f.job_id WHERE f.id=? AND j.status=\'done\' AND j.expires_at>? AND f.complete=1',path[2],time());if(!f)fail(404,'결과 파일이 만료됐어요.');return download(request,env,f);
 }
 if(path[1]==='worker')return workerRoute(request,env,path);
 const owner=await invite(request,env);
 if(path[1]==='session'&&method==='GET')return json({label:owner.label,expires_at:owner.expires_at});
 if(path[1]!=='jobs')fail(404,'요청 주소를 확인해주세요.');
 if(path.length===2){
  if(method==='GET')return json(await rows(env,'SELECT id AS job_id,title,status,percent,message,created_at,expires_at FROM jobs WHERE invite_id=? ORDER BY created_at DESC LIMIT 30',owner.id));
  if(method==='POST'){
   const b=await body(request);if(!validFile(b.name,b.size,Math.min(5e9,Number(env.MAX_VIDEO_BYTES)))||! /\.(mp4|mov|webm)$/i.test(b.name))fail(400,'5GB 이하 MP4·MOV·WebM 영상을 선택해주세요.');
   const fps=Number(b.fps)||24;if(fps<1||fps>120||!/^\d\d:\d\d:\d\d[:;]\d\d$/.test(b.start_tc||''))fail(400,'FPS와 시작 타임코드를 확인해주세요.');
   const jid=id(),maxResult=Math.min(4e9,Number(env.MAX_RESULT_BYTES)),reserved=b.size+maxResult;
   try{await stmt(env,'INSERT INTO jobs(id,invite_id,title,config,input_bytes,reserved_bytes,created_at,updated_at,expires_at) VALUES(?,?,?,?,?,?,?,?,?)',jid,owner.id,String(b.title||b.name).slice(0,120),JSON.stringify({fps,start_tc:b.start_tc,fingerprint:/^[a-f0-9]{64}$/.test(b.fingerprint||'')?b.fingerprint:null}),b.size,reserved,time(),time(),time()+86400).run();}
   catch(e){fail(409,'무료 저장 공간이 사용 중이거나 초대 작업 횟수를 모두 사용했어요. 기존 작업이 정리된 뒤 다시 시도해주세요.');}
   try{const f=await newFile(env,{id:jid},'input',b.name,b.size);return json({job_id:jid,file:f,part_bytes:PART_BYTES});}
   catch(e){await stmt(env,'UPDATE jobs SET status=\'failed\',error=\'파일 전송 준비에 실패했어요.\' WHERE id=?',jid).run();throw e;}
  }
 }
 const j=await job(env,path[2],owner);
 if(path.length===3&&method==='GET'){
  const files=await rows(env,'SELECT id,kind,name,size,complete FROM files WHERE job_id=?',j.id);
  return json({...view(j),files});
 }
 if(path[3]==='cancel'&&method==='POST'){
  if(!['done','failed','canceled','purged'].includes(j.status))await stmt(env,'UPDATE jobs SET status=\'canceled\',message=\'작업을 취소했어요.\',updated_at=? WHERE id=?',time(),j.id).run();return json({status:'canceled'});
 }
 if(path[3]==='upload'&&path[4]){
  if(j.status!=='uploading')fail(409,'업로드할 수 없는 작업 상태예요.');
  const f=await file(env,j.id,path[4]);if(f.kind!=='input')fail(403,'입력 영상만 올릴 수 있어요.');
  if(path[5]==='parts'&&method==='GET')return json(await rows(env,'SELECT number,size FROM parts WHERE file_id=? ORDER BY number',f.id));
  if(path[5]==='parts'&&path[6]&&method==='PUT')return json(await uploadPart(request,env,f,Number(path[6])));
  if(path[5]==='complete'&&method==='POST'){await complete(env,f);await stmt(env,'UPDATE jobs SET status=\'queued\',message=\'스튜디오 분석 서버를 기다리고 있어요.\',updated_at=? WHERE id=? AND status=\'uploading\'',time(),j.id).run();return json({status:'queued'});}
 }
 if(path[3]==='result'&&method==='GET'){
  if(j.status!=='done'||j.expires_at<time())fail(409,'결과가 아직 준비되지 않았거나 만료됐어요.');
  const files=await rows(env,'SELECT * FROM files WHERE job_id=? AND kind!=\'input\' AND complete=1',j.id);
  return json({summary:JSON.parse(j.summary),test:j.engine_mode==='mock',files:await Promise.all(files.map(async f=>({name:f.name,kind:f.kind,bytes:f.size,url:'/v1/download/'+f.id+'?ticket='+await signed(env,{fid:f.id,exp:time()+900})})))});
 }
 fail(404,'요청 주소를 확인해주세요.');
}
async function workerRoute(request,env,path){
 await secret(request,env.WORKER_KEY);const method=request.method;await touch(env);
 if(path[2]==='claim'&&method==='POST'){
  const nonce=random();
  const j=await stmt(env,`UPDATE jobs SET status='running',lease=?,lease_until=?,updated_at=?,message='스튜디오에서 영상을 준비하고 있어요.' WHERE id=(SELECT id FROM jobs WHERE status='queued' AND expires_at>? ORDER BY created_at LIMIT 1) AND NOT EXISTS(SELECT 1 FROM jobs WHERE status IN ('running','needs_review','uploading_result')) RETURNING *`,nonce,time()+120,time(),time()).first();
  if(!j)return new Response(null,{status:204});
  const f=await first(env,'SELECT * FROM files WHERE job_id=? AND kind=\'input\' AND complete=1',j.id);
  return json({job_id:j.id,title:j.title,lease:nonce,config:JSON.parse(j.config),input:{name:f.name,size:f.size},max_result_bytes:Number(env.MAX_RESULT_BYTES)});
 }
 if(path[2]!=='jobs'||!path[3])fail(404,'실행기 요청을 확인해주세요.');
 const j=await lease(request,env,path[3]);
 if(path[4]==='video'&&method==='GET'){const f=await first(env,'SELECT * FROM files WHERE job_id=? AND kind=\'input\'',j.id);return download(request,env,f);}
 if(path[4]==='progress'&&method==='POST'){
  const b=await body(request),status=['running','needs_review','uploading_result'].includes(b.status)?b.status:j.status;
  await stmt(env,'UPDATE jobs SET status=?,stage=?,percent=?,message=?,lease_until=?,updated_at=?,engine_mode=? WHERE id=? AND lease=?',status,String(b.stage||'').slice(0,40),Math.min(100,Math.max(0,Number(b.percent)||0)),String(b.message||'분석 중입니다.').slice(0,300),time()+120,time(),b.engine_mode==='mock'?'mock':'external',j.id,j.lease).run();return json({ok:true});
 }
 if(path[4]==='fail'&&method==='POST'){await stmt(env,'UPDATE jobs SET status=\'failed\',error=\'스튜디오 분석에 실패했어요. 스튜디오에 문의해주세요.\',updated_at=? WHERE id=?',time(),j.id).run();return json({ok:true});}
 if(path[4]==='outputs'&&path.length===5&&method==='POST'){
  const b=await body(request),items=b.files,maxResult=Math.min(4e9,Number(env.MAX_RESULT_BYTES));
  if(!Array.isArray(items)||!items.length||items.length>8||items.filter(x=>x.kind==='aaf').length!==1||items.some(x=>!validFile(x.name,x.size,maxResult))||items.reduce((n,x)=>n+x.size,0)>maxResult)fail(400,'결과 파일 크기와 목록을 확인해주세요. 무료 구성의 결과 합계는 4GB까지입니다.');
  const old=await rows(env,'SELECT id,name,size,kind FROM files WHERE job_id=? AND kind!=\'input\'',j.id);
  if(old.length){if(JSON.stringify(old.map(x=>[x.name,x.size]).sort())!==JSON.stringify(items.map(x=>[x.name,x.size]).sort()))fail(409,'기존 결과 업로드와 목록이 달라요.');return json({files:old,part_bytes:PART_BYTES});}
  const saved=[];for(const f of items)saved.push(await newFile(env,j,f.kind==='aaf'?'aaf':'extra',f.name,f.size));
  return json({files:saved,part_bytes:PART_BYTES});
 }
 if(path[4]==='outputs'&&path[5]){
  const f=await file(env,j.id,path[5]);if(f.kind==='input')fail(403,'결과 파일만 올릴 수 있어요.');
  if(path[6]==='parts'&&path[7]&&method==='PUT')return json(await uploadPart(request,env,f,Number(path[7])));
  if(path[6]==='complete'&&method==='POST'){await complete(env,f);return json({complete:true});}
 }
 if(path[4]==='done'&&method==='POST'){
  const b=await body(request),files=await rows(env,'SELECT kind,complete FROM files WHERE job_id=? AND kind!=\'input\'',j.id);
  if(!files.length||files.some(f=>!f.complete)||!files.some(f=>f.kind==='aaf'))fail(409,'결과 전송이 아직 끝나지 않았어요.');
  const summary={};for(const k of ['foot_steps','foot_candidates','move_clips','spots'])summary[k]=Math.max(0,Math.floor(Number(b.summary?.[k])||0));
  await stmt(env,'UPDATE jobs SET status=\'done\',percent=100,message=\'AAF 초안이 준비됐어요.\',summary=?,updated_at=? WHERE id=?',JSON.stringify(summary),time(),j.id).run();return json({status:'done'});
 }
 fail(404,'실행기 요청을 확인해주세요.');
}
export async function cleanup(env){
 await stmt(env,"UPDATE jobs SET status='failed',error='스튜디오 서버 연결이 끊겼어요. 스튜디오에 문의해주세요.' WHERE status IN ('running','needs_review','uploading_result') AND lease_until<?",time()).run();
 const jobs=await rows(env,"SELECT id FROM jobs WHERE expires_at<? AND status!='purged' LIMIT 8",time());
 for(const j of jobs){
  const files=await rows(env,'SELECT * FROM files WHERE job_id=?',j.id);let ok=true;
  for(const f of files){try{if(!f.complete)await env.MEDIA.resumeMultipartUpload(f.object_key,f.upload_id).abort();await env.MEDIA.delete(f.object_key);}catch{ok=false;}}
  // Never release reserved space until all stored objects are removed.
  if(ok)await env.DB.batch([stmt(env,'DELETE FROM parts WHERE file_id IN (SELECT id FROM files WHERE job_id=?)',j.id),stmt(env,'DELETE FROM files WHERE job_id=?',j.id),stmt(env,"UPDATE jobs SET status='purged',message='파일 보관 기간이 지났어요.',reserved_bytes=0 WHERE id=?",j.id)]);
 }
}
export default {
 async fetch(request,env){
  const origin=request.headers.get('Origin'),allowed=(env.ALLOWED_ORIGINS||'').split(',').map(s=>s.trim());
  if(origin&&!allowed.includes(origin))return json({error:{message:'허용되지 않은 웹 주소예요.'}},403);
  let response;
  if(request.method==='OPTIONS')response=new Response(null,{status:204});
  else try{response=await route(request,env);}catch(e){response=json({error:{message:e instanceof Failure?e.message:'서비스 응답을 확인해주세요. 잠시 후 다시 시도해주세요.'}},e instanceof Failure?e.status:500);}
  const h=new Headers(response.headers);h.set('X-Content-Type-Options','nosniff');h.set('Referrer-Policy','no-referrer');
  if(origin){h.set('Access-Control-Allow-Origin',origin);h.set('Vary','Origin');h.set('Access-Control-Allow-Methods','GET,POST,PUT,DELETE,OPTIONS');h.set('Access-Control-Allow-Headers','Authorization,Content-Type,X-Lease,Range');h.set('Access-Control-Expose-Headers','Content-Range,Content-Length,Accept-Ranges');}
  return new Response(response.body,{status:response.status,headers:h});
 },
 async scheduled(event,env,ctx){ctx.waitUntil(cleanup(env));}
};
