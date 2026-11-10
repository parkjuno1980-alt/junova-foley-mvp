"""Outbound Mac worker for the invitation service; no public listener or SSH needed.

Runs the existing engine contract. Never imports model code or uploads the library.
"""
import argparse
import fcntl
import http.client
import json
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit
import urllib.request
import urllib.error


def atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp=path.with_suffix('.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    temp.replace(path)


class Remote:
    def __init__(self):
        self.base=os.environ.get('JUNOVA_REMOTE_API','').rstrip('/')
        self.key=os.environ.get('JUNOVA_WORKER_KEY','')
        parsed=urlsplit(self.base)
        if not self.key or parsed.scheme not in {'https','http'} or not parsed.hostname or parsed.query or parsed.fragment or parsed.username:
            raise ValueError('JUNOVA_REMOTE_API와 JUNOVA_WORKER_KEY를 설정해주세요.')
        if parsed.scheme!='https' and parsed.hostname not in {'127.0.0.1','localhost'}:
            raise ValueError('외부 API에는 HTTPS가 필요합니다.')

    def headers(self, lease=None):
        h={'Authorization':'Bearer '+self.key}
        if lease: h['X-Lease']=lease
        return h

    def call(self,path,body=None,lease=None,method='POST'):
        raw=json.dumps(body or {}).encode() if method!='GET' else None
        h=self.headers(lease)
        if raw is not None:h['Content-Type']='application/json'
        request=urllib.request.Request(self.base+'/v1/worker/'+path,data=raw,headers=h,method=method)
        with urllib.request.urlopen(request,timeout=30) as response:
            return json.load(response) if response.status!=204 else None

    def download(self,j, target):
        request=urllib.request.Request(self.base+'/v1/worker/jobs/'+j['job_id']+'/video',headers=self.headers(j['lease']))
        temp=target.with_suffix(target.suffix+'.download')
        try:
            with urllib.request.urlopen(request,timeout=90) as response, temp.open('wb') as stream:
                total=0
                while True:
                    block=response.read(1024*1024)
                    if not block:break
                    total+=len(block)
                    if total>j['input']['size']:raise ValueError('입력 파일 크기가 맞지 않습니다.')
                    stream.write(block)
            if total!=j['input']['size']:raise ValueError('입력 파일 전송이 끝나지 않았습니다.')
            temp.replace(target)
        finally:temp.unlink(missing_ok=True)

    def part(self,path,data,lease):
        # One bounded part at a time; no whole-file buffering or multipart encoding.
        parsed=urlsplit(self.base)
        cls=http.client.HTTPSConnection if parsed.scheme=='https' else http.client.HTTPConnection
        connection=cls(parsed.hostname,parsed.port,timeout=120)
        try:
            h=self.headers(lease);h.update({'Content-Length':str(len(data)),'Content-Type':'application/octet-stream'})
            connection.request('PUT',parsed.path+'/v1/worker/'+path,body=data,headers=h)
            response=connection.getresponse();raw=response.read(16384)
            if response.status!=200:raise RuntimeError('결과 파일 전송에 실패했습니다.')
            return json.loads(raw)
        finally:connection.close()


def config(j,directory):
    # No browser-provided file path, library path, shell command or reference session.
    suffix=Path(j['input']['name']).suffix.lower()
    video=directory/('input'+suffix)
    return {'job_id':j['job_id'],'title':j['title'],
            'video':{'path':str(video),'fps':j['config']['fps'],'start_tc':j['config']['start_tc']},
            'reference':{'aaf':None,'audio_dir':None,'video':None,'session_to_video_offset_frames':2880},
            'options':{'foot':True,'move':True,'spot':True,'candidate_tracks':'muted','people_review':False},
            'output':{'dir':str(directory/'out'),'name':'Foley_Draft','media':'embedded'}}


def result_files(directory,result,max_bytes):
    entries=[('aaf',result['aaf'])]+[('extra',p) for p in result.get('files',[])]
    out=(directory/'out').resolve();files=[]
    for kind,rel in entries:
        if not isinstance(rel,str) or Path(rel).is_absolute():raise ValueError('잘못된 결과 경로입니다.')
        path=(directory/rel).resolve()
        if not path.is_relative_to(out) or not path.is_file():raise ValueError('결과 파일을 확인해주세요.')
        if path.suffix.lower() not in {'.aaf','.csv','.txt','.json'}:continue
        if any(f['name']==path.name for f in files):raise ValueError('결과 이름이 겹칩니다.')
        files.append({'name':path.name,'size':path.stat().st_size,'kind':kind,'path':path})
    if len(files)>8 or sum(f['size'] for f in files)>max_bytes:raise ValueError('무료 구성의 결과 파일 합계 한도를 넘었습니다.')
    return files


def stop_process(proc):
    if proc.poll() is not None:return
    try:
        os.killpg(proc.pid,signal.SIGTERM)
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid,signal.SIGKILL);proc.wait()
    except ProcessLookupError:pass


