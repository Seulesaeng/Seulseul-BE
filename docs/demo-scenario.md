# 슬슬 데모 시나리오

12시간 해커톤 심사용 데모 스크립트다. 모든 단계는 docs/api-contract.md의 6개 API로만 구성된다.
사용자 승인(`/confirm`) 전에는 어떤 실제 실행도 일어나지 않는다는 점을 데모 중 명시적으로 보여준다.

## 데모 전제

- 환경 모드: `VISION_MODE=CACHED`, `AGENT_MODE=LIVE`, `CALENDAR_MODE=CACHED`
  - Vision과 Calendar는 캐시된 데모 데이터로 결정론적으로 동작해 데모 중 실패 위험을 줄인다.
  - Agent(smolagents ToolCallingAgent)는 `POST /api/analyses/{id}/schedule`에서만 실제로 동작한다.
    `POST /api/analyses`는 절대 Agent를 호출하지 않는다 — 이 경계를 데모 중 강조 포인트로 사용한다.
- 데이터: `backend/data/album.json`의 `albumId = "album-001"`, 사진 6장 (2026-06-10 ~ 2026-08-01, 1주 간격).
- 사용자: 단일 데모 사용자, 네일 관리 주기(`cycleDays`) 21일, 마지막 시술일 2026-07-11 고정
  → 오늘(2026-08-01) 기준 `daysSinceLastService = 21`로 관리 시점(`careStatus.status = "NOW"`) 조건을 자연스럽게 충족.
- **필수 시나리오**: `backend/data/calendar_events.json`에 2026-08-15 "친구 결혼식" 이벤트를 고정 등록해
  `upcomingEvent`/`reversePlan`(역방향 스케줄) 경로가 항상 데모에서 동작하도록 한다. 이 기능은 포기 대상이 아니다.

## 시나리오 흐름

### 1단계 — 앨범 연결
- 사용자 액션: "데모 앨범 연결" 버튼 클릭
- API: `POST /api/albums/connect` `{ "source": "DEMO" }`
- 화면: 사진 6장이 촬영일 오름차순으로 표시됨. `scanSummary`는 고정 앨범의 사실 요약일 뿐 실시간 스캔이 아님을 자막으로 명시

### 2단계 — 분석 실행 (Agent 없음)
- 사용자 액션: "관리 시점 확인하기" 버튼 클릭
- API: `POST /api/analyses` `{ "albumId": "album-001" }`
- 서버 동작: 사진 자동선택 → Vision(cached, `changeSignal`) → 시술 주기(`timing`) → 관리 시점 판정(`careStatus`) → 중요 일정 조회(`upcomingEvent`, 결혼식) → 역방향 권장구간 계산(`reversePlan`) → `calendarMarkers`/`evidenceLogs`/`canSchedule`
- 화면:
  - `changeSignal.level`/`trend`로 "변화가 감지됐어요" 카드 표시 (mm/% 수치 없음)
  - `careStatus.status: "NOW"` 배지 + `careStatus.message` 표시
  - `upcomingEvent` + `reversePlan`으로 "8/15 결혼식 → 8/12~13 권장" 카드 표시
  - `calendarMarkers`로 주간 캘린더 스트립 렌더링
  - `evidenceLogs`를 단계별 타임라인으로 표시 (이 단계에는 Tool 호출이 전혀 없음을 강조 — "Agent가 아직 실행되지 않았습니다")
- 강조 포인트: 이 화면까지는 Agent가 단 한 번도 실행되지 않았다는 것을 `evidenceLogs`의 `type`이 `SYSTEM`/`VISION`/`POLICY`/`CALENDAR`뿐이라는 점으로 증명한다.

### 3단계 — 예약 후보 탐색 (Agent 최초 실행)
- 사용자 액션: `canSchedule === true`이므로 노출되는 "예약 후보 찾기" 버튼 클릭
- API: `POST /api/analyses/{analysisId}/schedule`
- 서버 동작: `reversePlan`(8/12~13)을 `recommendedWindow`로 Agent가 `get_calendar_busy_times` → `search_nail_shop_slots(FAVORITE_SHOP)` → (부족하면) `search_nail_shop_slots(ALTERNATIVE_SHOPS)` → `prepare_booking_candidates` 순으로 실행
- 화면: `candidates`(최대 3개)를 카드로 표시(샵/아티스트/서비스/가격/시간/`recommendationReason`), `executionLogs`를 실시간 로그처럼 표시
- 강조 포인트: 여기서부터만 `executionLogs`에 `type: TOOL`이 등장한다 — "지금부터 Agent가 실행됩니다"를 2단계와 대비해서 보여준다.

