# 슬슬 API 계약

백엔드 API의 기준 문서다. 프론트엔드는 이 문서만 보고 바로 연동한다.
API 응답 필드를 변경할 때는 반드시 이 문서를 먼저 수정한 뒤 구현을 바꾼다. (CLAUDE.md 참고)

## 공통 규칙

- 모든 날짜/시간은 ISO 8601, Asia/Seoul(`+09:00`) 기준 문자열이다. 날짜만 필요한 곳은 `YYYY-MM-DD`.
- 모든 요청/응답은 JSON이다. `Content-Type: application/json`.
- 필드명은 camelCase, enum 값은 UPPER_SNAKE_CASE를 사용한다.
- 서비스는 단일 사용자, 단일 데모 앨범(사진 6장), 네일 한 가지만 다룬다.
- 데모 데이터는 `backend/data/`에 고정 JSON fixture로 저장되어 있다 (`album.json`, `calendar_events.json`, `nail_shops.json` 등).
- 데모 앨범 이미지는 `backend/demo-album`을 `/demo-album`으로 마운트한 StaticFiles가 제공한다. 모든 `imageUrl`은 완전한 URL이다(상대 경로 아님).
- 배열 필드는 값이 없을 때 `null`이 아니라 빈 배열(`[]`)이다.
- **Agent(smolagents ToolCallingAgent)는 `POST /api/analyses/{analysis_id}/schedule`와 `POST /api/analyses/{analysis_id}/retry`에서만 실행된다.**
  `POST /api/analyses`는 일반 결정론 코드만 실행하며 `candidates`/`executionLogs`를 반환하지 않는다.
- `POST /api/bookings/{candidate_id}/confirm`만 실제 Google Calendar 이벤트를 생성할 수 있다(`CALENDAR_MODE=LIVE`일 때). 다른 어떤 엔드포인트도, Agent도 이벤트를 생성하지 않는다.
  단, 이 저장소에는 아직 실제 Google Calendar OAuth 연동이 없어 `CALENDAR_MODE=LIVE`로 confirm을 호출하면 현재는 항상 `502 EXTERNAL_SERVICE_ERROR`로 실패하는 것이 정상이다(가짜 성공 이벤트를 만들지 않는다).
- `evidenceLogs`/`executionLogs`/`changeSignal.reason`/`careStatus.message` 등 모든 로그·설명 텍스트는 결정론 코드가 생성한 문자열이다. LLM이 생성한 자유 서술 문장은 어떤 필드에도 담기지 않는다.

## HTTP 상태 코드

| 상태 코드 | 의미 | 사용처 |
|---|---|---|
| 200 | OK | 모든 성공 응답 |
| 400 | Bad Request | `INVALID_REQUEST` |
| 404 | Not Found | `ALBUM_NOT_FOUND`, `ANALYSIS_NOT_FOUND`, `CANDIDATE_NOT_FOUND` |
| 409 | Conflict | `CALENDAR_CONFLICT`, `SCHEDULE_NOT_APPLICABLE`, `INVALID_CANDIDATE_STATUS` |
| 502 | Bad Gateway | `EXTERNAL_SERVICE_ERROR` |

## 공통 오류 응답

```json
{
  "code": "ERROR_CODE",
  "message": "사용자용 메시지",
  "detail": null
}
```

### 오류 코드

| 코드 | HTTP 상태 | 발생 가능 API | 설명 |
|---|---|---|---|
| `INVALID_REQUEST` | 400 | 전체 | 요청 바디 누락/형식 오류 |
| `ALBUM_NOT_FOUND` | 404 | `POST /api/albums/connect`, `POST /api/analyses` | `album.json`을 찾을 수 없거나 `albumId`가 일치하지 않음 |
| `ANALYSIS_NOT_FOUND` | 404 | `POST /api/analyses/{id}/schedule`, `POST /api/analyses/{id}/retry` | `analysis_id`가 존재하지 않음 |
| `SCHEDULE_NOT_APPLICABLE` | 409 | `POST /api/analyses/{id}/schedule`, `POST /api/analyses/{id}/retry` | 해당 analysis의 `canSchedule`이 `false`라 탐색할 필요가 없음 |
| `CANDIDATE_NOT_FOUND` | 404 | `POST /api/bookings/{id}/confirm` | `candidate_id`가 존재하지 않음. 이미 `CONFIRMED`인 candidate는 404가 아니라 200으로 기존 확정 결과를 그대로 반환한다(멱등) |
| `INVALID_CANDIDATE_STATUS` | 409 | `POST /api/bookings/{id}/confirm` | candidate 상태가 `PREPARED`도 `CONFIRMED`도 아닌 방어적 이상 상태 |
| `CALENDAR_CONFLICT` | 409 | `POST /api/bookings/{id}/confirm` | 승인 직전 재조회 결과 해당 시간대에 Calendar 충돌 발생. candidate는 `PREPARED`로 유지된다 |
| `EXTERNAL_SERVICE_ERROR` | 502 | `POST /api/analyses`, `POST /api/analyses/{id}/schedule`, `POST /api/analyses/{id}/retry`, `POST /api/bookings/{id}/confirm` | Vision/Calendar/LLM 등 외부 호출 실패. confirm에서는 `CALENDAR_MODE=LIVE` 이벤트 생성 실패 시 발생하며 candidate는 `PREPARED`로 유지된다(현재는 OAuth 미연동으로 항상 이 응답) |

