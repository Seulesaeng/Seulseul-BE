# prototype.html 화면 ↔ 백엔드 API 대응표

docs/prototype.html은 제품 화면과 사용자 흐름의 기준이지만, 화면에 있는 모든 기능이
백엔드 구현 대상은 아니다. 이 문서는 각 화면이 실제로 어떤 API/필드로 채워지는지,
무엇이 프론트 정적 연출인지, MVP에서 무엇을 잘라내야 하는지 정리한 기준 문서다.
API 필드명은 docs/api-contract.md 최종본을 따른다.

## 화면별 대응표

| 화면 | 필요한 데이터 | 채우는 API/필드 | 백엔드 구현 | 프론트 정적 | MVP 처리 |
|---|---|---|---|---|---|
| **O-1 권한 온보딩** | 사진첩/캘린더 권한 문구 | 없음 | ✕ | ✅ | "사진은 기기 밖으로 나가지 않습니다", "온디바이스 분석" 문구 삭제. "데모 앨범 연결 동의"로 재정의 |
| **O-2 사진첩 스캔** | 스캔 애니메이션, 진행 카운트 | `POST /api/albums/connect`의 `scanSummary`(완료 후 표시용) | △ | ✅ (연출) | "사진첩을 살펴보고 있어요"/"기기 안에서만 처리됩니다" 삭제. `scanSummary.totalPhotos`/`coveragePeriod`는 고정 앨범 사실 요약일 뿐, 실시간 스캔 근거로 쓰지 않는다 |
| **H-1 홈 — 상태바** | "밤사이 34장 확인" 등 | 없음 | ✕ | ✅ | 상시 백그라운드 스캔 없음. 문구 삭제 또는 "분석하기" 버튼으로 대체 |
| **H-1 홈 — 주간 캘린더 스트립** | 요일별 마커 | `POST /api/analyses`의 `calendarMarkers[]`(date/type/label) | ✅ | — | `type: CARE_NOW\|RECOMMENDED\|USER_EVENT` 3종으로 렌더링. 별도 캘린더 REST API는 만들지 않는다 |
| **H-1 홈 — 자동 감지 알림 카드** | "2.8mm, 18% 빠름" | `POST /api/analyses`의 `changeSignal`(level/trend/confidence/reason) | ✅ (범주형만) | — | mm/% 수치 전부 삭제. `level: LOW\|MEDIUM\|HIGH`, `trend: STABLE\|INCREASING` 문구로 대체 |
| **H-1 홈 — 역방향 카드** | "결혼식 → 네일 8/12~13, 뿌리염색 8/11" | `POST /api/analyses`의 `upcomingEvent` + `reversePlan` | ✅ (네일만) | — | 뿌리염색 병행 삭제. `reversePlan`은 `canSchedule=true`일 때만 노출, 클릭 시 `/schedule` 호출로 연결 |
| **H-1 홈 — 케어 카드 리스트** | 네일/뿌리염색/속눈썹펌 3종, % | `POST /api/analyses`의 `careStatus`(status/needsCareNow) | ✅ (네일 1종) | — | 3종 → 1종. `status: NOW\|SOON\|FRESH`를 뱃지(now/soon/fresh)에 그대로 매핑 |
| **H-1 홈 — "+케어 추가", "3/3 사용중"** | 과금/멀티케어 UI | 없음 | ✕ | ✅ | 전체 삭제 (결제 없음, 사용자 한 명, 케어 한 종류) |
| **R-1 판단 과정 (AGENT REASONING)** | 6단계 + `scan_photo_library()` 등 가짜 툴 이름 | `POST /api/analyses`의 `evidenceLogs[]` | ✅ (재정의) | — | "AGENT REASONING" 라벨 삭제 — 이 화면은 Agent가 아니라 일반 코드(Vision/Policy)의 결과다. 존재하지 않는 툴 이름 전부 제거하고 `evidenceLogs`의 `type: SYSTEM\|VISION\|POLICY\|CALENDAR` 단계로 재구성 |
| **R-1 — "불확실해서 되묻기" 박스** | 신뢰도 기반 재질문 | `changeSignal.confidence`, `changeSignal.visionFailed` | △ | — | 대화형 재질문 로직은 없음. `confidence: LOW`이거나 `visionFailed: true`일 때의 정적 안내 문구로 단순화 |
| **R-2 역방향 스케줄링** | 목표 이벤트, 네일+뿌리염색 타임라인, 순서 로직 | `POST /api/analyses`의 `upcomingEvent` + `reversePlan` | ✅ (네일만) | — | 뿌리염색 항목/순서 로직 삭제. `reversePlan.basis`가 `UPCOMING_EVENT`일 때만 이 화면 진입, "두 개 한번에 잡기" 삭제 |
| **C-2 채팅·슬롯 — 말풍선/생각중 블록** | 스크립트 대사, `get_calendar_events()` 등 가짜 함수 텍스트 | `POST /api/analyses/{id}/schedule`(또는 `/retry`)의 `executionLogs[]` | ✅ (재정의) | ✅ (연출 타이밍) | **자유 입력창 없음 — 실제 채팅 API 불필요.** 프론트가 `executionLogs`를 스크립트처럼 순차 재생. 가짜 함수명은 실제 3-Tool 이름으로 교체. `filter(단골 우선)` 같은 개인화 로직 문구 삭제 |
| **C-2 — 슬롯 카드** | shop/time/price/개인화된 "이유" | `POST /api/analyses/{id}/schedule`(또는 `/retry`)의 `candidates[]` | ✅ | — | `price`, `recommendationReason` 필드로 대응. `recommendationReason`은 화이트리스트 4종(권장 관리 구간 내 / Calendar 충돌 없음 / 선호 네일샵 / 빠른 예약 가능 시간)만 사용 — 방문횟수·거리·야근여부 등 저장되지 않은 개인화 문구 금지 |
| **모달 (예약 확인)** | 선택 후보 재확인 | 프론트 보유 `candidates[]` 재사용 | ✅ (별도 API 불필요) | — | 그대로 유지. 실제 재확인은 `/confirm` 호출 시 서버가 수행 |
| **C-4 예약 완료** | 영수증, "캘린더 추가됨", "하루 전 알림", "계속 지켜볼게요" | `POST /api/bookings/{id}/confirm` | △ | — | "하루 전 알림"(푸시), "계속 지켜볼게요"(상시 모니터링) 삭제. `calendarEvent.created`가 `CACHED`에서는 `false`임을 반영해 "캘린더에 추가했어요" 문구를 모드에 따라 분기 |
| **A-1 활동 로그** | "247 사진확인/12 측정/3 제안", 7일치 로그(속눈썹·판단보류·조정제안 포함) | 없음 | ✕ | ✅ (전체) | DB 없는 인메모리 구조라 다일 누적 통계 자체가 불가능. 화면 자체를 MVP에서 제외하거나 완전 정적 더미로 처리 |

## 요약

- **백엔드가 실제로 채우는 화면**: 홈(캘린더 스트립/알림카드/역방향카드/케어카드), 판단 과정(evidenceLogs 재정의), 역방향 스케줄링, 슬롯 카드, 예약 완료
- **백엔드 지원이 없어 프론트 정적/연출로만 남는 화면**: 권한 온보딩, 사진첩 스캔 애니메이션, 상태바 문구, "+케어 추가"/과금 UI, 활동 로그
- **오해 소지가 있어 문구를 반드시 고쳐야 하는 지점**: 온디바이스 분석, mm/% 수치, "AGENT REASONING" 라벨, 뿌리염색·속눈썹 언급, 하루 전 알림, 상시 모니터링
