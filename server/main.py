"""Single-process, single-worker local API. Start with uvicorn, never --workers > 1."""
import asyncio
import ipaddress
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shlex
import shutil
import signal
import sys
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Literal
from urllib.parse import quote, urlsplit
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request, UploadFile, File, Form
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

ROOT = Path(__file__).resolve().parents[1]
STAGES = ['video_analysis', 'people_scene', 'sync', 'foot', 'move', 'spot', 'aaf']
TERMINAL = {'done', 'failed', 'canceled'}
Kind = Literal['video', 'aaf', 'audio', 'ref_video']


def now():
    return datetime.now(timezone.utc).isoformat()


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)


def read_json(path, default=None):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default


def fail(status, message, code='invalid_request'):
    raise HTTPException(status, detail={'code': code, 'message': message})


class Options(BaseModel):
    foot: bool = True
    move: bool = True
    spot: bool = True
    candidate_tracks: Literal['muted', 'off'] = 'muted'
    people_review: bool = False


class JobInput(BaseModel):
    title: str = Field(default='폴리 초안', min_length=1, max_length=120)
    fps: float = Field(default=24, ge=1, le=120)
    start_tc: str = Field(default='01:00:00:00', pattern=r'^\d{2}:\d{2}:\d{2}[:;]\d{2}$')
    session_to_video_offset_frames: int = Field(default=2880, ge=-10000000, le=10000000)
    options: Options = Field(default_factory=Options)


class LinkInput(BaseModel):
    kind: Kind
    path: str = Field(min_length=1, max_length=4096)


class Person(BaseModel):
    track_id: str = Field(min_length=1, max_length=120)
    shoe: str = Field(default='', max_length=120)
    weight: float = Field(default=1, ge=0, le=10)
    exclude: bool = False