---

## 공통 타입 (JavaScript / JSDoc)

```javascript
/**
 * @typedef {Object} Photo
 * @property {string} photoId
 * @property {string} imageUrl  - 예: "http://localhost:8000/demo-album/01.jpg"
 * @property {string} takenAt   - ISO 8601, Asia/Seoul
 * @property {string} label
 */

/**
 * @typedef {Object} SelectedPhoto
 * @property {string} photoId
 * @property {string} imageUrl
 * @property {string} takenAt
 * @property {string} label
 * @property {"BASELINE"|"RECENT"} role  - BASELINE 1장 + RECENT 3장
 */

/**
 * @typedef {Object} ChangeSignal
 * @property {"LOW"|"MEDIUM"|"HIGH"} level
 * @property {"STABLE"|"INCREASING"} trend
 * @property {"LOW"|"MEDIUM"|"HIGH"} confidence
 * @property {string} reason
 * @property {boolean} visionFailed
 */

/**
 * @typedef {Object} Timing
 * @property {number} cycleDays
 * @property {string} lastServiceDate   - ISO 8601 date
 * @property {number} daysSinceLastService
 * @property {string} nextDueDate       - ISO 8601 date
 */

/**
 * @typedef {Object} CareStatus
 * @property {"NOW"|"SOON"|"FRESH"} status
 * @property {boolean} needsCareNow
 * @property {string[]} reasonCodes   - 예: "CYCLE_DUE", "CYCLE_APPROACHING", "CHANGE_LEVEL_HIGH", "CHANGE_LEVEL_MEDIUM", "EVENT_WITHIN_WINDOW"
 * @property {string} message
 * @property {"POLICY"} decidedBy
 * @property {string} decidedAt
 */

/**
 * @typedef {Object} UpcomingEvent
 * @property {string} eventId
 * @property {string} title
 * @property {string} date       - ISO 8601 date
 * @property {"cached"|"live"} source
 */

/**
 * @typedef {Object} ReversePlan
 * @property {string} recommendedStart   - ISO 8601 date
 * @property {string} recommendedEnd     - ISO 8601 date
 * @property {"UPCOMING_EVENT"|"CARE_CYCLE"} basis
 * @property {string} reason
 */

/**
 * @typedef {Object} CalendarMarker
 * @property {string} date   - ISO 8601 date
 * @property {"CARE_NOW"|"RECOMMENDED"|"USER_EVENT"} type
 * @property {string} label
 */

/**
 * @typedef {Object} EvidenceLogEntry
 * @property {number} step
 * @property {"SYSTEM"|"VISION"|"POLICY"|"CALENDAR"} type
 * @property {string} message
 * @property {Object|null} result
 */

/**
 * @typedef {Object} ExecutionLogEntry
 * @property {number} step
 * @property {"TOOL"|"POLICY"|"AGENT"|"SYSTEM"} type
 * @property {"get_calendar_busy_times"|"search_nail_shop_slots"|"prepare_booking_candidates"|null} tool
 * @property {"THIS_WEEK"|"NEXT_WEEK"|null} searchScope
 * @property {Object|null} result
 * @property {string} message
 * @property {"CALENDAR_BUSY"|"OUTSIDE_SEARCH_SCOPE"|"SHOP_UNAVAILABLE"|null} exclusionReason
 */

/**
 * @typedef {Object} RecommendedWindow
 * @property {string} start   - ISO 8601 date
 * @property {string} end     - ISO 8601 date
 * @property {"UPCOMING_EVENT"|"CARE_CYCLE"} basis
 */

/**
 * @typedef {Object} ExcludedSlot
 * @property {string} slotId
 * @property {string} shop
 * @property {string} start
 * @property {string} end
 * @property {"CALENDAR_BUSY"|"OUTSIDE_SEARCH_SCOPE"|"SHOP_UNAVAILABLE"} exclusionReason
 */

/**
 * @typedef {Object} Candidate
 * @property {string} candidateId
 * @property {string} slotId
 * @property {string} shop
 * @property {string} artist
 * @property {string} service   - 예: "젤네일"
 * @property {number} price     - 정수, 원 단위
 * @property {string} start     - ISO 8601, Asia/Seoul
 * @property {string} end       - ISO 8601, Asia/Seoul
 * @property {"권장 관리 구간 내"|"Calendar 충돌 없음"|"선호 네일샵"|"빠른 예약 가능 시간"} recommendationReason
 * @property {"PREPARED"|"CONFIRMED"} status
 */

/**
 * @typedef {Object} ScanSummary
 * @property {number} totalPhotos
 * @property {{from: string, to: string}} coveragePeriod
 */

/**
 * @typedef {Object} ConnectAlbumRequest
 * @property {"DEMO"} source
 */

/**
 * @typedef {Object} ConnectAlbumResponse
 * @property {string} albumId
 * @property {"DEMO"} sourceMode
 * @property {string} connectedAt
 * @property {ScanSummary} scanSummary
 * @property {Photo[]} photos   - 정확히 6장, takenAt 오름차순
 */

/**
 * @typedef {Object} AnalyzeRequest
 * @property {string} albumId
 */

/**
 * @typedef {Object} AnalyzeResponse
 * @property {string} analysisId
 * @property {string} albumId
 * @property {SelectedPhoto[]} selectedPhotos
 * @property {ChangeSignal} changeSignal
 * @property {Timing} timing
 * @property {CareStatus} careStatus
 * @property {UpcomingEvent|null} upcomingEvent
 * @property {ReversePlan|null} reversePlan
 * @property {CalendarMarker[]} calendarMarkers
 * @property {EvidenceLogEntry[]} evidenceLogs
 * @property {boolean} canSchedule
 * @property {"LIVE"|"CACHED"} visionMode
 * @property {"LIVE"|"CACHED"} calendarMode
 * @property {string|null} fallbackReason
 */

/**
 * @typedef {Object} ScheduleResponse
 * @property {string} analysisId
 * @property {string} scheduleRunId
 * @property {RecommendedWindow} recommendedWindow
 * @property {ExcludedSlot[]} excludedSlots
 * @property {Candidate[]} candidates   - 최대 3개
 * @property {ExecutionLogEntry[]} executionLogs
 * @property {"LIVE"|"FALLBACK"|"CACHED"} agentMode
 * @property {"LIVE"|"CACHED"} calendarMode
 * @property {string|null} fallbackReason
 */

/**
 * @typedef {Object} RetryRequest
 * @property {"THIS_WEEK"|"NEXT_WEEK"} searchScope   - MVP에서는 "NEXT_WEEK"만 지원한다. 그 외 값(THIS_WEEK 포함)은 400 INVALID_REQUEST.
 */

/**
 * @typedef {Object} RetryResponse
 * @property {string} analysisId
 * @property {string} retryRunId
 * @property {"NEXT_WEEK"} searchScope
 * @property {RecommendedWindow} searchWindow   - 기존 reversePlan의 recommendedStart/recommendedEnd에 각각 7일을 더한 구간
 * @property {Candidate[]} candidates   - 최대 3개
 * @property {ExcludedSlot[]} excludedSlots
 * @property {ExecutionLogEntry[]} executionLogs
 * @property {"LIVE"|"FALLBACK"|"CACHED"} agentMode
 * @property {"LIVE"|"CACHED"} calendarMode
 * @property {string|null} fallbackReason
 */

/**
 * @typedef {Object} BusyTimeRange
 * @property {string} start
 * @property {string} end
 */

/**
 * @typedef {Object} RecheckResult
 * @property {boolean} conflict
 * @property {string} checkedAt
 * @property {BusyTimeRange[]} conflictingBusyTimes   - conflict=false면 항상 빈 배열
 */

/**
 * @typedef {Object} ShopBooking
 * @property {boolean} simulated          - 항상 true. 실제 네일샵 예약이 아니다
 * @property {string} confirmationCode    - 최초 성공 시 한 번만 생성되고, 이후 재확인/재호출에도 재사용된다
 */

/**
 * @typedef {Object} CalendarEventResult
 * @property {boolean} created
 * @property {boolean} simulated
 * @property {string|null} eventId
 * @property {string|null} htmlLink
 */

/**
 * @typedef {Object} ConfirmResponse
 * @property {string} candidateId
 * @property {string} slotId
 * @property {string} shop
 * @property {string} artist
 * @property {string} service
 * @property {number} price
 * @property {string} start
 * @property {string} end
 * @property {string} recommendationReason
 * @property {"CONFIRMED"} status
 * @property {string} confirmedAt
 * @property {"SIMULATED"} reservationMode   - 항상 "SIMULATED". 실제 미용실 예약이 아니다
 * @property {RecheckResult} recheck
 * @property {ShopBooking} shopBooking
 * @property {CalendarEventResult} calendarEvent
 * @property {"LIVE"|"CACHED"} calendarMode
 */

/**
 * @typedef {Object} ApiError
 * @property {string} code
 * @property {string} message
 * @property {Object|null} detail
 */
```

