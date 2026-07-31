# 목(mock) 데이터. POST /api/albums/connect, /schedule, /retry, /confirm이 사용한다.
# POST /api/analyses는 더 이상 이 모듈을 쓰지 않는다 (app/services/analysis_service.py의 실제 결정론 로직 사용).
# 실제 smolagents/Google Calendar는 아직 연결하지 않는다 (CLAUDE.md 구현 순서 참고).
# 앨범 사진은 backend/data/album.json(고정 fixture, 일부러 섞인 순서)에서 읽는다.
from __future__ import annotations

from datetime import datetime
from typing import List

from app import demo_clock
from app.fixtures import load_json
from app.models.schemas import (
    Candidate,
    CalendarEventResult,
    CoveragePeriod,
    ExcludedSlot,
    ExecutionLogEntry,
    Photo,
    RecheckResult,
    RecommendedWindow,
    ScanSummary,
    ShopBooking,
)


def _load_album() -> dict:
    return load_json("album.json")


ALBUM_ID = _load_album()["albumId"]
FAVORITE_SHOP = "슬슬네일 강남점"
ALTERNATIVE_SHOP = "무드네일 합정"

_WINDOWS = {
    "THIS_WEEK": ("2026-08-12", "2026-08-13"),
    "NEXT_WEEK": ("2026-08-19", "2026-08-20"),
}


def now_iso() -> str:
    return demo_clock.now_iso()


def build_photos(base_url: str) -> List[Photo]:
    album = _load_album()
    return [
        Photo(
            photoId=photo["photoId"],
            imageUrl=f"{base_url}demo-album/{photo['filename']}",
            takenAt=photo["takenAt"],
            label=photo["label"],
        )
        for photo in album["photos"]
    ]


def build_scan_summary() -> ScanSummary:
    album = _load_album()
    taken_dates = sorted(datetime.fromisoformat(photo["takenAt"]).date() for photo in album["photos"])
    return ScanSummary(
        totalPhotos=len(album["photos"]),
        coveragePeriod=CoveragePeriod(from_=taken_dates[0].isoformat(), to=taken_dates[-1].isoformat()),
    )


def build_recommended_window(search_scope: str) -> RecommendedWindow:
    start, end = _WINDOWS.get(search_scope, _WINDOWS["THIS_WEEK"])
    return RecommendedWindow(start=start, end=end, basis="UPCOMING_EVENT")


def build_excluded_slots(search_scope: str) -> List[ExcludedSlot]:
    start, _ = _WINDOWS.get(search_scope, _WINDOWS["THIS_WEEK"])
    slot_id = "slot_1201" if search_scope == "THIS_WEEK" else "slot_1401"
    return [
        ExcludedSlot(
            slotId=slot_id,
            shop=FAVORITE_SHOP,
            start=f"{start}T10:00:00+09:00",
            end=f"{start}T11:00:00+09:00",
            exclusionReason="CALENDAR_BUSY",
        )
    ]


def build_candidates(search_scope: str, schedule_run_id: str) -> List[Candidate]:
    start, end = _WINDOWS.get(search_scope, _WINDOWS["THIS_WEEK"])
    prefix = schedule_run_id
    return [
        Candidate(
            candidateId=f"{prefix}_c1",
            slotId="slot_1203" if search_scope == "THIS_WEEK" else "slot_1402",
            shop=FAVORITE_SHOP,
            artist="김아라",
            service="젤네일",
            price=45000,
            start=f"{start}T14:00:00+09:00",
            end=f"{start}T15:00:00+09:00",
            recommendationReason="선호 네일샵",
            status="PREPARED",
        ),
        Candidate(
            candidateId=f"{prefix}_c2",
            slotId="slot_1204" if search_scope == "THIS_WEEK" else "slot_1403",
            shop=FAVORITE_SHOP,
            artist="이나연",
            service="젤네일",
            price=42000,
            start=f"{end}T11:00:00+09:00",
            end=f"{end}T12:00:00+09:00",
            recommendationReason="권장 관리 구간 내",
            status="PREPARED",
        ),
        Candidate(
            candidateId=f"{prefix}_c3",
            slotId="slot_1301" if search_scope == "THIS_WEEK" else "slot_1501",
            shop=ALTERNATIVE_SHOP,
            artist="박서현",
            service="젤네일",
            price=40000,
            start=f"{end}T16:00:00+09:00",
            end=f"{end}T17:00:00+09:00",
            recommendationReason="빠른 예약 가능 시간",
            status="PREPARED",
        ),
    ]


def build_execution_logs(search_scope: str) -> List[ExecutionLogEntry]:
    scope_label = "이번 주" if search_scope == "THIS_WEEK" else "다음 주"
    return [
        ExecutionLogEntry(
            step=1,
            type="SYSTEM",
            tool=None,
            searchScope=search_scope,
            result=None,
            message=f"{scope_label} 범위로 예약 후보 탐색을 시작합니다.",
            exclusionReason=None,
        ),
        ExecutionLogEntry(
            step=2,
            type="TOOL",
            tool="get_calendar_busy_times",
            searchScope=search_scope,
            result={"busyTimes": [{"start": f"{_WINDOWS[search_scope][0]}T09:00:00+09:00", "end": f"{_WINDOWS[search_scope][0]}T12:00:00+09:00"}]},
            message="캘린더 바쁜 시간 조회 완료",
            exclusionReason=None,
        ),
        ExecutionLogEntry(
            step=3,
            type="TOOL",
            tool="search_nail_shop_slots",
            searchScope=search_scope,
            result={"shopScope": "FAVORITE_SHOP", "slotCount": 2},
            message="선호 네일샵 슬롯 2건 조회",
            exclusionReason=None,
        ),
        ExecutionLogEntry(
            step=4,
            type="AGENT",
            tool=None,
            searchScope=search_scope,
            result={"candidateCountSoFar": 2},
            message="선호샵 후보가 3개 미만이라 대체샵을 추가 조회합니다.",
            exclusionReason=None,
        ),
        ExecutionLogEntry(
            step=5,
            type="TOOL",
            tool="search_nail_shop_slots",
            searchScope=search_scope,
            result={"shopScope": "ALTERNATIVE_SHOPS", "slotCount": 1},
            message="대체 네일샵 슬롯 1건 조회",
            exclusionReason=None,
        ),
        ExecutionLogEntry(
            step=6,
            type="TOOL",
            tool="prepare_booking_candidates",
            searchScope=search_scope,
            result={"candidateCount": 3, "excludedSlotCount": 1},
            message="슬롯 1건 제외(캘린더 충돌), 예약 후보 3건 준비 완료",
            exclusionReason="CALENDAR_BUSY",
        ),
    ]


def build_recheck(checked_at: str) -> RecheckResult:
    return RecheckResult(conflict=False, checkedAt=checked_at)


def build_shop_booking() -> ShopBooking:
    code = "SIM-" + demo_clock.now().strftime("%Y%m%d%H%M%S")
    return ShopBooking(simulated=True, confirmationCode=code)


def build_calendar_event(calendar_mode: str) -> CalendarEventResult:
    if calendar_mode == "LIVE":
        # 실제 Google Calendar 연동은 아직 붙이지 않았다 (CLAUDE.md 구현 순서 9단계).
        # 구조만 맞춘 목 데이터를 반환한다.
        return CalendarEventResult(
            created=True,
            simulated=False,
            eventId="g_evt_mock_" + demo_clock.now().strftime("%H%M%S"),
            htmlLink="https://calendar.google.com/event?eid=mock",
        )
    return CalendarEventResult(created=False, simulated=True, eventId=None, htmlLink=None)