class Manager:
    def __init__(self):
        self.data = Path(os.environ.get('JUNOVA_DATA_DIR') or '~/JUNOVA_jobs').expanduser().resolve()
        self.cmd = shlex.split(os.environ.get('JUNOVA_ENGINE_CMD') or '') or [sys.executable, '-m', 'server.mock_engine']
        self.mock = self.cmd[-1] == 'server.mock_engine'
        self.token = os.environ.get('JUNOVA_API_TOKEN', '')
        self.origins = [x.strip().rstrip('/') for x in os.environ.get('JUNOVA_ALLOWED_ORIGINS', '').split(',') if x.strip()]
        self.roots = [Path(x.strip()).expanduser().resolve() for x in os.environ.get('JUNOVA_ALLOWED_ROOTS', '').split(',') if x.strip()]
        self.jobs = {}
        self.locks = {}
        self.queue = asyncio.Queue()
        self.proc = None
        self.active = None
        self.tickets = {}

    def directory(self, jid):
        return self.data / jid

    def get(self, jid):
        if jid not in self.jobs:
            fail(404, '작업을 찾지 못했어요.', 'not_found')
        return self.jobs[jid]

    def save(self, jid, **changes):
        job = self.get(jid)
        job.update(changes, updated_at=now())
        write_json(self.directory(jid) / 'state.json', job)
        return job

    async def startup(self):
        self.data.mkdir(parents=True, exist_ok=True)
        # Restarted processes cannot be safely resumed automatically.
        for path in self.data.glob('j_*/state.json'):
            data = read_json(path)
            if data and re.fullmatch(r'j_[a-f0-9]{32}', data.get('job_id', '')):
                jid = data['job_id']
                self.jobs[jid] = data
                self.locks[jid] = asyncio.Lock()
                if data['status'] in {'queued', 'running', 'needs_review'}:
                    self.save(jid, status='failed', error='서버가 다시 시작됐어요. 새 작업으로 다시 실행해주세요.')
        self.worker = asyncio.create_task(self.work())

    async def kill(self):
        proc = self.proc
        if not proc or proc.returncode is not None:
            return
        try:
            os.killpg(proc.pid, signal.SIGTERM)
            await asyncio.wait_for(proc.wait(), timeout=3)
        except asyncio.TimeoutError:
            os.killpg(proc.pid, signal.SIGKILL)
            await proc.wait()
        except ProcessLookupError:
            pass

    async def shutdown(self):
        if self.active:
            self.save(self.active, status='failed', error='서버가 종료됐어요. 새 작업으로 다시 실행해주세요.')
        await self.kill()
        self.worker.cancel()
        try:
            await self.worker
        except asyncio.CancelledError:
            pass

    def result_files(self, jid):
        directory = self.directory(jid)
        result = read_json(directory / 'result.json')
        if not isinstance(result, dict) or not isinstance(result.get('aaf'), str):
            fail(409, '결과 AAF가 아직 준비되지 않았어요.', 'result_not_ready')
        paths = [('aaf', result['aaf'])] + [('extra', p) for p in result.get('files', [])]
        files = []
        for kind, rel in paths:
            if not isinstance(rel, str) or Path(rel).is_absolute():
                fail(409, '엔진 결과 경로를 확인해주세요.')
            path = (directory / rel).resolve()
            if not path.is_relative_to((directory / 'out').resolve()) or not path.is_file():
                fail(409, '엔진 결과 파일을 확인해주세요.')
            if any(x['name'] == path.name for x in files):
                fail(409, '결과 파일 이름이 겹쳐요.')
            files.append({'name': path.name, 'kind': kind, 'bytes': path.stat().st_size,
                          'url': f'/api/jobs/{jid}/result/' + ('aaf' if kind == 'aaf' else quote(path.name)), '_path': path})
        return files, result.get('summary', {})

    async def work(self):
        while True:
            jid = await self.queue.get()
            try:
                if self.get(jid)['status'] != 'queued':
                    continue
                self.active = jid
                self.save(jid, status='running', message='엔진을 시작하고 있어요.')
                directory = self.directory(jid)
                # Never shell=True; commands are administrator configuration, not web input.
                with (directory / 'engine.log').open('wb') as log:
                    self.proc = await asyncio.create_subprocess_exec(*self.cmd, 'run', str(directory), cwd=ROOT,
                                                                    stdout=log, stderr=log, start_new_session=True)
                    while self.proc.returncode is None:
                        await asyncio.sleep(1)
                        if self.get(jid)['status'] == 'canceled':
                            await self.kill()
                            break
                        p = directory / 'progress.json'
                        progress = read_json(p, {})
                        if not progress and time.time()-(directory/'job.json').stat().st_mtime > 30:
                            self.save(jid, warnings=['응답이 느려요. 작업은 계속 확인하고 있어요.'])
                        if progress:
                            stage = progress.get('stage')
                            changes = {k: progress[k] for k in ['message', 'warnings'] if k in progress}
                            if stage in STAGES:
                                changes.update(stage=stage, stage_index=STAGES.index(stage)+1)
                            try:
                                changes['percent'] = max(0, min(100, float(progress.get('percent', 0))))
                            except (TypeError, ValueError):
                                pass
                            changes['status'] = 'needs_review' if progress.get('status') == 'needs_review' else 'running'
                            warnings = changes.get('warnings', [])
                            changes['warnings'] = [str(x) for x in warnings] if isinstance(warnings, list) else []
                            if time.time()-p.stat().st_mtime > 30:
                                changes['warnings'].append('응답이 느려요. 작업은 계속 확인하고 있어요.')
                            self.save(jid, **changes)
                    code = await self.proc.wait()
                if self.get(jid)['status'] == 'canceled':
                    continue
                if code:
                    # Keep the full engine log local; do not expose paths/secrets from stderr.
                    self.save(jid, status='failed', error='엔진 실행에 실패했어요. 서버의 engine.log를 확인해주세요.', message='분석을 끝내지 못했어요.')
                else:
                    self.result_files(jid)
                    self.save(jid, status='done', percent=100, message='폴리 초안이 준비됐어요.')
            except asyncio.CancelledError:
                raise
            except Exception:
                self.save(jid, status='failed', error='엔진 실행 또는 결과 파일을 확인해주세요.')
            finally:
                self.proc = None
                self.active = None
                self.queue.task_done()


