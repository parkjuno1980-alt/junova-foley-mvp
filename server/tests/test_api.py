import math
from pathlib import Path
import time

import aaf2
from fastapi.testclient import TestClient
import pytest

from server.main import create_app, read_json, write_json


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('JUNOVA_DATA_DIR', str(tmp_path/'jobs'))
    monkeypatch.setenv('MOCK_STAGE_SECONDS', '.05')
    for key in ['JUNOVA_API_TOKEN','JUNOVA_ALLOWED_ROOTS','JUNOVA_ALLOWED_ORIGINS','MOCK_FAIL_AT','MOCK_REVIEW','JUNOVA_ENGINE_CMD']:
        monkeypatch.delenv(key, raising=False)
    with TestClient(create_app(), base_url='http://127.0.0.1', client=('127.0.0.1',1234)) as c:
        yield c


def create(c, **kwargs):
    r=c.post('/api/jobs',json=kwargs)
    assert r.status_code==200, r.text
    return r.json()['job_id']


def video(c,jid):
    r=c.post(f'/api/jobs/{jid}/files',data={'kind':'video'},files={'file':('test.mp4',b'mock video fixture','video/mp4')})
    assert r.status_code==200, r.text


def wait(c,jid,expected='done'):
    deadline=time.monotonic()+15
    while time.monotonic()<deadline:
        data=c.get(f'/api/jobs/{jid}').json()
        if data['status']==expected:
            return data
        if data['status'] in {'failed','canceled'}:
            pytest.fail(str(data))
        time.sleep(.1)
    pytest.fail(str(data))


def test_end_to_end_aaf_and_range(client,tmp_path):
    assert client.get('/').status_code==200
    assert client.get('/api/health').json()['engine']['mock'] is True
    jid=create(client)
    assert client.post(f'/api/jobs/{jid}/start').status_code==400
    video(client,jid)
    for path in ['audio/room/left.wav','audio/other/left.wav']:
        assert client.post(f'/api/jobs/{jid}/files',data={'kind':'audio','relpath':path},files={'file':('left.wav',b'fixture')}).status_code==200
    assert client.post(f'/api/jobs/{jid}/start').status_code==200
    assert client.post(f'/api/jobs/{jid}/start').status_code==409
    wait(client,jid)
    result=client.get(f'/api/jobs/{jid}/result').json()
    assert result['summary']['foot_steps']==6
    r=client.get(f'/api/jobs/{jid}/result/aaf')
    assert r.status_code==200
    path=tmp_path/'download.aaf';path.write_bytes(r.content)
    with aaf2.open(str(path),'r') as f:
        comp=next(f.content.compositionmobs())
        assert len(list(comp.slots))==3
        assert len(list(f.content.essencedata))==1
        candidates=next(s for s in comp.slots if s.name.startswith('FootCand_'))
        gains=[c for c in candidates.segment.components if isinstance(c,aaf2.components.OperationGroup)]
        assert len(gains)==3
        assert math.isclose(20*math.log10(float(next(iter(gains[0].parameters)).value)),-144,abs_tol=.001)
    partial=client.get(f'/api/jobs/{jid}/result/aaf',headers={'Range':'bytes=0-31'})
    assert partial.status_code==206
    assert partial.content==r.content[:32]
    assert client.get(f'/api/jobs/{jid}/result/aaf',headers={'Range':f'bytes={len(r.content)+1}-'}).status_code==416
    ticket=client.post(f'/api/jobs/{jid}/download/aaf').json()
    assert client.get(ticket['url']).content==r.content


def test_paths_and_origins(client,tmp_path):
    jid=create(client)
    for path in ['../escape.wav','/tmp/escape.wav','a/../../escape.wav','C:/file.wav']:
        assert client.post(f'/api/jobs/{jid}/files',data={'kind':'audio','relpath':path},files={'file':('x.wav',b'')}).status_code==400
    assert client.post(f'/api/jobs/{jid}/files/link',json={'kind':'video','path':'/etc/passwd'}).status_code==403
    assert client.post('/api/jobs',json={},headers={'Origin':'https://untrusted.example'}).status_code==403
    assert client.get('/api/health',headers={'Host':'untrusted.example'}).status_code==403
    assert client.get('/api/jobs/not-a-job').status_code==404
    assert client.get('/.env').status_code==404


def test_review_and_candidate_off(client):
    jid=create(client,options={'people_review':True,'candidate_tracks':'off'})
    video(client,jid);client.post(f'/api/jobs/{jid}/start')
    wait(client,jid,'needs_review')
    people=client.get(f'/api/jobs/{jid}/review/people').json()
    people[0]['shoe']='운동화'
    assert client.put(f'/api/jobs/{jid}/review/people',json=people).status_code==200
    wait(client,jid)
    assert client.get(f'/api/jobs/{jid}/result').json()['summary']['foot_candidates']==0


def test_fail_and_cancel(client,monkeypatch):
    monkeypatch.setenv('MOCK_FAIL_AT','foot')
    jid=create(client);video(client,jid);client.post(f'/api/jobs/{jid}/start')
    wait(client,jid,'failed')
    assert client.get(f'/api/jobs/{jid}/result').status_code==409
    monkeypatch.delenv('MOCK_FAIL_AT')
    monkeypatch.setenv('MOCK_STAGE_SECONDS','3')
    first=create(client);video(client,first);client.post(f'/api/jobs/{first}/start')
    wait(client,first,'running')
    second=create(client);video(client,second);client.post(f'/api/jobs/{second}/start')
    assert client.get(f'/api/jobs/{second}').json()['status']=='queued'
    assert client.post(f'/api/jobs/{second}/cancel').json()['status']=='canceled'
    assert client.post(f'/api/jobs/{first}/cancel').json()['status']=='canceled'
    time.sleep(.2)
    assert client.get(f'/api/jobs/{first}').json()['status']=='canceled'


def test_token_link_ticket_and_result_escape(tmp_path,monkeypatch):
    monkeypatch.setenv('JUNOVA_DATA_DIR',str(tmp_path/'jobs'))
    monkeypatch.setenv('JUNOVA_API_TOKEN','test-only-token')
    monkeypatch.setenv('JUNOVA_ALLOWED_ROOTS',str(tmp_path/'media'))
    monkeypatch.setenv('MOCK_STAGE_SECONDS','.05')
    media=tmp_path/'media';media.mkdir();(media/'test.mp4').write_bytes(b'fixture')
    (media/'escape').symlink_to('/etc')
    with TestClient(create_app(),base_url='http://127.0.0.1',client=('127.0.0.1',1234)) as c:
        assert c.get('/api/health').status_code==401
        c.headers['Authorization']='Bearer test-only-token'
        jid=create(c)
        assert c.post(f'/api/jobs/{jid}/files/link',json={'kind':'video','path':str(media/'escape/passwd')}).status_code==403
        assert c.post(f'/api/jobs/{jid}/files/link',json={'kind':'video','path':str(media/'test.mp4')}).status_code==200
        c.post(f'/api/jobs/{jid}/start');wait(c,jid)
        url=c.post(f'/api/jobs/{jid}/download/aaf').json()['url']
        del c.headers['Authorization']
        assert c.get(url,headers={'Range':'bytes=0-15'}).status_code==206
        assert c.get(url.replace('/result/aaf','')).status_code==401
        assert c.get(f'/api/jobs/{jid}/result/aaf').status_code==401
        c.headers['Authorization']='Bearer test-only-token'
        write_json(tmp_path/'jobs'/jid/'result.json',{'aaf':'../../outside.aaf','files':[]})
        assert c.get(f'/api/jobs/{jid}/result').status_code==409