---

## 1. GET /api/health

### Request
없음.

### Response 200

```json
{
  "status": "ok",
  "visionMode": "CACHED",
  "agentMode": "LIVE",
  "calendarMode": "CACHED",
  "serverTime": "2026-08-01T09:00:00+09:00"
}
```

---

## 2. POST /api/albums/connect

`backend/data/album.json`을 로드하고 `takenAt` 오름차순으로 정렬해 반환한다.
사진 파일은 `backend/demo-album`을 `/demo-album`으로 마운트한 StaticFiles가 제공하며,
`imageUrl`은 항상 완전한 URL(scheme+host+port 포함)이다.

### Request

```json
{ "source": "DEMO" }
```

### Response 200

```json
{
  "albumId": "album-001",
  "sourceMode": "DEMO",
  "connectedAt": "2026-08-01T09:00:00+09:00",
  "scanSummary": {
    "totalPhotos": 6,
    "coveragePeriod": { "from": "2026-06-10", "to": "2026-08-01" }
  },
  "photos": [
    { "photoId": "photo_01", "imageUrl": "http://localhost:8000/demo-album/01.jpg", "takenAt": "2026-06-10T09:00:00+09:00", "label": "1회차" },
    { "photoId": "photo_02", "imageUrl": "http://localhost:8000/demo-album/02.jpg", "takenAt": "2026-06-17T09:00:00+09:00", "label": "2회차" },
    { "photoId": "photo_03", "imageUrl": "http://localhost:8000/demo-album/03.jpg", "takenAt": "2026-06-24T09:00:00+09:00", "label": "3회차" },
    { "photoId": "photo_04", "imageUrl": "http://localhost:8000/demo-album/04.jpg", "takenAt": "2026-07-01T09:00:00+09:00", "label": "4회차" },
    { "photoId": "photo_05", "imageUrl": "http://localhost:8000/demo-album/05.jpg", "takenAt": "2026-07-11T09:00:00+09:00", "label": "5회차" },
    { "photoId": "photo_06", "imageUrl": "http://localhost:8000/demo-album/06.jpg", "takenAt": "2026-08-01T09:00:00+09:00", "label": "6회차(오늘)" }
  ]
}
```

