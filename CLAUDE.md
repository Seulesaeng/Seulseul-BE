# 슬슬 백엔드 지침

## 프로젝트 개요
12시간 해커톤용 사진 기반 뷰티 재예약 Agent '슬슬'이다.
사용자가 연결한 데모 앨범(사진 6장)의 시간별 변화와 개인 시술 주기를 결합해
관리 시점을 판단한다. 관리 시점이거나 임박한 중요 일정(결혼식 등)이 있으면
smolagents ToolCallingAgent가 캘린더와 네일샵 슬롯을 조회해 예약 후보를 준비한다.

## 담당 범위
- 이 저장소에서는 **백엔드(FastAPI)만 구현**한다.
- 프론트엔드(React + Vite + CSS)는 다른 팀원이 별도로 구현한다.
  백엔드 작업 중에는 프론트 관련 파일을 생성하거나 수정하지 않는다.
- docs/api-contract.md가 백엔드-프론트엔드 간 유일한 인터페이스 기준이다.
  API 응답 필드를 바꿀 때는 반드시 이 문서를 먼저 수정한 뒤 구현을 바꾼다.
- docs/backend-screen-map.md는 prototype.html 화면과 API 응답 필드의 대응표다.
  화면에 보이는 모든 문구/기능이 백엔드 구현 대상은 아니라는 점을 이 문서로 판단한다.

## 외부 API 6개 (docs/api-contract.md가 기준)
1. `GET /api/health` — 서버/모드 상태 확인
2. `POST /api/albums/connect` — 데모 앨범 연결(`source:"DEMO"`) + 정렬된 사진 + `scanSummary` 반환
3. `POST /api/analyses` — 사진선택·Vision·시술주기·관리시점판정·중요일정조회·역방향권장구간계산까지만 수행.
   **Agent를 실행하지 않는다. `candidates`를 만들지 않는다.**
4. `POST /api/analyses/{analysis_id}/schedule` — **Agent가 유일하게 실행되는 지점.**
   `recommendedWindow`(analysis의 `reversePlan` 기반)에서 3-Tool로 최대 3개 예약 후보 탐색
5. `POST /api/analyses/{analysis_id}/retry` — 동일 Agent 플로우를 다른 `searchScope`로 재실행
6. `POST /api/bookings/{candidate_id}/confirm` — 승인 직전 재확인 → 예약 시뮬레이션 → (`CALENDAR_MODE=LIVE`일 때만) 실제 Calendar 이벤트 생성

## Agent Tool 3개 & 실행 위치
1. `get_calendar_busy_times`
2. `search_nail_shop_slots`
3. `prepare_booking_candidates`

- Agent(smolagents ToolCallingAgent)는 **`/schedule`, `/retry`에서만** 실행된다. `/analyses`는 절대 Agent를 호출하지 않는다(코드 레벨에서 Agent 모듈을 import조차 하지 않는 것으로 경계를 검증한다).
- 목표 이벤트(결혼식 등) 조회는 Agent Tool이 아니라 일반 함수 `CalendarClient.get_important_events()`가 담당한다(`/analyses`에서 사용, LLM이 호출/판단하지 않음).
- Agent는 Tool 호출 여부/횟수만 오케스트레이션한다(예: 선호샵 결과가 3개 미만이면 대체샵을 추가 조회). 실제 겹침 판정·가격·추천사유 산정은 Tool 내부의 결정론 코드가 담당한다.

## 로깅 규칙
- `evidenceLogs`(`/analyses` 전용): 사진선택/Vision/주기계산/관리시점정책/중요일정/역방향구간 각 단계를 일반 코드가 직접 기록한다.
- `executionLogs`(`/schedule`, `/retry` 전용): 각 Tool wrapper가 공용 `EventCollector`에 호출 시작·인자·결과·실패·검색범위 변경·제외 사유를 직접 기록한다.
- **LLM의 `final_answer` 문자열을 파싱해 로그를 만들지 않는다.** `agent.memory`/`step_callbacks`는 보조·디버깅 용도로만 사용한다. 로그의 기준은 실제 Tool 함수 실행이다.
- LLM이 생성한 자연어 "추론" 문장은 어떤 응답 필드에도 그대로 반환하지 않는다.
- 필수 테스트: (1) 실제 Tool 실행 시 로그가 생성되는지, (2) LLM final answer를 파싱하지 않는지.

