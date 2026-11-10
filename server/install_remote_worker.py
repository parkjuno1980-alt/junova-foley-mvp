"""Install the outbound worker for the current macOS user from a private bundle."""
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess


def main():
    source = Path(__file__).resolve().parent
    settings = json.loads((source / 'worker.json').read_text())
    home = Path.home()
    python = home / 'miniforge3/envs/junova-engine/bin/python'
    wrapper = Path('/Volumes/JUNOVAJUNO_WORK/AI 테스트/_분석결과/engine_home/mac/junova_engine.sh')
    if not python.is_file() or not wrapper.is_file():
        raise SystemExit('기존 junova-engine 환경 또는 외장 드라이브의 엔진을 찾지 못했습니다. 경로를 확인해주세요.')
    target = home / 'Library/Application Support/JUNOVA Remote'
    target.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(target, 0o700)
    settings['JUNOVA_ENGINE_CMD'] = 'bash ' + __import__('shlex').quote(str(wrapper))
    settings['JUNOVA_REMOTE_DATA_DIR'] = str(home / 'JUNOVA_remote_jobs')
    config = target / 'worker.json'
    # Create with private permissions before writing any credential.
    fd = os.open(config, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.chmod(config, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(settings, stream)
    shutil.copy2(source / 'remote_worker.py', target / 'remote_worker.py')
    agents = home / 'Library/LaunchAgents'
    agents.mkdir(parents=True, exist_ok=True)
    plist = agents / 'com.junova.remote-worker.plist'
    data = {
        'Label': 'com.junova.remote-worker',
        'ProgramArguments': [str(python), str(target / 'remote_worker.py'), '--config', str(config)],
        'RunAtLoad': True, 'KeepAlive': True, 'ThrottleInterval': 30,
        'WorkingDirectory': str(target),
        'StandardOutPath': str(target / 'worker.log'),
        'StandardErrorPath': str(target / 'worker-error.log'),
    }
    plist.write_bytes(plistlib.dumps(data))
    domain = 'gui/' + str(os.getuid())
    # Only replace our own LaunchAgent; leave existing engine/API processes alone.
    subprocess.run(['launchctl', 'bootout', domain, str(plist)], capture_output=True)
    subprocess.run(['launchctl', 'bootstrap', domain, str(plist)], check=True)
    print('원격 실행기를 등록했습니다. 클라우드 접속 확인은 별도로 필요합니다.')


if __name__ == '__main__':
    main()