`scanSummary`는 실시간 사진첩 스캔이 아니라 **고정 데모 앨범의 사실 정보 요약**이다("온디바이스 스캔" 같은 문구의 근거로 쓰지 않는다).

### Response 400

```json
{ "code": "INVALID_REQUEST", "message": "source는 'DEMO'만 지원합니다.", "detail": null }
```

### Response 404

```json
{ "code": "ALBUM_NOT_FOUND", "message": "데모 앨범 데이터를 찾을 수 없습니다.", "detail": null }
```

---

## 3. POST /api/analyses

**일반 코드만 실행한다. Agent를 실행하지 않고 `candidates`를 만들지 않는다.**

1. 기준 사진 1장(가장 오래된 사진) + 최근 사진 3장을 자동 선택한다.
2. Vision 변화 신호를 가져온다(`VISION_MODE`에 따라 캐시/라이브). 실패 시 캐시 폴백 + `fallbackReason`.
3. 개인 시술 주기를 계산한다(`timing`).
4. 관리 시점을 순수 함수로 판정한다(`careStatus`).
5. `CalendarClient.get_important_events()`로 중요 일정을 조회한다(`upcomingEvent`, Agent Tool 아님. CACHED는 `calendar_events.json`, LIVE는 Google `events.list`, LIVE 실패 시 CACHED로 자동 fallback).
6. 네일 역방향 권장 구간을 계산한다(`reversePlan`, 이벤트가 있으면 이벤트 기준, 없으면 관리주기 기준).
7. `calendarMarkers`, `evidenceLogs`, `canSchedule`을 구성한다.

### Request

```json
{ "albumId": "album-001" }
```

### Response 200

```json
{
  "analysisId": "analysis_20260801_090000",
  "albumId": "album-001",
  "selectedPhotos": [
    { "photoId": "photo_01", "imageUrl": "http://localhost:8000/demo-album/01.jpg", "takenAt": "2026-06-10T09:00:00+09:00", "label": "1회차", "role": "BASELINE" },
    { "photoId": "photo_04", "imageUrl": "http://localhost:8000/demo-album/04.jpg", "takenAt": "2026-07-01T09:00:00+09:00", "label": "4회차", "role": "RECENT" },
    { "photoId": "photo_05", "imageUrl": "http://localhost:8000/demo-album/05.jpg", "takenAt": "2026-07-11T09:00:00+09:00", "label": "5회차", "role": "RECENT" },
    { "photoId": "photo_06", "imageUrl": "http://localhost:8000/demo-album/06.jpg", "takenAt": "2026-08-01T09:00:00+09:00", "label": "6회차(오늘)", "role": "RECENT" }
  ],
  "changeSignal": {
    "level": "MEDIUM",
    "trend": "INCREASING",
    "confidence": "HIGH",
    "reason": "직전 사진 대비 큐티클 라인 부근 변화가 뚜렷합니다.",
    "visionFailed": false
  },
  "timing": {
    "cycleDays": 21,
    "lastServiceDate": "2026-07-11",
    "daysSinceLastService": 21,
    "nextDueDate": "2026-08-01"
  },
  "careStatus": {
    "status": "NOW",
    "needsCareNow": true,
    "reasonCodes": ["CYCLE_DUE", "CHANGE_LEVEL_MEDIUM"],
    "message": "관리 주기(21일)에 도달했고 변화 수준이 MEDIUM 이상입니다.",
    "decidedBy": "POLICY",
    "decidedAt": "2026-08-01T09:00:02+09:00"
  },
  "upcomingEvent": {
    "eventId": "evt_wedding_001",
    "title": "친구 결혼식",
    "date": "2026-08-15",
    "source": "cached"
  },
  "reversePlan": {
    "recommendedStart": "2026-08-12",
    "recommendedEnd": "2026-08-13",
    "basis": "UPCOMING_EVENT",
    "reason": "8/15 결혼식 기준 시술 2~3일 후가 가장 좋은 상태입니다."
  },
  "calendarMarkers": [
    { "date": "2026-08-01", "type": "CARE_NOW", "label": "지금 관리" },
    { "date": "2026-08-12", "type": "RECOMMENDED", "label": "네일 권장" },
    { "date": "2026-08-13", "type": "RECOMMENDED", "label": "네일 권장" },
    { "date": "2026-08-15", "type": "USER_EVENT", "label": "친구 결혼식" }
  ],
  "evidenceLogs": [
    { "step": 1, "type": "SYSTEM", "message": "기준 사진 1장 + 최근 사진 3장을 선택했습니다.", "result": { "baselinePhotoId": "photo_01", "recentPhotoIds": ["photo_04", "photo_05", "photo_06"] } },
    { "step": 2, "type": "VISION", "message": "Vision 변화 신호를 계산했습니다.", "result": { "level": "MEDIUM", "trend": "INCREASING" } },
    { "step": 3, "type": "POLICY", "message": "개인 시술 주기를 계산했습니다.", "result": { "cycleDays": 21, "daysSinceLastService": 21 } },
    { "step": 4, "type": "POLICY", "message": "관리 시점을 판정했습니다.", "result": { "status": "NOW", "needsCareNow": true } },
    { "step": 5, "type": "CALENDAR", "message": "중요 일정을 조회했습니다.", "result": { "found": true, "eventId": "evt_wedding_001" } },
    { "step": 6, "type": "POLICY", "message": "역방향 권장 구간을 계산했습니다.", "result": { "recommendedStart": "2026-08-12", "recommendedEnd": "2026-08-13" } }
  ],
  "canSchedule": true,
  "visionMode": "CACHED",
  "calendarMode": "CACHED",
  "fallbackReason": null
}
```

