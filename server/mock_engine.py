"""Contract fixture, not a Foley analysis model. All output is synthetic and labeled."""
import json
import math
import os
from pathlib import Path
import struct
import sys
import time
import wave

import aaf2
from aaf2.rational import AAFRational

from server.main import STAGES, now, read_json, write_json


def build_aaf(directory, candidates=True):
    out = directory/'out'
    out.mkdir(parents=True, exist_ok=True)
    wav = out/'mock_click.wav'
    with wave.open(str(wav), 'wb') as audio:
        audio.setparams((1, 2, 48000, 0, 'NONE', 'not compressed'))
        audio.writeframes(b''.join(struct.pack('<h', int(math.sin(i*2*math.pi*180/48000)*12000*math.exp(-i/1800))) for i in range(12000)))
    with aaf2.open(str(out/'MOCK_Foley_Draft.aaf'), 'w') as f:
        amplitude = f.create.ParameterDef('e4962321-2267-11d3-8a4c-0050040ef7d2', 'ParameterDef_Amplitude', '', 'Rational')
        f.dictionary.register_def(amplitude)
        operation = f.create.OperationDef('9d2ea894-0968-11d3-8a38-0050040ef7d2', 'OperationDef_MonoAudioGain')
        operation.media_kind = 'sound'
        operation['NumberInputs'].value = 1
        operation['ParametersDefined'].append(amplitude)
        f.dictionary.register_def(operation)
        master = f.create.MasterMob('합성 테스트 클릭')
        f.content.mobs.append(master)
        master.import_audio_essence(str(wav), edit_rate=48000)
        comp = f.create.CompositionMob('MOCK · 실제 분석 결과가 아닙니다')
        comp.usage = 'Usage_TopLevel'
        f.content.mobs.append(comp)
        for n, name in enumerate(['Foot_L', 'Foot_R'] + (['FootCand_Test'] if candidates else [])):
            slot = comp.create_timeline_slot(48000)
            slot.name = name
            seq = f.create.Sequence(media_kind='sound')
            slot.segment = seq
            for beat in range(3):
                filler = f.create.Filler('sound', length=24000)
                seq.components.append(filler)
                clip = master.create_source_clip(slot_id=1, start=0, length=12000, media_kind='sound')
                if name.startswith('FootCand_'):
                    # MonoAudioGain uses amplitude, so -144 dB = 10**(-144/20).
                    gain = f.create.OperationGroup('OperationDef_MonoAudioGain', length=12000, media_kind='sound')
                    level = f.create.ConstantValue('ParameterDef_Amplitude', AAFRational('1/15848932'))
                    gain.parameters.append(level)
                    gain.segments.append(clip)
                    seq.components.append(gain)
                else:
                    seq.components.append(clip)
    wav.unlink()
    (out/'README_MOCK.txt').write_text('연결 확인용 합성 클릭 AAF입니다. 영상을 분석하지 않았습니다. Pro Tools 호환성은 별도 확인이 필요합니다.', encoding='utf-8')
    (out/'인물표.csv').write_text('track_id,shoe\nFoot_L,테스트 신발\n', encoding='utf-8-sig')
    return {'aaf': 'out/MOCK_Foley_Draft.aaf', 'files': ['out/README_MOCK.txt', 'out/인물표.csv'],
            'summary': {'foot_steps': 6, 'foot_candidates': 3 if candidates else 0, 'move_clips': 0, 'spots': 0}}


def run(directory):
    config = read_json(directory/'job.json')
    for i, stage in enumerate(STAGES):
        progress = {'status': 'running', 'stage': stage, 'stage_index': i+1, 'stages_total': 7,
                    'percent': int(i/7*100), 'message': f'연결 테스트 {i+1}/7 · {stage}',
                    'warnings': ['테스트 엔진입니다. 실제 영상 분석 결과가 아닙니다.'], 'updated_at': now()}
        if stage == os.environ.get('MOCK_FAIL_AT'):
            raise RuntimeError('의도한 테스트 실패: ' + stage)
        if stage == 'people_scene' and (os.environ.get('MOCK_REVIEW') == '1' or config['options']['people_review']):
            write_json(directory/'review/people.json', [{'track_id':'Foot_L', 'shoe':'테스트 신발', 'weight':1, 'exclude':False}])
            progress['status'] = 'needs_review'
            progress['message'] = '테스트 인물·신발표를 확인해주세요.'
            while not (directory/'review/people_answer.json').exists():
                progress['updated_at'] = now()
                write_json(directory/'progress.json', progress)
                time.sleep(.2)
            progress['status'] = 'running'
        write_json(directory/'progress.json', progress)
        time.sleep(float(os.environ.get('MOCK_STAGE_SECONDS', '1.2')))
    result = build_aaf(directory, config['options']['candidate_tracks'] != 'off')
    write_json(directory/'result.json', result)


if __name__ == '__main__':
    if len(sys.argv) != 3 or sys.argv[1] != 'run':
        raise SystemExit('usage: python -m server.mock_engine run JOB_DIR')
    run(Path(sys.argv[2]).resolve())