## 절대 금지 (MVP 범위 밖)
- 네일 외 서비스(뿌리염색, 속눈썹 등), 다중 사용자, 실제 사진첩 접근, 실제 결제, 배포, 매장용 화면
- 자유 대화형 채팅(사용자 텍스트 입력 기반 대화 API) — prototype의 채팅 화면은 스크립트 재생이며 실제 대화 API가 필요하지 않다
- 활동 로그 누적 통계(다일 히스토리) — DB 없는 인메모리 구조에서 원천적으로 불가
- 정확한 mm 측정, 성장 속도 %(수치 정밀도 과장 금지) — `changeSignal`은 `level/trend/confidence` 범주형만 사용한다
- "온디바이스 분석" 등 실제 아키텍처와 다른 문구
- 하루 전 알림, 지속적 백그라운드 모니터링(푸시/스케줄러)

## 필수 기능 (포기 대상 아님)
- `upcomingEvent`/`reversePlan`의 CACHED 경로(결혼식 역방향 스케줄)는 반드시 동작해야 한다.
  `CALENDAR_MODE=LIVE`만 선택적으로 포기 가능하며, LIVE 실패 시 항상 CACHED 이벤트로 자동 fallback한다.

## Calendar 이벤트 생성 의미 (confirm 응답)
- `CALENDAR_MODE=CACHED`: `calendarEvent`는 `created:false, simulated:true, eventId:null, htmlLink:null` — 실제로 생성된 것처럼 보이면 안 된다.
- `CALENDAR_MODE=LIVE` 성공: `created:true, simulated:false`, 실제 `eventId`/`htmlLink` 포함.

## 환경 모드
- `VISION_MODE=LIVE | CACHED` (기본값 CACHED) — `/analyses` 응답에만 노출
- `AGENT_MODE=LIVE | FALLBACK | CACHED` (기본값 LIVE) — `/schedule`, `/retry` 응답에만 노출
- `CALENDAR_MODE=LIVE | CACHED` (기본값 CACHED) — 모든 분석/예약 응답에 노출

## 구현 순서
1. API 계약 + Pydantic 스키마
2. 6개 목(mock) API
3. JSON fixture
4. `/analyses` 결정론 로직
5. smolagents spike
6. `/schedule` Agent
7. `/retry`
8. `/confirm`
9. Google Calendar LIVE
10. 테스트 및 인계 문서

## 테스트 명령
Backend:
- `uvicorn app.main:app --reload --port 8000`
- `pytest -q`

테스트를 실행하지 않은 코드는 완료로 간주하지 않는다.

## 비밀정보 처리
- `.env`, `credentials.json`, `token.json`, 그 외 시크릿 파일은 절대 커밋하지 않는다 (`.gitignore`로 차단).
- 저장소에는 `backend/.env.example`만 커밋하고, 실제 값은 로컬 `backend/.env`에만 둔다.
- `evidenceLogs`, `executionLogs`, 에러 응답의 `detail` 등 어떤 로그/응답에도 API 키, 토큰, 자격증명, 개인정보를 남기지 않는다.
- Google Calendar 자격증명(`credentials.json`, `token.json`) 경로는 `.env`로만 주입한다.

## 구현 규칙
- smolagents API 시그니처를 추측하지 않는다. 설치된 버전의 inspect 결과와 공식 문서를 확인한다.
- 정책 로직(관리 시점 판정, 역방향 구간 계산, 슬롯 충돌 판정, confirm 시 재확인, recommendationReason 산정)은 순수 함수로 구현한다.
- 날짜와 시간은 ISO 8601 및 Asia/Seoul 기준이다.
- Vision 실패 시 캐시 또는 주기 기반 폴백을 사용하고, 그 사유를 응답의 `fallbackReason`에 남긴다.
- 모든 외부 호출(Vision, Calendar, LLM)에는 timeout과 예외 처리가 있어야 한다.
- 불필요한 리팩터링과 새 라이브러리 추가를 하지 않는다.
- 구현 후 변경 파일, 테스트 결과, 남은 위험을 요약한다.
