# 슬슬 (Seulseul) — Backend

사진 기반 뷰티 재예약 Agent "슬슬"의 백엔드(FastAPI)다. 12시간 해커톤 MVP로,
연결된 데모 앨범(사진 6장)의 시간별 변화와 개인 시술 주기를 결합해 관리 시점을 판단하고,
관리 시점이거나 임박한 중요 일정(결혼식 등)이 있으면 [smolagents](https://github.com/huggingface/smolagents)
`ToolCallingAgent`가 Google Calendar와 네일샵 슬롯을 조회해 예약 후보를 준비한다.

**이 저장소는 백엔드만 다룬다.** 프론트엔드(React + Vite)는 별도 저장소에서 구현한다.
`docs/api-contract.md`가 백엔드-프론트엔드 간 유일한 인터페이스 기준이다.

## 기술 스택

- **FastAPI** + **Pydantic v2** — API 서버
- **smolagents** `ToolCallingAgent`(`CodeAgent` 아님) — 예약 후보 탐색 Agent (`/schedule`, `/retry`에서만 실행)
- **OpenAI API** — Vision 변화 분석(오프라인 스크립트), Agent용 LLM
- **Google Calendar API v3** (`google-api-python-client` + OAuth) — 중요 일정 조회, Free/Busy 조회, 이벤트 생성
- DB 없음 — `backend/data/*.json` 고정 fixture + 프로세스 인메모리 상태로 동작한다
  (운영 전환 시 논리 데이터 모델은 [`docs/erd.md`](docs/erd.md) 참고)

## 공개 API

| Method | Path | Agent 실행 | 설명 |
|---|---|---|---|
| GET | `/api/health` | ✕ | 서버/모드 상태 확인 |
| POST | `/api/albums/connect` | ✕ | 데모 앨범 연결 |
| POST | `/api/analyses` | ✕ | 사진 분석 → 관리 시점 판정 → 중요 일정/역방향 구간 계산 |
| POST | `/api/analyses/{id}/schedule` | ✅ | Agent가 3-Tool로 예약 후보(최대 3개) 탐색 |
| POST | `/api/analyses/{id}/retry` | ✅ | 다음 주(NEXT_WEEK) 구간에서 동일 Agent 플로우 재실행 |
| POST | `/api/bookings/{id}/confirm` | ✕ | 재확인 후 예약 확정 + (LIVE일 때) 실제 Google Calendar 이벤트 생성 |

요청/응답 스키마, 오류 코드, 프론트엔드 연동 흐름(fetch/curl 예시 포함)은 **[`docs/api-contract.md`](docs/api-contract.md)**에 있다.

## 시작하기

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate        # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env          # 값 채우기 (아래 "환경 변수" 참고)

uvicorn app.main:app --reload --port 8000
```

서버가 뜨면 `GET http://localhost:8000/api/health`로 확인한다.

## 환경 변수

`backend/.env.example`을 `.env`로 복사해 채운다. 주요 변수:

| 변수 | 기본값 | 설명 |
|---|---|---|
| `VISION_MODE` | `CACHED` | `POST /api/analyses`에만 노출. `LIVE`면 실제 OpenAI Vision 호출 |
| `AGENT_MODE` | `LIVE` | `/schedule`, `/retry`에만 노출. `LIVE`가 아니면 결정론 fallback만 사용 |
| `CALENDAR_MODE` | `CACHED` | 모든 분석/예약 응답에 노출. `LIVE`면 실제 Google Calendar 사용, 읽기 실패 시 CACHED로 자동 fallback |
| `FRONTEND_ORIGIN` | 로컬 후보 4개 | CORS 허용 origin. 쉼표로 복수 지정 가능 |
| `GOOGLE_CREDENTIALS_PATH` / `GOOGLE_TOKEN_PATH` | `./credentials.json` / `./token.json` | Google OAuth (아래 "Google Calendar LIVE 연동" 참고) |
| `GOOGLE_CALENDAR_ID` | `primary` | 조회/생성 대상 캘린더 |
| `APP_TIMEZONE` | `Asia/Seoul` | Calendar API 호출 시 사용하는 타임존 |
| `OPENAI_API_KEY`, `VISION_MODEL`, `AGENT_MODEL`(`_ID`/`_API_KEY`) | — | 각각 Vision 캐시 생성 스크립트, Agent LLM 전용 |

해커톤 시연용 조합(`CACHED` 전체 / `LIVE` 전체)은 [`backend/.env.demo.example`](backend/.env.demo.example)에 정리돼 있다.

`.env`, `credentials.json`, `token.json`은 `.gitignore`로 차단돼 있으며 **절대 커밋하지 않는다.**

## Google Calendar LIVE 연동 준비

1. Google Cloud Console에서 OAuth 클라이언트(데스크톱 앱) JSON을 받아 `GOOGLE_CREDENTIALS_PATH`(기본 `backend/credentials.json`)에 저장한다.
2. 토큰을 최초 발급한다(브라우저 인증, 사람이 직접 실행할 때만 열림):
   ```bash
   python scripts/issue_google_token.py
   ```
3. 읽기 전용 smoke test로 검증한다(이벤트 생성/수정/삭제 없음):
   ```bash
   python scripts/calendar_live_smoke_test.py
   ```

FastAPI 서버 자체는 요청 처리 중 브라우저 인증을 절대 실행하지 않는다 — `token.json`이 없거나
만료돼 갱신할 수 없으면 그 요청만 CACHED로 fallback(읽기)하거나 502로 실패(confirm의 승인 직전
안전성 검증·이벤트 생성)한다.

## 테스트

```bash
cd backend
pytest -q
```

현재 **234개** 테스트가 통과한다. Google API·OAuth·smolagents LLM 호출은 전부 mock/monkeypatch로
대체되며(`tests/conftest.py`의 안전장치), 실제 네트워크 호출 없이 전체 스위트가 실행된다.

## 데모 실행

이미 떠 있는 서버에 실제 HTTP 요청을 보내 전체 플로우(`albums/connect → analyses → schedule → (retry) → confirm`)를
검증하는 클라이언트 스크립트:

```bash
python scripts/demo_e2e.py                    # confirm 직전까지만 (기본)
python scripts/demo_e2e.py --confirm           # 실제 승인까지 (LIVE면 실제 Calendar 이벤트 생성 가능)
python scripts/demo_e2e.py --confirm --retry   # retry 흐름도 함께 검증
```

시연 대본은 [`docs/demo-scenario.md`](docs/demo-scenario.md)를 참고한다.

## 디렉터리 구조

```
backend/
  app/
    api/routes.py          # 공개 API 6개
    agent/                 # AgentRunContext, EventCollector, Tool 3개, runner
    services/               # 결정론 정책(policy.py), Vision/Calendar/네일샵/스케줄/confirm 서비스
    models/schemas.py       # Pydantic 스키마 (API 계약의 원본)
    google_auth.py          # OAuth 토큰 로드/갱신 공용 로직 (스크립트·런타임 공유)
  data/                     # 고정 JSON fixture (album, treatment_history, vision_cache, calendar_* 등)
  scripts/                  # issue_google_token.py, calendar_live_smoke_test.py, demo_e2e.py 등
  spikes/                   # smolagents ToolCallingAgent 스파이크
  tests/                    # pytest 전체 스위트
docs/
  api-contract.md           # 프론트엔드 연동 기준 문서 (유일한 인터페이스 계약)
  erd.md                    # 운영 전환 시 논리 데이터 모델 (현재는 DB 미사용)
  demo-scenario.md          # 심사/시연 대본
  backend-screen-map.md     # prototype 화면 ↔ API 응답 필드 대응표
```


