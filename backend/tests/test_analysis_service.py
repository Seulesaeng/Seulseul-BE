# POST /api/analyses 결정론 로직 단위 테스트 (순수 함수 중심).
# 고정 fixture 값(backend/data/*.json, DEMO_NOW=2026-08-10T09:41:00+09:00) 기준.
from __future__ import annotations

from datetime import date

import pytest

from app import mock_data
from app.models.schemas import Photo
from app.services import calendar_service, photo_service, policy

DEMO_TODAY = date(2026, 8, 10)


def _photo(photo_id: str, taken_at: str) -> Photo:
    return Photo(photoId=photo_id, imageUrl=f"http://x/{photo_id}.jpg", takenAt=taken_at, label=photo_id)


# ---- 1. 앨범 처리: 사진 정렬 ----


def test_sort_photos_by_taken_at_orders_ascending_regardless_of_input_order():
    shuffled = [
        _photo("photo_03", "2026-06-24T09:00:00+09:00"),
        _photo("photo_01", "2026-06-10T09:00:00+09:00"),
        _photo("photo_02", "2026-06-17T09:00:00+09:00"),
    ]
    sorted_photos = photo_service.sort_photos_by_taken_at(shuffled)
    assert [p.photoId for p in sorted_photos] == ["photo_01", "photo_02", "photo_03"]


def test_real_album_fixture_is_deliberately_unsorted():
    album = mock_data._load_album()
    taken_ats = [p["takenAt"] for p in album["photos"]]
    assert taken_ats != sorted(taken_ats)


# ---- 1. 앨범 처리: 기준 사진 (마지막 시술일과 가장 가까운 사진) ----


def test_baseline_photo_is_nearest_to_reference_date_not_simply_earliest():
    # 가장 오래된 사진은 2026-01-01이지만, 마지막 시술일(2026-07-16)과 가장 가까운
    # 사진은 2026-07-17(1일 차이)이어야 한다 -> "가장 오래된 사진"과는 다른 결과.
    photos = [
        _photo("photo_old", "2026-01-01T09:00:00+09:00"),
        _photo("photo_far", "2026-07-10T09:00:00+09:00"),  # 6일 차이
        _photo("photo_near", "2026-07-17T09:00:00+09:00"),  # 1일 차이 (가장 가까움)
        _photo("photo_recent1", "2026-08-01T09:00:00+09:00"),
        _photo("photo_recent2", "2026-08-05T09:00:00+09:00"),
        _photo("photo_recent3", "2026-08-09T09:00:00+09:00"),
    ]
    sorted_photos = photo_service.sort_photos_by_taken_at(photos)
    baseline = photo_service.select_baseline_photo(sorted_photos, reference_date=date(2026, 7, 16))
    assert baseline.photoId == "photo_near"
    assert baseline.photoId != sorted_photos[0].photoId  # 가장 오래된 사진과는 다르다


def test_real_album_fixture_baseline_is_photo_taken_on_last_service_date():
    photos = mock_data.build_photos("http://testserver/")
    sorted_photos = photo_service.sort_photos_by_taken_at(photos)
    baseline = photo_service.select_baseline_photo(sorted_photos, reference_date=date(2026, 7, 16))
    assert baseline.photoId == "photo_01"
    assert baseline.takenAt.startswith("2026-07-16")


# ---- 1. 앨범 처리: 최근 사진 3장 (기준 사진과 중복 없음) ----


