# 엔진 연결용 로컬 API — 구현 명세 (v0.1, 2026-10-23)

웹 화면(`index.html`)이 맥스튜디오의 JUNOVA 폴리 엔진을 쓰도록, 맥스튜디오에서 도는 **로컬 API 서버**를 만든다.
엔진은 GPU 모델과 외장 드라이브 라이브러리가 필요해서 Vercel에서는 돌 수 없다.

```
웹 화면 (index.html) ──HTTP──▶ 로컬 API 서버 (server/, FastAPI) ──subprocess──▶ 엔진 (계약을 지키는 명령)
        ▲                                   │                                     │
        └──────── 진행률 · 결과 AAF ◀────────┘◀──── progress.json · result.json ────┘
```

## 1. 현재 상태 (확인한 사실)
- 웹: 백엔드 없음. Supabase 신청 저장만 외부 통신. 브라우저 안에서 AAF 읽기·걸음 검출·AAF ZIP 내보내기.
- 엔진: HTTP API 없음. Mac 쪽은 폴더 대기열(`queue/*.sh` → `done/*.log`, `heartbeat.txt`).
  결과는 오디오를 포함한 AAF(회차당 1.3~2.4GB) + 스팟목록·인물표 CSV.
- 엔진 단계: 영상 분석(포즈·흐름·컷) → 장면·인물 판별 → 동기화 예측 → 발소리 → 무브 → 스팟 → AAF 작성.

## 2. 구성
| 항목 | 값 |
| --- | --- |
| 위치 | `server/` (Python 3.11+, FastAPI + uvicorn) |
| 실행 | `uvicorn server.main:app --host 127.0.0.1 --port 8800` |
| 정적 화면 | 서버가 `/`에서 `index.html`을 그대로 내보냄 (같은 주소에서 쓰면 CORS·Safari 문제 없음) |
| 작업 폴더 | `JUNOVA_DATA_DIR` (기본 `~/JUNOVA_jobs/<job_id>/`) |
| 엔진 명령 | `JUNOVA_ENGINE_CMD` (기본 `python -m server.mock_engine`) |
| 인증 | `JUNOVA_API_TOKEN` 설정 시 `Authorization: Bearer <token>` 필수. 미설정이면 127.0.0.1에서만 허용 |
| CORS | `JUNOVA_ALLOWED_ORIGINS` (쉼표 목록, 기본: 같은 주소만) |
| 경로 등록 허용 범위 | `JUNOVA_ALLOWED_ROOTS` (쉼표 목록. 이 밖의 경로는 `files/link`에서 거절) |
| 동시 실행 | 한 번에 작업 1개 (GPU). 나머지는 `queued` |

`.env.example`에 위 변수 이름만 적는다(값 없음).

## 3. API
상태 값: `created · queued · running · needs_review · done · failed · canceled`
단계 키(순서 고정): `video_analysis · people_scene · sync · foot · move · spot · aaf`

| 메서드 | 경로 | 요청 | 응답 |
| --- | --- | --- | --- |
| GET | `/api/health` | — | `{ok, engine:{cmd, version}, jobs:{running, queued}, disk_free_gb}` |
| POST | `/api/jobs` | `{title, fps, start_tc, options}` | `{job_id, status:"created"}` |
| POST | `/api/jobs/{id}/files` | multipart: `kind`(`video`·`aaf`·`audio`·`ref_video`), `relpath`(오디오 폴더 안 상대 경로), `file` — 디스크로 흘려 저장(메모리에 다 올리지 않음) | `{saved, kind, relpath, bytes}` |
| POST | `/api/jobs/{id}/files/link` | `{kind, path}` — 맥스튜디오 안의 파일·폴더 경로 (GB 단위 영상용). `JUNOVA_ALLOWED_ROOTS` 밖이면 403 | `{linked, kind, path}` |
| POST | `/api/jobs/{id}/start` | — (영상 없으면 400) | `{status:"queued"}` |
| GET | `/api/jobs/{id}` | — | `{job_id, title, status, stage, stage_index, stages_total, percent, message, warnings[], created_at, updated_at, error?}` |
| GET | `/api/jobs/{id}/events` | — | (선택) Server-Sent Events로 위 상태를 변할 때마다 |
| GET·PUT | `/api/jobs/{id}/review/people` | PUT: `[{track_id, shoe, weight, exclude}]` | 인물·신발표. `needs_review`일 때 PUT 후 자동 재개 |
| GET | `/api/jobs/{id}/result` | — | `{files:[{name, kind, bytes, url}], summary:{foot_steps, foot_candidates, move_clips, spots}}` |
| GET | `/api/jobs/{id}/result/aaf` | — | AAF 파일. **Range 요청(이어받기) 지원** 필수 (1~2GB) |
| GET | `/api/jobs/{id}/result/{name}` | — | 그 밖의 결과 파일 (CSV·README) |
| POST | `/api/jobs/{id}/cancel` | — | 엔진 프로세스 종료 후 `canceled` |
| GET | `/api/jobs` | — | 최근 작업 목록 |

오류는 `{error:{code, message}}` + 알맞은 HTTP 코드. 사용자에게 보일 `message`는 한국어.

## 4. 엔진 실행 계약 (서버 ↔ 엔진)
서버는 엔진을 이렇게만 부른다: `$JUNOVA_ENGINE_CMD run <job_dir>`

