# AGENTS.md — JUNOVA Foley MVP

이 저장소에서 일하는 코딩 에이전트(Codex 등)를 위한 작업 규칙입니다.

## 프로젝트
- 폴리 작업자를 위한 웹 MVP. 기존 Pro Tools AAF·영상을 바탕으로 새 영상의 폴리 초안(멀티트랙 AAF)을 만든다.
- 지금 화면: `index.html` 한 파일(브라우저 안에서 Pyodide + pyaaf2, MediaPipe Pose로 동작). 제품 설명: `MVP.md`.
- 배포: `main` push → Vercel 자동 배포. Vercel은 `index.html`만 `public/`으로 복사한다(`vercel.json`).

## 지금 할 일
`docs/ENGINE_API.md`에 적힌 **로컬 API 서버(`server/`)와 웹 화면 연결**을 구현한다.
- 실제 폴리 엔진(JUNOVA 엔진 v1.6)은 이 저장소에 없다. 엔진은 맥스튜디오 외장 드라이브에 따로 있고,
  API 서버는 `docs/ENGINE_API.md`의 "엔진 실행 계약"으로만 엔진을 부른다.
- 개발·테스트는 계약을 지키는 **가짜 엔진(`server/mock_engine.py`)**으로 한다. 엔진 알고리즘을 새로 만들지 말 것.

## 지킬 것
- 비밀 키·비밀번호·토큰을 코드나 문서에 넣지 않는다. 설정은 환경 변수로 받고 `.env.example`에 이름만 적는다.
- 영상·AAF·오디오 파일(`*.aaf *.wav *.mov *.mp4` 등)은 커밋하지 않는다(`.gitignore` 유지). 테스트용 미디어는 테스트 안에서 생성한다.
- Supabase 신청 폼 코드와 설정은 건드리지 않는다.
- 기존 브라우저 모드(서버 없이 도는 지금 기능)는 그대로 둔다. 서버 연결은 "엔진 서버가 있으면 쓰는" 추가 경로다.
- Vercel 빌드가 깨지지 않게 한다: `server/`는 배포 대상이 아니며, `index.html`은 여전히 단독으로 열려야 한다.
- 화면 문구는 한국어, `MVP.md`의 말투(짧고 쉬운 말)를 따른다.
- 작업은 브랜치 + Pull Request로 올린다. `main`에 바로 push하지 않는다.

## 확인 방법
- `python3 -m http.server 8765` → `http://localhost:8765` (기존 브라우저 모드)
- `docs/ENGINE_API.md`의 "2. 구성" 표대로 서버를 띄우고, 가짜 엔진으로 작업 생성 → 진행률 → AAF 다운로드까지 웹 화면에서 끝까지 확인.
- `pytest server/tests` 통과.