def test_recent_photos_exclude_baseline_when_baseline_is_among_most_recent():
    # 기준 사진이 하필 가장 최근 3장 안에 들어갈 경우, 대체 사진으로 채워 중복을 피해야 한다.
    photos = [
        _photo("photo_01", "2026-01-01T09:00:00+09:00"),
        _photo("photo_02", "2026-02-01T09:00:00+09:00"),
        _photo("photo_03", "2026-03-01T09:00:00+09:00"),
        _photo("photo_04", "2026-04-01T09:00:00+09:00"),
        _photo("photo_baseline", "2026-08-09T09:00:00+09:00"),  # 최근 3장 안에 드는 시점
    ]
    sorted_photos = photo_service.sort_photos_by_taken_at(photos)
    # reference_date를 baseline 사진 날짜와 정확히 일치시켜 그 사진이 기준으로 뽑히게 한다.
    baseline = photo_service.select_baseline_photo(sorted_photos, reference_date=date(2026, 8, 9))
    assert baseline.photoId == "photo_baseline"

    recent = photo_service.select_recent_photos(sorted_photos, baseline)
    recent_ids = [p.photoId for p in recent]
    assert baseline.photoId not in recent_ids
    assert len(recent) == 3
    assert recent_ids == ["photo_02", "photo_03", "photo_04"]  # baseline을 제외한 최근 3장


def test_real_album_fixture_recent_photos_are_last_three_excluding_baseline():
    photos = mock_data.build_photos("http://testserver/")
    selected = photo_service.build_selected_photos(photos, reference_date=date(2026, 7, 16))
    assert selected[0].role == "BASELINE"
    assert selected[0].photoId == "photo_01"
    recent_ids = [p.photoId for p in selected[1:]]
    assert recent_ids == ["photo_04", "photo_05", "photo_06"]  # 08-06, 08-08, 08-09
    assert "photo_01" not in recent_ids


# ---- 2. 시술 주기 ----


def test_calculate_cycle_stats_matches_fixture_result():
    stats = policy.calculate_cycle_stats(["2026-04-29", "2026-05-23", "2026-06-18", "2026-07-16"])
    # intervals: 24, 26, 28
    assert stats.minDays == 24
    assert stats.maxDays == 28
    assert stats.avgDays == pytest.approx(26.0)


def test_days_since_last_treatment_matches_fixture_result():
    days = policy.calculate_days_since(date(2026, 7, 16), DEMO_TODAY)
    assert days == 25


def test_calculate_cycle_stats_requires_at_least_two_dates():
    with pytest.raises(ValueError):
        policy.calculate_cycle_stats(["2026-07-16"])


# ---- 3. 관리 시점 정책 (Vision만으로 due를 결정하지 않는다) ----

CYCLE = policy.CycleStats(minDays=24, avgDays=26.0, maxDays=28)


def test_23_days_high_change_is_not_due():
    # daysSince(23) < cycleMin(24) -> HIGH 변화만으로는 due가 되지 않는다.
    due, reason = policy.decide_due(23, CYCLE, change_level="HIGH", vision_failed=False)
    assert due is False
    assert reason == "CYCLE_ON_TRACK"


def test_25_days_high_change_is_due():
    # daysSince(25) >= cycleMin(24) and HIGH -> due.
    due, reason = policy.decide_due(25, CYCLE, change_level="HIGH", vision_failed=False)
    assert due is True
    assert reason == "EARLY_HIGH_CHANGE"


def test_vision_failed_at_26_days_is_due():
    # daysSince(26) >= cycleAvg(26) and visionFailed -> due.
    due, reason = policy.decide_due(26, CYCLE, change_level="LOW", vision_failed=True)
    assert due is True
    assert reason == "VISION_FAILED_CYCLE_AVG_REACHED"


def test_29_days_low_change_is_due():
    # daysSince(29) >= cycleMax(28) -> due, changeLevel과 무관하게 항상 due.
    due, reason = policy.decide_due(29, CYCLE, change_level="LOW", vision_failed=False)
    assert due is True
    assert reason == "CYCLE_MAX_REACHED"