`<job_dir>/job.json` (서버가 씀)
```json
{
  "job_id": "j_20261023_0001",
  "title": "콜드 EP01",
  "video": {"path": "<job_dir>/in/video/콜드_ep01.mp4", "fps": 24, "start_tc": "01:00:00:00"},
  "reference": {
    "aaf": null, "audio_dir": null, "video": null,
    "session_to_video_offset_frames": 2880
  },
  "options": {
    "foot": true, "move": true, "spot": true,
    "candidate_tracks": "muted",
    "people_review": false
  },
  "output": {"dir": "<job_dir>/out", "name": "콜드_EP01_AI_Foot_Move_Spot3", "media": "embedded"}
}
```
- `candidate_tracks`: `muted`(후보 트랙을 클립 게인 −144dB로 넣음) · `off`(빼기)
- `reference`: 같은 작품의 완성 세션이 있을 때(선택). 없으면 null

`<job_dir>/progress.json` (엔진이 씀 — 임시 파일에 쓴 뒤 rename으로 교체)
```json
{"status": "running", "stage": "foot", "stage_index": 4, "stages_total": 7,
 "percent": 58, "message": "발소리를 고르고 있어요", "warnings": [], "updated_at": "2026-10-23T13:40:00Z"}
```
- 엔진이 사람 확인이 필요하면 `status:"needs_review"` + `<job_dir>/review/people.json`을 쓰고 기다린다.
  서버는 PUT 받은 내용을 `<job_dir>/review/people_answer.json`으로 쓴다.

`<job_dir>/result.json` (엔진이 끝에 씀)
```json
{"aaf": "out/콜드_EP01_AI_Foot_Move_Spot3_v1.aaf",
 "files": ["out/스팟목록.csv", "out/인물_신발표.csv", "out/README_AI초안.txt"],
 "summary": {"foot_steps": 909, "foot_candidates": 268, "move_clips": 444, "spots": 160}}
```
- 종료 코드 0 = 성공. 0이 아니면 서버가 `failed` + stderr 마지막 줄을 `error`로.
- 서버는 `progress.json`을 1초마다 읽어 상태를 갱신한다. 엔진이 30초 넘게 갱신하지 않으면 `warnings`에 "응답이 느려요"를 추가(실패 처리는 하지 않음).

### 가짜 엔진 `server/mock_engine.py`
- 같은 계약으로 7단계를 몇 초씩 진행시키고, pyaaf2로 **작은 실제 AAF**(트랙 2~3개, 합성 클릭음 몇 개, `FootCand_*` 트랙은 클립 게인 −144dB)를 만든다.
- 환경 변수로 실패·`needs_review`를 흉내 낼 수 있게 (`MOCK_FAIL_AT=foot`, `MOCK_REVIEW=1`).
- 진짜 엔진 래퍼는 엔진 쪽에서 같은 계약으로 따로 제공한다 (`JUNOVA_ENGINE_CMD`만 바꾸면 교체).

## 5. 웹 화면 변경 (index.html)
- 상단에 **엔진 서버 상태 표시**: 같은 주소의 `/api/health`가 응답하면 "엔진 서버 연결됨", 아니면 지금 브라우저 모드 그대로.
- 서버 모드의 입력: 새 영상(필수), 기존 AAF·연결 오디오 폴더·기존 영상(선택), 시작 TC, fps, 옵션(발소리·무브·스팟, 후보 트랙 끄기/소리 꺼 두기).
  - 큰 파일은 "맥스튜디오 경로로 등록" 입력칸도 제공(`files/link`).
  - 오디오 폴더는 폴더 구조(상대 경로)를 유지해 올린다.
- 실행: 작업 만들기 → 파일 올리기(파일별 진행률) → 시작 → 2초마다 `GET /api/jobs/{id}` → 기존 `setProgress` 화면에 단계·퍼센트·메시지 표시.
- 완료: 결과 영역에 요약 수치와 "멀티트랙 AAF 받기"(→ `/result/aaf`), CSV 받기 링크. 실패 시 한국어 메시지와 다시 시도.
- `needs_review`: 인물·신발표를 표로 보여 주고 수정 후 "이대로 진행".
- Supabase 신청 폼, 데모 버튼, 기존 브라우저 모드는 그대로.

## 6. 완료 기준
- `uvicorn` 실행 → `http://127.0.0.1:8800`에서 화면이 열리고 "엔진 서버 연결됨" 표시.
- 가짜 엔진으로: 영상 1개 올리기 → 7단계 진행률 → AAF 다운로드 → pyaaf2로 열어 트랙·클립 수 확인.
- 1GB 이상 파일 업로드가 메모리를 키우지 않음(스트리밍 저장), AAF 다운로드 Range 동작.
- 취소·실패·`needs_review` 흐름 확인.
- `pytest server/tests` (API 단위 테스트 + 가짜 엔진 끝까지 실행) 통과.
- `index.html`을 서버 없이 열어도(또는 Vercel에서) 지금 기능이 그대로 동작.

## 7. 이번 범위에서 뺄 것
- 진짜 엔진 이식(엔진 쪽에서 따로), 결제·회원가입, 외부 인터넷 공개(터널), 완성 세션 학습 API(`/api/sessions`는 다음 단계).
