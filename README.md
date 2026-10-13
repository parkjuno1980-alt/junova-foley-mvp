# JUNOVA Foley Lab

기존 Pro Tools AAF와 영상을 분석해 새 영상의 발소리 초안을 만들고, 멀티트랙 AAF로 내보내는 브라우저 MVP입니다.

- 시작 파일: `index.html`
- 사용자 제공 JUNOVA 로고와 다크모드
- 신청: 이름·연락처·남긴 말을 Supabase에 저장
- 공개 키는 삽입만 가능하며, 신청 조회·수정·삭제는 차단
- 실제 작업 영상·AAF·녹음과 비밀 키는 Git에 포함하지 않음

## 로컬 확인

```sh
python3 -m http.server 8765
```

`http://localhost:8765`를 열어주세요. 처음 분석할 때 CDN에서 브라우저용 Python·영상 분석 모델을 다운로드합니다. 업로드 영상·오디오는 브라우저 안에서 처리합니다.

## 자동 배포

- 공개 주소: https://junova-foley-mvp.vercel.app
- 연결 저장소: https://github.com/parkjuno1980-alt/junova-foley-mvp
- Vercel 프로젝트: JUNOVA / junova-foley-mvp
- 운영 브랜치: main

Vercel의 GitHub 연결을 통해 `main`에 push하면 자동 배포합니다. Vercel은 index.html만 공개 출력 폴더에 복사하며 Supabase CLI 개발 의존성은 설치하지 않습니다.

```sh
git add . && git commit -m "Update MVP" && git push origin main
```

실제 사용자의 AAF·걷기 영상과 Pro Tools에서의 싱크는 별도 검수가 필요합니다.