### 4단계 — 다른 시간 보기 (선택)
- 사용자 액션: 후보가 마음에 들지 않으면 "다음 주 시간 보기" 클릭
- API: `POST /api/analyses/{analysisId}/retry` `{ "searchScope": "NEXT_WEEK" }`
- 서버 동작: 동일 Agent 플로우를 `NEXT_WEEK` 창으로 재실행
- 화면: 새 `candidates`로 카드 목록 갱신

### 5단계 — 예약 승인
- 사용자 액션: 후보 카드에서 "예약 확정" 클릭 → 승인 확인 모달(승인 전 실제 실행 없음을 재고지) → 확인
- API: `POST /api/bookings/{candidateId}/confirm` `{}`
- 서버 동작: 후보 상태 확인 → Calendar 재조회(재확인) → 충돌 없음 → 네일샵 예약 시뮬레이션 성공 → `CALENDAR_MODE=CACHED`이므로 `calendarEvent: { created:false, simulated:true, eventId:null, htmlLink:null }`
- 화면: "예약이 확정되었습니다" + 재확인 결과(`recheck.conflict:false`)
- 강조 포인트: `shopBooking.simulated:true`로 실제 미용실 예약이 아님을, `calendarEvent.created:false`로 CACHED 모드에서는 실제 캘린더 이벤트가 생성되지 않았음을 명확히 보여준다. (`CALENDAR_MODE=LIVE`로 전환하면 `created:true`+실제 `htmlLink`가 채워지는 것을 별도로 시연 가능)

## 대안 시나리오 (여유 시간이 있을 때)

| 시나리오 | 트리거 방법 | 보여줄 것 |
|---|---|---|
| 관리 시점도 아니고 임박 일정도 없음 | 시술 주기/이벤트 fixture를 조정한 대체 데이터로 `/api/analyses` 호출 | `careStatus.status:"FRESH"`, `upcomingEvent:null`, `reversePlan:null`, `canSchedule:false` → `/schedule` 호출 시 `409 SCHEDULE_NOT_APPLICABLE` |
| Vision 폴백 | `VISION_MODE=LIVE`로 전환 후 실패 유도 | `changeSignal.visionFailed:true`, `fallbackReason` 값 확인 |
| 선호샵 부족 → 대체샵 확장 | FAVORITE_SHOP 슬롯을 1개만 남긴 fixture로 `/schedule` 호출 | `executionLogs`에 `search_nail_shop_slots`가 `FAVORITE_SHOP`→`ALTERNATIVE_SHOPS` 순으로 2번 호출된 기록 |
| 캘린더 충돌 | confirm 재확인용 캐시 데이터에 충돌 슬롯을 미리 심어둔 후보로 confirm 시도 | `409 CALENDAR_CONFLICT`, "다른 시간을 찾아보세요" 안내 |
| Calendar LIVE 성공 | `CALENDAR_MODE=LIVE`로 전환 후 confirm | `calendarEvent.created:true`, 실제 `htmlLink` |

## 데모 시 주의사항

- Agent는 `/schedule`, `/retry`에서만 실행된다 — `/analyses`의 `evidenceLogs`와 `/schedule`의 `executionLogs`를 나란히 보여주며 이 경계를 명확히 짚는다.
- 모든 예약/캘린더 동작은 사용자 승인(`/confirm`) 이후에만 실행된다.
- 미용실 예약은 항상 시뮬레이션이다. 실제 결제·매장 반영이 없다는 점을 화면 문구로 고지한다.
- Vision 결과는 mm/% 같은 정밀 수치가 아니라 `level`/`trend`/`confidence` 범주형 값이다 — 데모 문구도 이에 맞춘다.
- 데모 중 네트워크 문제 대비: `VISION_MODE=CACHED`, `CALENDAR_MODE=CACHED`가 기본값이므로 인터넷 연결 없이도 1~5단계가 동작해야 한다.