### Response 200 (관리 시점도 아니고 임박 일정도 없는 경우)

`careStatus.status`가 `"FRESH"`, `upcomingEvent`/`reversePlan`이 `null`, `canSchedule`이 `false`인 것을 제외하면 형태는 동일하다.

### Response 400

```json
{ "code": "INVALID_REQUEST", "message": "albumId가 필요합니다.", "detail": null }
```

### Response 404

```json
{ "code": "ALBUM_NOT_FOUND", "message": "albumId 'album-999'를 찾을 수 없습니다.", "detail": null }
```

### Response 502

```json
{ "code": "EXTERNAL_SERVICE_ERROR", "message": "Vision 분석 중 오류가 발생했습니다.", "detail": null }
```

---

## 4. POST /api/analyses/{analysis_id}/schedule

**Agent(smolagents ToolCallingAgent)가 유일하게 실행되는 엔드포인트.**
analysis의 `reversePlan`을 `recommendedWindow`로 사용해 Agent가 다음 순서로 Tool을 호출한다.

1. `get_calendar_busy_times(recommendedWindow)`
2. `search_nail_shop_slots(FAVORITE_SHOP)`
3. 후보가 3개 미만이면 `search_nail_shop_slots(ALTERNATIVE_SHOPS)` 추가 호출
4. `prepare_booking_candidates` → 최대 3개 후보(가격/추천사유 포함) 반환

`canSchedule`이 `false`인 analysis에는 호출할 수 없다(`SCHEDULE_NOT_APPLICABLE`).

### Path Parameters

| 이름 | 타입 | 설명 |
|---|---|---|
| `analysis_id` | string | `POST /api/analyses`가 반환한 `analysisId` |

### Request

```json
{}
```

### Response 200

```json
{
  "analysisId": "analysis_20260801_090000",
  "scheduleRunId": "schedule_20260801_090500",
  "recommendedWindow": { "start": "2026-08-12", "end": "2026-08-13", "basis": "UPCOMING_EVENT" },
  "excludedSlots": [
    { "slotId": "slot_1201", "shop": "슬슬네일 강남점", "start": "2026-08-12T10:00:00+09:00", "end": "2026-08-12T11:00:00+09:00", "exclusionReason": "CALENDAR_BUSY" }
  ],
  "candidates": [
    { "candidateId": "cand_01", "slotId": "slot_1203", "shop": "슬슬네일 강남점", "artist": "김아라", "service": "젤네일", "price": 45000, "start": "2026-08-12T14:00:00+09:00", "end": "2026-08-12T15:00:00+09:00", "recommendationReason": "선호 네일샵", "status": "PREPARED" },
    { "candidateId": "cand_02", "slotId": "slot_1204", "shop": "슬슬네일 강남점", "artist": "이나연", "service": "젤네일", "price": 42000, "start": "2026-08-13T11:00:00+09:00", "end": "2026-08-13T12:00:00+09:00", "recommendationReason": "권장 관리 구간 내", "status": "PREPARED" }
  ],
  "executionLogs": [
    { "step": 1, "type": "SYSTEM", "tool": null, "searchScope": null, "result": null, "message": "예약 후보 탐색을 시작합니다.", "exclusionReason": null },
    { "step": 2, "type": "TOOL", "tool": "get_calendar_busy_times", "searchScope": null, "result": { "busyTimes": [ { "start": "2026-08-12T09:00:00+09:00", "end": "2026-08-12T12:00:00+09:00" } ] }, "message": "캘린더 바쁜 시간 조회 완료", "exclusionReason": null },
    { "step": 3, "type": "TOOL", "tool": "search_nail_shop_slots", "searchScope": null, "result": { "shopScope": "FAVORITE_SHOP", "slots": [ { "slotId": "slot_1201" }, { "slotId": "slot_1203" } ] }, "message": "선호 네일샵 슬롯 2건 조회", "exclusionReason": null },
    { "step": 4, "type": "AGENT", "tool": null, "searchScope": null, "result": { "candidateCountSoFar": 1 }, "message": "선호샵 후보가 3개 미만이라 대체샵을 추가 조회합니다.", "exclusionReason": null },
    { "step": 5, "type": "TOOL", "tool": "search_nail_shop_slots", "searchScope": null, "result": { "shopScope": "ALTERNATIVE_SHOPS", "slots": [ { "slotId": "slot_1204" } ] }, "message": "대체 네일샵 슬롯 1건 조회", "exclusionReason": null },
    { "step": 6, "type": "TOOL", "tool": "prepare_booking_candidates", "searchScope": null, "result": { "candidateIds": ["cand_01", "cand_02"], "excludedSlotIds": ["slot_1201"] }, "message": "슬롯 1건 제외(캘린더 충돌), 예약 후보 2건 준비 완료", "exclusionReason": "CALENDAR_BUSY" }
  ],
  "agentMode": "LIVE",
  "calendarMode": "CACHED",
  "fallbackReason": null
}
```

