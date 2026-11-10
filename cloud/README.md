# AutoFoley 초대형 원격 처리 — 비용을 낮춘 구성

공식 웹(Vercel)은 유지하고 Cloudflare Workers·D1·R2와 스튜디오 Mac을 연결한다. 기존 Supabase 신청 폼은 변경하지 않는다. 고객은 새 영상만 올리고 Mac의 스튜디오 라이브러리로 만든 AAF를 같은 브라우저에서 받는다. 고객 컴퓨터에는 SSH, 엔진 토큰, 앱 설치가 필요 없다.

## 비용과 적용한 제한

2026-11-03 공식 문서 기준으로 비교했다.

| 구성 | 시작 비용과 조건 |
| --- | --- |
| Supabase Pro를 파일 저장소로 사용 | 월 US$25부터. 파일 저장·전송량에 따라 추가 과금 가능 |
| Cloudflare Workers Free + D1 Free + R2 Standard | 각 무료 사용량 안이면 서비스 사용료 US$0부터. Mac 전기·기존 인터넷 비용은 별도 |
| Mac에 직접 공개 접속 | 별도 파일 저장소 비용은 줄지만 안정된 공개 주소·접속 제어·회선과 대용량 전송 구성을 따로 운영해야 함 |

초기 사용량이 작은 초대형 서비스에는 R2를 선택했다. R2 Standard 무료 사용량은 10 GB-month, 월 Class A 100만 회·Class B 1천만 회이며 인터넷 다운로드 전송료는 없다. Workers Free는 하루 요청 10만 회·CPU 10ms 한도가 있다. 무료 한도를 넘으면 계정 요금제에 따라 과금 또는 요청 실패가 발생할 수 있다.

**무조건 무료인 상품이나 계정 전체의 강제 지출 상한은 아니다.** R2는 무료 사용량이 포함된 종량제 구독이며 활성화에 결제 정보·약관 동의가 필요할 수 있다. 사용자가 해당 화면에서 직접 활성화한다. Workers 유료 플랜과 Supabase 유료 전환은 이 구성에 필요하지 않다.

이 앱의 비용 제한:

- 입력 한 개 최대 **5,000,000,000바이트(5GB)**.
- 결과 합계 최대 **4,000,000,000바이트(4GB)**. 그 이상은 오류로 중단한다.
- 모든 미정리 작업의 `입력 크기 + 결과 예약 4GB` 합계를 **9,000,000,000바이트**로 제한한다. 대기·실패·취소 작업도 파일을 정리하기 전까지 예약을 차지한다.
- 작업 생성 후 24시간에 파일이 만료된다. 15분 간격 정리 작업에서 R2 파일·미완료 multipart를 삭제한 뒤 예약을 해제한다. 삭제 실패 시 예약을 유지한다.
- Mac은 한 번에 한 작업을 실행한다. 클라우드 파일은 나눠 올리고, Mac은 스트리밍으로 내려받는다. 원본 라이브러리·모델은 클라우드로 올리지 않는다.
- 재시도된 동일 조각은 다시 R2에 쓰지 않는다. 결과 다운로드는 짧은 만료 링크와 Range를 사용한다.
- 이 제한은 이 서비스의 전용 버킷에 대한 것이다. 같은 계정의 다른 버킷·앱 사용량, 장애·운영자 설정 변경까지 비용 0원으로 보장하지 않는다. Cloudflare 사용량과 청구 알림도 확인한다.

## 구성 파일

- `worker.mjs`, `protocol.mjs`: 초대·작업·업로드·다운로드·Mac 실행기 API.
- `migrations/0001_jobs.sql`: D1의 초대 해시, 소유 권한, 원자적 배정, 저장 용량 예약.
- `wrangler.jsonc`: Workers Free용 설정, R2 비공개 바인딩, D1, 정리 일정. 배포 전 실제 D1 ID로 바꾼다.
- `../server/remote_worker.py`: Mac에서 외부로 작업을 가져오는 실행기. 기존 `run <job_dir>` 계약을 사용한다.
- `../index.html`: 초대 링크로 새 영상만 입력하는 경로. 기존 로컬 브라우저·엔진 모드는 유지한다.