def create_app():
    manager = Manager()

    @asynccontextmanager
    async def lifespan(app):
        await manager.startup()
        yield
        await manager.shutdown()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.manager = manager

    @app.middleware('http')
    async def security(request, call_next):
        host = request.url.hostname or ''
        peer = request.client.host if request.client else ''
        def loopback(value):
            try:
                return ipaddress.ip_address(value).is_loopback
            except ValueError:
                return value == 'localhost'
        origin = request.headers.get('origin')
        same_origin = origin is None or origin.rstrip('/') == str(request.base_url).rstrip('/')
        if not manager.token and (not loopback(host) or not loopback(peer)):
            return JSONResponse({'error': {'code': 'local_only', 'message': '외부 접속에는 서버 토큰 설정이 필요해요.'}}, status_code=403)
        # Origin checks also apply to tokenless loopback requests (CSRF/DNS rebinding).
        if origin and not same_origin and origin.rstrip('/') not in manager.origins:
            return JSONResponse({'error': {'code': 'origin_denied', 'message': '허용되지 않은 웹 주소예요.'}}, status_code=403)
        if request.url.path.startswith('/api/') and request.method != 'OPTIONS' and manager.token:
            supplied = request.headers.get('authorization', '')
            ticket = manager.tickets.get(request.query_params.get('download', ''))
            download_ok = (request.method == 'GET' and ticket and ticket[0] == request.url.path and ticket[1] > time.time())
            if not download_ok and not secrets.compare_digest(supplied, 'Bearer ' + manager.token):
                return JSONResponse({'error': {'code': 'unauthorized', 'message': '엔진 연결 토큰을 확인해주세요.'}}, status_code=401)
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        return response

    app.add_middleware(CORSMiddleware, allow_origins=manager.origins, allow_methods=['GET','POST','PUT'],
                       allow_headers=['Authorization','Content-Type','Range'], expose_headers=['Content-Range','Accept-Ranges'])

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request, exc):
        detail = exc.detail if isinstance(exc.detail, dict) else {'code': 'request_error', 'message': '요청 주소나 내용을 확인해주세요.'}
        return JSONResponse({'error': detail}, status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return JSONResponse({'error': {'code': 'validation', 'message': '입력 값과 파일 형식을 확인해주세요.'}}, status_code=422)

    @app.get('/')
    def page():
        return FileResponse(ROOT / 'index.html')

    @app.get('/api/health')
    def health():
        return {'ok': True, 'engine': {'cmd': 'configured' if not manager.mock else 'mock_engine',
                'version': 'mock-v1' if manager.mock else 'external', 'mock': manager.mock},
                'jobs': {'running': int(manager.active is not None), 'queued': sum(j['status']=='queued' for j in manager.jobs.values())},
                'disk_free_gb': round(shutil.disk_usage(manager.data).free/1024**3, 1)}

    @app.post('/api/jobs')
    def create(body: JobInput):
        h, m, s, frame = map(int, re.split('[:;]', body.start_tc))
        if h>23 or m>59 or s>59 or frame>=round(body.fps):
            fail(422, '시작 타임코드와 FPS를 확인해주세요.')
        jid = 'j_' + uuid4().hex
        directory = manager.directory(jid)
        directory.mkdir(parents=True)
        config = {'job_id': jid, 'title': body.title, 'video': {'path': None, 'fps': body.fps, 'start_tc': body.start_tc},
                  'reference': {'aaf': None, 'audio_dir': None, 'video': None, 'session_to_video_offset_frames': body.session_to_video_offset_frames},
                  'options': body.options.model_dump(), 'output': {'dir': str(directory/'out'), 'name': 'Foley_Draft', 'media': 'embedded'}}
        write_json(directory/'job.json', config)
        manager.jobs[jid] = {'job_id': jid, 'title': body.title, 'status': 'created', 'stage': None, 'stage_index': 0,
                             'stages_total': 7, 'percent': 0, 'message': '작업 자료를 기다리고 있어요.', 'warnings': [],
                             'created_at': now(), 'updated_at': now(), 'mock': manager.mock}
        manager.locks[jid] = asyncio.Lock()
        manager.save(jid)
        return {'job_id': jid, 'status': 'created'}

    @app.get('/api/jobs')
    def jobs():
        return sorted(manager.jobs.values(), key=lambda x: x['created_at'], reverse=True)[:100]

    @app.get('/api/jobs/{jid}')
    def status(jid: str):
        return manager.get(jid)

    def editable(jid):
        if manager.get(jid)['status'] != 'created':
            fail(409, '자료는 작업을 시작하기 전에만 바꿀 수 있어요.')

    def register(jid, kind, path):
        config = read_json(manager.directory(jid)/'job.json')
        if kind == 'video':
            config['video']['path'] = str(path)
        else:
            config['reference'][{'aaf':'aaf', 'audio':'audio_dir', 'ref_video':'video'}[kind]] = str(path)
        write_json(manager.directory(jid)/'job.json', config)

    @app.post('/api/jobs/{jid}/files')
    async def upload(jid: str, kind: Kind = Form(...), relpath: str = Form(''), file: UploadFile = File(...)):
        manager.get(jid)
        async with manager.locks[jid]:
            editable(jid)
            raw = (relpath or file.filename or '').replace('\\', '/')
            rel = PurePosixPath(raw)
            if not raw or rel.is_absolute() or '..' in rel.parts or any(':' in x or any(ord(c)<32 for c in x) for x in rel.parts):
                fail(400, '파일의 상대 경로를 확인해주세요.')
            base = manager.directory(jid)/'in'/kind
            path = base.joinpath(*rel.parts).resolve()
            if not path.is_relative_to(base.resolve()) or path == base.resolve():
                fail(400, '파일 경로를 확인해주세요.')
            path.parent.mkdir(parents=True, exist_ok=True)
            temp = path.with_name(path.name + '.upload')
            count = 0
            try:
                with temp.open('wb') as out:
                    while chunk := await file.read(1024*1024):
                        await asyncio.to_thread(out.write, chunk)
                        count += len(chunk)
                temp.replace(path)
            except BaseException:
                temp.unlink(missing_ok=True)
                raise
            finally:
                await file.close()
            register(jid, kind, base if kind == 'audio' else path)
            return {'saved': True, 'kind': kind, 'relpath': rel.as_posix(), 'bytes': count}

    @app.post('/api/jobs/{jid}/files/link')
    async def link(jid: str, body: LinkInput):
        manager.get(jid)
        async with manager.locks[jid]:
            editable(jid)
            path = Path(body.path).expanduser().resolve()
            if not any(path.is_relative_to(root) for root in manager.roots):
                fail(403, '맥스튜디오의 허용된 작업 폴더 안 경로만 등록할 수 있어요.')
            if not (path.is_dir() if body.kind == 'audio' else path.is_file()):
                fail(400, '맥스튜디오에서 해당 파일이나 폴더를 찾지 못했어요.')
            register(jid, body.kind, path)
            return {'linked': True, 'kind': body.kind, 'path': str(path)}

    @app.post('/api/jobs/{jid}/start')
    async def start(jid: str):
        manager.get(jid)
        async with manager.locks[jid]:
            editable(jid)
            config = read_json(manager.directory(jid)/'job.json')
            if not config['video']['path'] or not Path(config['video']['path']).is_file():
                fail(400, '새 영상을 먼저 등록해주세요.')
            manager.save(jid, status='queued', message='앞선 작업이 끝나면 시작해요.')
            manager.queue.put_nowait(jid)
        return {'status': 'queued'}

    @app.post('/api/jobs/{jid}/cancel')
    async def cancel(jid: str):
        manager.get(jid)
        async with manager.locks[jid]:
            if manager.get(jid)['status'] not in TERMINAL:
                manager.save(jid, status='canceled', message='작업을 취소했어요.')
                if manager.active == jid:
                    await manager.kill()
        return {'status': manager.get(jid)['status']}

    @app.get('/api/jobs/{jid}/review/people')
    def people(jid: str):
        manager.get(jid)
        return read_json(manager.directory(jid)/'review/people.json', [])

    @app.put('/api/jobs/{jid}/review/people')
    def review(jid: str, body: list[Person]):
        if manager.get(jid)['status'] != 'needs_review':
            fail(409, '인물 확인을 기다리는 작업이 아니에요.')
        existing = read_json(manager.directory(jid)/'review/people.json', [])
        if sorted(p['track_id'] for p in existing) != sorted(p.track_id for p in body):
            fail(422, '인물표의 트랙 목록을 확인해주세요.')
        write_json(manager.directory(jid)/'review/people_answer.json', [p.model_dump() for p in body])
        # Engine must acknowledge the answer through progress.json before status changes.
        return {'saved': True}

    @app.get('/api/jobs/{jid}/result')
    def result(jid: str):
        if manager.get(jid)['status'] != 'done':
            fail(409, '결과가 아직 준비되지 않았어요.')
        files, summary = manager.result_files(jid)
        return {'files': [{k:v for k,v in f.items() if k!='_path'} for f in files], 'summary': summary}

    @app.get('/api/jobs/{jid}/result/{name}')
    def download(jid: str, name: str):
        if manager.get(jid)['status'] != 'done':
            fail(409, '결과가 아직 준비되지 않았어요.')
        files, _ = manager.result_files(jid)
        item = next((f for f in files if (name=='aaf' and f['kind']=='aaf') or f['name']==name), None)
        if not item:
            fail(404, '결과 파일을 찾지 못했어요.')
        return FileResponse(item['_path'], filename=item['name'], media_type='application/octet-stream')

    @app.post('/api/jobs/{jid}/download/{name}')
    def download_ticket(jid: str, name: str):
        if manager.get(jid)['status'] != 'done':
            fail(409, '결과가 아직 준비되지 않았어요.')
        files, _ = manager.result_files(jid)
        item = next((f for f in files if (name=='aaf' and f['kind']=='aaf') or f['name']==name), None)
        if not item:
            fail(404, '결과 파일을 찾지 못했어요.')
        manager.tickets = {k:v for k,v in manager.tickets.items() if v[1] > time.time()}
        ticket = secrets.token_urlsafe(32)
        # Scope ticket to one file; browser downloads without buffering GBs in JS.
        decoded_path = f'/api/jobs/{jid}/result/' + ('aaf' if item['kind']=='aaf' else item['name'])
        manager.tickets[ticket] = (decoded_path, time.time()+300)
        return {'url': item['url'] + '?download=' + ticket, 'expires_in': 300}

    return app


app = create_app()