### Response 404

```json
{ "code": "ANALYSIS_NOT_FOUND", "message": "analysis_id 'analysis_xxx'를 찾을 수 없습니다.", "detail": null }
```

### Response 409

```json
{ "code": "SCHEDULE_NOT_APPLICABLE", "message": "관리 시점이 아니고 임박한 일정도 없어 예약 후보를 탐색할 필요가 없습니다.", "detail": null }
```

### Response 502

```json
{ "code": "EXTERNAL_SERVICE_ERROR", "message": "예약 후보 탐색 중 오류가 발생했습니다.", "detail": null }
```

---

## 5. POST /api/analyses/{analysis_id}/retry

**MVP에서는 `searchScope`로 `"NEXT_WEEK"`만 지원한다.** `"THIS_WEEK"`를 포함한 다른 값은 400 `INVALID_REQUEST`다.

기존 analysis의 결과(`selectedPhotos`, `changeSignal`, `timing`, `careStatus`, `upcomingEvent`, `reversePlan`)는
**재계산하지 않고 그대로 재사용한다** — Vision을 다시 호출하지 않고, 사진을 다시 선택하지 않고, 시술 주기·`careStatus`를
다시 계산하지 않고, `upcomingEvent`/`reversePlan`도 다시 계산하지 않는다. 새로 하는 일은 오직 `searchWindow`(다음 주 구간)에서
`/schedule`과 동일한 Agent runner·Tool 3개(`get_calendar_busy_times` → `search_nail_shop_slots(FAVORITE_SHOP)` →
부족하면 `search_nail_shop_slots(ALTERNATIVE_SHOPS)` → `prepare_booking_candidates`)를 새 `AgentRunContext`로 다시
실행해 `candidates`를 새로 탐색하는 것뿐이다.

`searchWindow`는 기존 `reversePlan.recommendedStart`/`recommendedEnd`에 각각 7일을 더해 결정론적으로 계산한다(순수 함수).
예: `reversePlan`이 `2026-08-12`~`2026-08-13`이면 `searchWindow`는 `2026-08-19`~`2026-08-20`이다.

이번 호출로 준비된 `candidates`는 이전 `/schedule`(또는 이전 `/retry`) 호출의 후보를 삭제·덮어쓰지 않는다 —
`candidateId`는 `{retryRunId}_{slotId}` 형태라 다른 실행의 `candidateId`와 겹치지 않는다.

`canSchedule`이 `false`인 analysis에는 호출할 수 없다(`SCHEDULE_NOT_APPLICABLE`), `/schedule`과 동일하다.

### Path Parameters

| 이름 | 타입 | 설명 |
|---|---|---|
| `analysis_id` | string | `POST /api/analyses`가 반환한 `analysisId` |

### Request

```json
{ "searchScope": "NEXT_WEEK" }
```

### Response 200

`RetryResponse` 형태(`ScheduleResponse`와 필드명이 다르다 — `scheduleRunId`/`recommendedWindow` 대신 `retryRunId`/`searchWindow`를 쓴다).