## 계정 연결과 배포

`cloud/`에서 실행한다. 비밀 값은 Git에 넣지 않는다. `.dev.vars.example`에는 이름만 제공하며 `.dev.vars`는 로컬 시험용이다.

```sh
npm ci
npx wrangler login --scopes account:read user:read workers:write workers_scripts:write d1:write
npx wrangler d1 create autof-remote-jobs
npx wrangler r2 bucket create autof-remote-media
npx wrangler d1 migrations apply autof-remote-jobs --remote
```

D1 생성 결과의 ID를 `wrangler.jsonc`에 적용한다. R2는 비공개로 유지하고 `r2.dev` 공개 주소를 켜지 않는다. 다른 용도로 버킷을 공유하지 않는다. R2 미완료 multipart 만료 정책을 함께 설정하면 추가 안전장치가 된다.

`ADMIN_KEY`, `WORKER_KEY`, `DOWNLOAD_KEY`는 각각 별도로 충분히 긴 무작위 값을 만들고 `wrangler secret put` 또는 `wrangler secret bulk`의 표준 입력으로 설정한다. 실제 값을 명령 인수·문서·PR·로그에 넣지 않는다. 관리 키는 초대 발급에만, 실행기 키는 Mac의 한정된 API에만 사용한다.

로컬 검증 후 `npx wrangler deploy`로 **Cloudflare API**를 배포한다. Vercel CLI 직접 배포는 하지 않는다. API 공개 HTTPS 주소만 `index.html`의 `remoteServiceConfig.apiBase`에 넣고 웹 변경은 PR → main → Vercel Git 자동 배포로 반영한다.

## Mac 실행기

기존 엔진 코드는 공개 저장소로 가져오지 않는다. 실행기 파일은 기존 엔진 웹 체크아웃의 `server/`에 추가할 수 있다. 설정 파일은 Git 밖에 JSON으로 저장하고 권한 600을 적용한다. 환경 변수로도 설정할 수 있다.

- `JUNOVA_REMOTE_API`: 배포한 HTTPS API 주소.
- `JUNOVA_WORKER_KEY`: 전용 실행기 인증 값. 관리자 키와 구분한다.
- `JUNOVA_ENGINE_CMD`: 기존 `mac/junova_engine.sh` 실행 명령. 새 모델이나 엔진을 만들지 않는다.
- `JUNOVA_REMOTE_DATA_DIR`: 전용 로컬 작업 폴더. 기존 스튜디오 원본과 구분한다. 이 폴더의 로컬 미디어·로그 보관과 디스크 공간도 관리한다.

```sh
python3 server/remote_worker.py --config /path/outside-git/worker.json
```

실행기는 30초마다 작업을 확인하고 15초마다 진행·생존 상태를 보고한다. 재부팅 후 자동 실행하려면 LaunchAgent로 같은 명령을 등록한다. 기존 로컬 엔진 API 서버를 재시작하거나 공개하지 않는다. 현재 Mac과 다른 장치에 SSH 인증을 배포하지 않는다.

네트워크 단절·배정 만료·고객 취소 시 실행기를 중단하고, 자체 엔진 자식 프로세스를 종료한다. 갑작스러운 전원 종료나 SIGKILL 뒤 이전 엔진 프로세스가 남는 상황은 운영자가 확인해야 한다. 만료된 배정은 실패로 표시하며 자동으로 중복 분석하지 않는다.

## 초대와 고객 화면

관리 API `POST /v1/admin/invites`에 관리 키로 `{label, days, max_jobs}`를 보내 초대 링크를 만든다. API가 반환하는 실제 초대 링크도 비공개로 전달한다. `DELETE /v1/admin/invites/{id}`로 취소할 수 있다. 초대는 서버에 해시만 저장한다.