def process(remote,j,data_dir,command):
    directory=(data_dir/j['job_id']).resolve()
    if not directory.is_relative_to(data_dir) or directory==data_dir:raise ValueError('작업 ID를 확인해주세요.')
    directory.mkdir(parents=True,exist_ok=True)
    settings=config(j,directory);atomic(directory/'job.json',settings)
    done=threading.Event();lost=threading.Event();progress={'status':'running','message':'스튜디오에서 영상을 내려받고 있어요.','percent':0}
    def heartbeat():
        failures=0
        while not done.is_set():
            try:
                current=progress.copy()
                if progress.get('status') not in {'uploading_result'}:
                    try:current=json.loads((directory/'progress.json').read_text())
                    except (OSError,ValueError):pass
                current['engine_mode']='mock' if 'server.mock_engine' in command else 'external'
                remote.call('jobs/'+j['job_id']+'/progress',current,j['lease']);failures=0
            except urllib.error.HTTPError as e:
                if e.code in {401,403,404,409}:lost.set();break
                failures+=1
            except Exception:failures+=1
            if failures>=3:lost.set();break
            done.wait(15)
    thread=threading.Thread(target=heartbeat,daemon=True);thread.start();proc=None
    try:
        remote.download(j,Path(settings['video']['path']))
        if lost.is_set():raise RuntimeError('작업 배정이 만료되었습니다.')
        with (directory/'engine.log').open('ab') as log:
            proc=subprocess.Popen(command+['run',str(directory)],stdout=log,stderr=log,start_new_session=True)
            while proc.poll() is None:
                if lost.wait(1):raise RuntimeError('작업이 취소되거나 연결이 끊겼습니다.')
        if proc.returncode:raise RuntimeError('엔진 실행에 실패했습니다.')
        result=json.loads((directory/'result.json').read_text())
        files=result_files(directory,result,j['max_result_bytes'])
        progress.update(status='uploading_result',message='AAF를 브라우저로 보낼 준비를 하고 있어요.',percent=95,engine_mode='mock' if 'server.mock_engine' in command else 'external')
        remote.call('jobs/'+j['job_id']+'/progress',progress,j['lease'])
        info=remote.call('jobs/'+j['job_id']+'/outputs',{'files':[{k:v for k,v in f.items() if k!='path'} for f in files]},j['lease'])
        for target in info['files']:
            source=next(f for f in files if f['name']==target['name'])
            with source['path'].open('rb') as stream:
                number=1
                while block:=stream.read(info['part_bytes']):
                    if lost.is_set():raise RuntimeError('작업 연결이 만료됐습니다.')
                    remote.part(f"jobs/{j['job_id']}/outputs/{target['id']}/parts/{number}",block,j['lease']);number+=1
            remote.call(f"jobs/{j['job_id']}/outputs/{target['id']}/complete",{},j['lease'])
        remote.call('jobs/'+j['job_id']+'/done',{'summary':result.get('summary',{})},j['lease'])
        print('완료:',j['job_id'],flush=True)
    except BaseException as exc:
        if proc is not None:stop_process(proc)
        try:remote.call('jobs/'+j['job_id']+'/fail',{},j['lease'])
        except Exception:pass
        diagnostic={'type':type(exc).__name__,'http_status':getattr(exc,'code',None),'phase':progress.get('status')}
        atomic(directory/'worker_error.json',diagnostic)
        print('작업 중단. 로컬 engine.log를 확인하세요:',j['job_id'],diagnostic,flush=True)
        if isinstance(exc,(KeyboardInterrupt,SystemExit)):raise
    finally:
        done.set();thread.join(timeout=35)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--once',action='store_true');parser.add_argument('--config');args=parser.parse_args()
    if args.config:
        source=Path(args.config).expanduser()
        if source.stat().st_mode & 0o077:raise SystemExit('실행기 설정 파일 권한을 600으로 설정해주세요.')
        settings=json.loads(source.read_text())
        for key in ['JUNOVA_REMOTE_API','JUNOVA_WORKER_KEY','JUNOVA_ENGINE_CMD','JUNOVA_REMOTE_DATA_DIR']:
            if key in settings:os.environ[key]=str(settings[key])
    def terminate(signum,frame):raise SystemExit(0)
    signal.signal(signal.SIGTERM,terminate)
    remote=Remote()
    directory=Path(os.environ.get('JUNOVA_REMOTE_DATA_DIR','~/JUNOVA_remote_jobs')).expanduser().resolve();directory.mkdir(parents=True,exist_ok=True)
    command=shlex.split(os.environ.get('JUNOVA_ENGINE_CMD',''))
    if not command:raise SystemExit('기존 엔진의 JUNOVA_ENGINE_CMD를 설정해주세요. 가짜 엔진은 명시적으로 선택합니다.')
    with (directory/'.worker.lock').open('w') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise SystemExit('이미 다른 원격 실행기가 동작 중입니다.')
        while True:
            try:
                j=remote.call('claim')
                if j:process(remote,j,directory,command)
            except (KeyboardInterrupt,SystemExit):raise
            except Exception:print('작업 서버 연결을 다시 확인합니다.',flush=True)
            if args.once:break
            time.sleep(30)


if __name__=='__main__':main()