```json
{
  "analysisId": "analysis_20260810094100_3395",
  "retryRunId": "retry_20260810094100_1872",
  "searchScope": "NEXT_WEEK",
  "searchWindow": { "start": "2026-08-19", "end": "2026-08-20", "basis": "UPCOMING_EVENT" },
  "candidates": [
    { "candidateId": "retry_20260810094100_1872_slot_2006", "slotId": "slot_2006", "shop": "무드네일 합정", "artist": "박서현", "service": "GEL_NAIL", "price": 42000, "start": "2026-08-19T14:00:00+09:00", "end": "2026-08-19T15:30:00+09:00", "recommendationReason": "빠른 예약 가능 시간", "status": "PREPARED" },
    { "candidateId": "retry_20260810094100_1872_slot_2005", "slotId": "slot_2005", "shop": "프리즘네일 홍대", "artist": "이나연", "service": "GEL_NAIL", "price": 45000, "start": "2026-08-19T19:00:00+09:00", "end": "2026-08-19T20:30:00+09:00", "recommendationReason": "선호 네일샵", "status": "PREPARED" },
    { "candidateId": "retry_20260810094100_1872_slot_2007", "slotId": "slot_2007", "shop": "무드네일 합정", "artist": "이나연", "service": "GEL_NAIL", "price": 42000, "start": "2026-08-20T20:00:00+09:00", "end": "2026-08-20T21:30:00+09:00", "recommendationReason": "권장 관리 구간 내", "status": "PREPARED" }
  ],
  "excludedSlots": [
    { "slotId": "slot_2001", "shop": "프리즘네일 홍대", "start": "2026-08-12T19:00:00+09:00", "end": "2026-08-12T20:30:00+09:00", "exclusionReason": "OUTSIDE_SEARCH_SCOPE" }
  ],
  "executionLogs": [
    { "step": 1, "type": "SYSTEM", "tool": null, "searchScope": null, "result": { "searchStart": "2026-08-19T00:00:00+09:00", "searchEnd": "2026-08-20T23:59:59+09:00" }, "message": "다음 주(NEXT_WEEK) 예약 후보 재탐색을 시작합니다.", "exclusionReason": null },
    { "step": 2, "type": "TOOL", "tool": "get_calendar_busy_times", "searchScope": null, "result": { "busyCount": 1 }, "message": "캘린더 바쁜 시간 조회 완료", "exclusionReason": null },
    { "step": 3, "type": "TOOL", "tool": "search_nail_shop_slots", "searchScope": null, "result": { "searchScope": "FAVORITE_SHOP", "eligibleSlotIds": ["slot_2005"] }, "message": "FAVORITE_SHOP 슬롯 조회 완료", "exclusionReason": null },
    { "step": 4, "type": "AGENT", "tool": null, "searchScope": null, "result": { "totalEligibleCount": 1 }, "message": "누적 후보가 1개로 목표(3개) 미만이라 추가 검색이 필요합니다.", "exclusionReason": null },
    { "step": 5, "type": "TOOL", "tool": "search_nail_shop_slots", "searchScope": null, "result": { "searchScope": "ALTERNATIVE_SHOPS", "eligibleSlotIds": ["slot_2006", "slot_2007"] }, "message": "ALTERNATIVE_SHOPS 슬롯 조회 완료", "exclusionReason": null },
    { "step": 6, "type": "TOOL", "tool": "prepare_booking_candidates", "searchScope": null, "result": { "preparedCandidateCount": 3 }, "message": "예약 후보 3건 준비 완료", "exclusionReason": null }
  ],
  "agentMode": "LIVE",
  "calendarMode": "CACHED",
  "fallbackReason": null
}
```

`agentMode`/`fallbackReason`은 `/schedule`과 동일한 규칙을 따른다 — Agent가 실패하면 결정론 fallback으로 대체되고
(`agentMode: "FALLBACK"`), Agent가 후보 준비 이후 마무리 단계에서만 실패하면 이미 준비된 후보를 그대로 쓰면서
`fallbackReason: "AGENT_FINALIZATION_FAILED_AFTER_PREPARE"`를 반환한다.

`calendarMode`는 이번 조회에 **실제로 사용한** 모드를 정직하게 반환한다 — 실제 Google Calendar LIVE 조회(바쁜 시간 조회)는
아직 구현되지 않아(`create_event`만 LIVE 시도) `CALENDAR_MODE=LIVE`로 설정해도 실제로는 CACHED fixture를 사용하며, 이 경우
`calendarMode: "CACHED"`와 `fallbackReason: "CALENDAR_LIVE_LOOKUP_NOT_IMPLEMENTED"`를 반환한다(`AGENT_MODE`와는 독립적).
`fallbackReason`에 Agent 관련 사유와 동시에 해당하면 `"; "`로 이어붙여 반환한다.

### Response 400

```json
{ "code": "INVALID_REQUEST", "message": "MVP에서는 searchScope로 'NEXT_WEEK'만 지원합니다.", "detail": null }
```

### Response 404 / 409 / 502

`/schedule`과 동일 (`ANALYSIS_NOT_FOUND`, `SCHEDULE_NOT_APPLICABLE`, `EXTERNAL_SERVICE_ERROR`).

---

## 6. POST /api/bookings/{candidate_id}/confirm

1. **조회**: `candidate_id`로 인메모리 candidate를 조회한다. 없으면 404 `CANDIDATE_NOT_FOUND`.
2. **멱등 처리**: `status`가 이미 `CONFIRMED`면 재확인·예약 시뮬레이션·Calendar 이벤트 생성을 다시 하지 않고, 최초 확정 시 저장해둔 응답을 그대로 반환한다(`confirmationCode`/`eventId`도 새로 만들지 않는다).
3. `status`가 `PREPARED`도 `CONFIRMED`도 아니면(방어적 이상 상태) 409 `INVALID_CANDIDATE_STATUS`.
4. **재확인**: 결정론 코드로 Google Calendar 바쁜 시간을 다시 조회해, 슬롯 검색 때 쓰는 것과 동일한 순수 충돌 판정 함수로 겹침을 확인한다(Agent 아님).
5. 새 충돌이 있으면 409 `CALENDAR_CONFLICT` — candidate는 `PREPARED`로 유지되고 Calendar 이벤트 생성 함수는 호출되지 않는다.
6. 충돌이 없으면 네일샵 예약 성공을 시뮬레이션한다(`shopBooking`, 항상 시뮬레이션. `confirmationCode`는 이 시점에 한 번만 생성).
7. `CALENDAR_MODE`에 따라 Calendar 이벤트를 처리한다:
   - `CACHED`: 실제 이벤트를 생성하지 않는다.
   - `LIVE`: 실제 Google Calendar 이벤트 생성을 시도한다. **생성에 성공했을 때만** candidate를 `CONFIRMED`로 바꾼다. 실패하면 502 `EXTERNAL_SERVICE_ERROR`이며 candidate는 `PREPARED`로 유지된다.
     이 저장소에는 아직 실제 Google Calendar OAuth 연동이 없다 — 따라서 **지금은 `CALENDAR_MODE=LIVE`로 confirm을 호출하면 항상 502가 정상 동작**이다(가짜 성공 이벤트를 만들지 않는다).