def test_vision_alone_never_determines_due():
    # visionFailed도 아니고 changeLevel=HIGH이지만 daysSince가 cycleMin 미만이면 여전히 not due.
    due, _ = policy.decide_due(10, CYCLE, change_level="HIGH", vision_failed=False)
    assert due is False
    # changeLevel=HIGH지만 visionFailed=False이고 날짜 조건도 미충족이면 not due.
    due2, _ = policy.decide_due(0, CYCLE, change_level="HIGH", vision_failed=True)
    assert due2 is False


def test_decide_care_status_maps_due_to_now_status():
    status = policy.decide_care_status(
        days_since_last=25, cycle_stats=CYCLE, change_level="HIGH", vision_failed=False,
        decided_at="2026-08-10T09:41:00+09:00",
    )
    assert status.status == "NOW"
    assert status.needsCareNow is True
    assert status.reasonCodes == ["EARLY_HIGH_CHANGE"]
    assert status.decidedBy == "POLICY"


def test_decide_care_status_maps_not_due_to_fresh_status():
    status = policy.decide_care_status(
        days_since_last=5, cycle_stats=CYCLE, change_level="LOW", vision_failed=False,
        decided_at="2026-08-10T09:41:00+09:00",
    )
    assert status.status == "FRESH"
    assert status.needsCareNow is False


# ---- 4. 중요 일정 (CALENDAR_MODE=CACHED -> calendar_events.json) ----


def test_select_nearest_care_relevant_event_picks_wedding():
    event = calendar_service.select_nearest_care_relevant_event("album-001", today=DEMO_TODAY)
    assert event is not None
    assert event.eventId == "evt_wedding_001"
    assert event.title == "친구 결혼식"
    assert event.date == "2026-08-15"


def test_select_nearest_care_relevant_event_none_for_unknown_album():
    event = calendar_service.select_nearest_care_relevant_event("album-unknown", today=DEMO_TODAY)
    assert event is None


# ---- 5. 역방향 스케줄: 결혼식 8/15 -> 8/12~13 ----


def test_calculate_reverse_plan_wedding_minus_2_to_3_days():
    event = calendar_service.select_nearest_care_relevant_event("album-001", today=DEMO_TODAY)
    calc = policy.calculate_reverse_plan(event)
    assert calc is not None
    assert calc.targetEvent.eventId == "evt_wedding_001"
    assert calc.recommendedStart == "2026-08-12"
    assert calc.recommendedEnd == "2026-08-13"
    assert calc.reasonCode == "EVENT_MINUS_2_TO_3_DAYS"
    assert calc.userMessage  # 사람이 읽을 수 있는 메시지가 채워져 있다


def test_calculate_reverse_plan_none_when_no_event():
    assert policy.calculate_reverse_plan(None) is None


def test_to_api_reverse_plan_maps_to_contract_fields_without_changing_contract():
    event = calendar_service.select_nearest_care_relevant_event("album-001", today=DEMO_TODAY)
    calc = policy.calculate_reverse_plan(event)
    api_plan = policy.to_api_reverse_plan(calc)
    assert api_plan.recommendedStart == "2026-08-12"
    assert api_plan.recommendedEnd == "2026-08-13"
    assert api_plan.basis == "UPCOMING_EVENT"
    assert api_plan.reason == calc.userMessage


# ---- calendarMarkers (기존 로직 유지 확인) ----


def test_build_calendar_markers_types_and_dates():
    event = calendar_service.select_nearest_care_relevant_event("album-001", today=DEMO_TODAY)
    calc = policy.calculate_reverse_plan(event)
    api_plan = policy.to_api_reverse_plan(calc)
    markers = policy.build_calendar_markers(
        today=DEMO_TODAY, needs_care_now=True, reverse_plan=api_plan, upcoming_event=event
    )
    by_type = {m.type: m for m in markers}
    assert by_type["CARE_NOW"].date == "2026-08-10"
    assert by_type["USER_EVENT"].date == "2026-08-15"
    recommended_dates = {m.date for m in markers if m.type == "RECOMMENDED"}
    assert recommended_dates == {"2026-08-12", "2026-08-13"}