고객 링크는 공식 홈페이지의 `#invite=...` 형태다. 초대 값은 파일 서버의 전체 관리자 키가 아니다. 고객은 해당 초대에 속한 작업만 생성·조회·취소·다운로드할 수 있다. 파일 전송 재개는 같은 원본을 다시 선택해야 하며, 원본 구별용 앞·뒤 일부와 크기의 지문을 사용한다. 전체 파일 무결성 해시를 검증하는 기능과는 구분한다.

페이지를 새로 열어도 초대 링크의 ‘내 지난 원격 작업’에서 진행 상황과 결과를 확인한다. 링크는 만료 전까지 본인 작업에 접근하는 권한이므로 공개 게시하지 않는다. 다운로드 URL은 파일 하나로 제한하고 15분 후 만료된다. 영상 원본에서 화면 밖 동작을 모두 복원하거나 싱크 정확도를 보장하는 기능은 아니다. Pro Tools에서 결과를 검수한다.

## 로컬 검증

```sh
npx wrangler d1 migrations apply autof-remote-jobs --local
npx wrangler dev --local --ip 127.0.0.1 --port 8787
```

시험 키는 `.dev.vars`에만 둔다. Node 시험은 `node --test tests/*.test.mjs`로 실행한다. 종단 간 시험은 로컬 API에만 연결하고 다음 환경 변수를 준다.

```sh
TEST_REMOTE_URL=http://127.0.0.1:8787 TEST_PYTHON=/path/to/test/python node --test tests/*.test.mjs
```

종단 간 시험은 합성 영상 바이트와 명시적으로 선택한 가짜 엔진을 사용한다. 초대 인증, 다른 고객 접근 거절, 대용량 조각 규격, 저장 예약 제한, 중복 배정 방지, 취소 후 배정 무효화, AAF 반환과 Range를 검증한다. 실제 스튜디오 엔진의 새 영상 분석이나 Pro Tools 가져오기 성공을 뜻하지 않는다.

브라우저 시험에는 loopback 주소에서만 `?remote-test=http://127.0.0.1:8787`을 허용한다. 공식 사이트에서는 URL로 API 주소를 바꿀 수 없고 코드에 설정된 HTTPS 주소만 사용한다.

## 공식 근거

- [R2 요금·무료 사용량](https://developers.cloudflare.com/r2/pricing/)
- [R2 활성화](https://developers.cloudflare.com/r2/get-started/)
- [Workers 무료 한도](https://developers.cloudflare.com/workers/platform/limits/)
- [D1 요금](https://developers.cloudflare.com/d1/platform/pricing/)
- [Supabase 요금](https://supabase.com/pricing)

## 2026-11-10 배포 확인과 남은 검증

API: `https://autof-remote-api.autof-remote-api.workers.dev`. R2 전용 비공개 버킷과 미완료 업로드 1일 중단 정책을 만들었다. `/v1/health`는 정상 응답하나 Mac 실행기는 아직 미접속이다. 기존 API 시험 5개와 로컬 연결 시험 4개가 통과했다. 실제 5GB 업로드, 실제 스튜디오 엔진 결과 및 Pro Tools 가져오기는 아직 검증하지 않았다.

`server/install_remote_worker.py`는 비공개 설치 묶음 안에서 실행하는 macOS 설치 도우미다. 같은 폴더의 `worker.json`에는 전용 실행기 키와 API 주소만 넣고 Git에 올리지 않는다. 기존 conda 환경과 엔진 경로를 확인하고 사용자 Library에 실행기·권한 600 설정을 설치한다. 로그인 시 실행하는 LaunchAgent만 등록하며 기존 엔진 API는 건드리지 않는다. 맥스튜디오 설치 후 health의 worker_online과 실제 엔진 결과를 별도로 확인한다.