8. 모든 필수 처리가 성공한 뒤에만 candidate를 `CONFIRMED`로 바꾸고 `confirmedAt`과 확정 결과를 저장한다.

### Path Parameters

| 이름 | 타입 | 설명 |
|---|---|---|
| `candidate_id` | string | `candidates[].candidateId` |

### Request

```json
{}
```

### Response 200 — `CALENDAR_MODE=CACHED` (최초 확정)

```json
{
  "candidateId": "schedule_20260810094100_8077_slot_2001",
  "slotId": "slot_2001",
  "shop": "프리즘네일 홍대",
  "artist": "김아라",
  "service": "GEL_NAIL",
  "price": 45000,
  "start": "2026-08-12T19:00:00+09:00",
  "end": "2026-08-12T20:30:00+09:00",
  "recommendationReason": "선호 네일샵",
  "status": "CONFIRMED",
  "confirmedAt": "2026-08-10T09:41:00+09:00",
  "reservationMode": "SIMULATED",
  "recheck": {
    "conflict": false,
    "checkedAt": "2026-08-10T09:41:00+09:00",
    "conflictingBusyTimes": []
  },
  "shopBooking": {
    "simulated": true,
    "confirmationCode": "SIM-20260810094100-t_2001"
  },
  "calendarEvent": {
    "created": false,
    "simulated": true,
    "eventId": null,
    "htmlLink": null
  },
  "calendarMode": "CACHED"
}
```

`reservationMode`는 항상 `"SIMULATED"`다(미용실 예약은 항상 시뮬레이션). `CALENDAR_MODE=CACHED`에서는 `calendarEvent.created`가 항상 `false`, `eventId`/`htmlLink`가 항상 `null`이다 — 프론트가 "Google Calendar에 실제 추가됨"으로 오해할 값을 반환하지 않는다.

### Response 200 — 동일 candidate 중복 confirm (멱등)

같은 `candidate_id`로 다시 호출해도 최초 확정 시 응답과 완전히 동일한 JSON이 그대로 반환된다(`confirmedAt`, `shopBooking.confirmationCode`, `calendarEvent` 전부 동일). 이벤트를 다시 생성하거나 `confirmationCode`를 다시 만들지 않는다.

### Response 200 — `CALENDAR_MODE=LIVE` (실제 연동 완료 이후, 성공 시)

`calendarEvent`만 다음과 같이 달라진다. **실제 OAuth 연동이 아직 없어 이 응답은 향후 구현 이후에만 발생한다** — 지금은 아래 502 응답이 정상이다.

```json
{ "created": true, "simulated": false, "eventId": "g_evt_abc123", "htmlLink": "https://calendar.google.com/event?eid=..." }
```

### Response 404

```json
{ "code": "CANDIDATE_NOT_FOUND", "message": "candidate_id 'cand_xx'를 찾을 수 없거나 더 이상 승인할 수 없습니다.", "detail": null }
```

### Response 409 — `INVALID_CANDIDATE_STATUS`

```json
{ "code": "INVALID_CANDIDATE_STATUS", "message": "candidate_id 'cand_xx'의 상태가 올바르지 않습니다: SOMETHING_ELSE", "detail": null }
```

### Response 409 — `CALENDAR_CONFLICT`

```json
{
  "code": "CALENDAR_CONFLICT",
  "message": "재확인 결과 해당 시간대에 캘린더 일정이 생겨 예약할 수 없습니다.",
  "detail": {
    "conflict": true,
    "checkedAt": "2026-08-10T09:41:00+09:00",
    "conflictingBusyTimes": [
      { "start": "2026-08-12T19:15:00+09:00", "end": "2026-08-12T19:45:00+09:00" }
    ]
  }
}
```

candidate는 `PREPARED` 상태로 유지되며, Calendar 이벤트 생성 함수는 호출되지 않는다.

### Response 502 — `CALENDAR_MODE=LIVE` 이벤트 생성 실패 (현재는 항상 이 응답)

```json
{ "code": "EXTERNAL_SERVICE_ERROR", "message": "Google Calendar 이벤트 생성에 실패했습니다: ...", "detail": null }
```

candidate는 `PREPARED` 상태로 유지된다(`CONFIRMED`로 바뀌지 않는다).

---

## 엔드포인트 요약

| Method | Path | Agent 실행 | 목적 |
|---|---|---|---|
| GET | `/api/health` | ✕ | 서버/모드 상태 확인 |
| POST | `/api/albums/connect` | ✕ | 데모 앨범 연결 + 정렬된 사진 + scanSummary |
| POST | `/api/analyses` | ✕ | 사진선택·Vision·주기·관리시점판정·중요일정조회·역방향구간계산 |
| POST | `/api/analyses/{id}/schedule` | ✅ | Agent가 3-Tool로 예약 후보(최대 3개) 탐색 |
| POST | `/api/analyses/{id}/retry` | ✅ | 동일 Agent 플로우를 NEXT_WEEK 구간(+7일)에서 재실행(MVP: searchScope는 NEXT_WEEK만 지원) |
| POST | `/api/bookings/{id}/confirm` | ✕ | 재확인 + 예약 시뮬레이션 + (LIVE만) 캘린더 생성 |
