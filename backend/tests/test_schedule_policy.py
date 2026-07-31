# app/services/policy.py의 슬롯 검색/충돌 판정/후보 준비 순수 함수 단위 테스트.
# 실제 fixture(backend/data/nail_shop_slots.json, calendar_busy.json)를 사용한다.
from __future__ import annotations

from app.services import calendar_service, nail_shop_service, policy

ALBUM_ID = "album-001"
WINDOW_START = "2026-08-12T00:00:00+09:00"
WINDOW_END = "2026-08-13T23:59:59+09:00"

VALID_REASONS = {"권장 관리 구간 내", "Calendar 충돌 없음", "선호 네일샵", "빠른 예약 가능 시간"}


def _busy_times():
    return calendar_service.get_busy_times(ALBUM_ID, "CACHED")


def test_favorite_shop_search_yields_exactly_one_eligible_slot():
    slots = nail_shop_service.get_slots_by_scope(ALBUM_ID, "FAVORITE_SHOP")
    eligible, excluded = policy.find_eligible_and_excluded_slots(slots, _busy_times(), WINDOW_START, WINDOW_END)

    assert [s["slotId"] for s in eligible] == ["slot_2001"]
    excluded_ids = {e["slotId"]: e["exclusionReason"] for e in excluded}
    assert excluded_ids["slot_2003"] == "CALENDAR_BUSY"  # 팀 회의와 충돌
    assert excluded_ids["slot_2005"] == "OUTSIDE_SEARCH_SCOPE"  # 다음 주(8/19), 창 밖


def test_alternative_shop_search_yields_two_eligible_slots():
    slots = nail_shop_service.get_slots_by_scope(ALBUM_ID, "ALTERNATIVE_SHOPS")
    eligible, excluded = policy.find_eligible_and_excluded_slots(slots, _busy_times(), WINDOW_START, WINDOW_END)

    assert {s["slotId"] for s in eligible} == {"slot_2002", "slot_2004"}
    # slot_2006/slot_2007은 다음 주(NEXT_WEEK) 슬롯이라 이번 주 창 기준으로는 제외된다.
    excluded_ids = {e["slotId"]: e["exclusionReason"] for e in excluded}
    assert excluded_ids == {"slot_2006": "OUTSIDE_SEARCH_SCOPE", "slot_2007": "OUTSIDE_SEARCH_SCOPE"}


def test_prepare_candidates_from_slot_ids_dedup_sort_and_cap_at_three():
    ids_with_duplicates = ["slot_2004", "slot_2001", "slot_2001", "slot_2002"]
    candidates, skipped = policy.prepare_candidates_from_slot_ids(ids_with_duplicates, ALBUM_ID, "run1")

    assert skipped == []
    assert len(candidates) == 3
    # 결정론 정렬: start 시각 오름차순 (2002: 8/12 14:00 -> 2001: 8/12 19:00 -> 2004: 8/13 20:00)
    assert [c.slotId for c in candidates] == ["slot_2002", "slot_2001", "slot_2004"]


def test_prepare_candidates_from_slot_ids_skips_nonexistent_id():
    candidates, skipped = policy.prepare_candidates_from_slot_ids(
        ["slot_2001", "slot_bogus_id"], ALBUM_ID, "run1"
    )
    assert skipped == ["slot_bogus_id"]
    assert [c.slotId for c in candidates] == ["slot_2001"]


def test_full_favorite_plus_alternative_prepare_matches_one_plus_two():
    favorite_slots = nail_shop_service.get_slots_by_scope(ALBUM_ID, "FAVORITE_SHOP")
    alt_slots = nail_shop_service.get_slots_by_scope(ALBUM_ID, "ALTERNATIVE_SHOPS")
    favorite_eligible, _ = policy.find_eligible_and_excluded_slots(favorite_slots, _busy_times(), WINDOW_START, WINDOW_END)
    alt_eligible, _ = policy.find_eligible_and_excluded_slots(alt_slots, _busy_times(), WINDOW_START, WINDOW_END)

    all_ids = [s["slotId"] for s in favorite_eligible] + [s["slotId"] for s in alt_eligible]
    candidates, skipped = policy.prepare_candidates_from_slot_ids(all_ids, ALBUM_ID, "run1")

    assert skipped == []
    assert len(candidates) == 3
    favorite_count = sum(1 for c in candidates if c.recommendationReason == "선호 네일샵")
    assert favorite_count == 1
    assert len(candidates) - favorite_count == 2

    for candidate in candidates:
        assert isinstance(candidate.price, int)
        assert candidate.recommendationReason in VALID_REASONS
        assert candidate.status == "PREPARED"


# ---- POST /api/analyses/{id}/retry (searchScope=NEXT_WEEK) ----

NEXT_WEEK_START = "2026-08-19T00:00:00+09:00"
NEXT_WEEK_END = "2026-08-20T23:59:59+09:00"


def test_calculate_next_week_window_adds_seven_days():
    start, end = policy.calculate_next_week_window("2026-08-12", "2026-08-13")
    assert start == "2026-08-19"
    assert end == "2026-08-20"


def test_calculate_next_week_window_is_pure_and_deterministic():
    # 같은 입력이면 항상 같은 출력 (순수 함수).
    assert policy.calculate_next_week_window("2026-08-12", "2026-08-13") == ("2026-08-19", "2026-08-20")
    assert policy.calculate_next_week_window("2026-08-12", "2026-08-13") == ("2026-08-19", "2026-08-20")


def test_next_week_favorite_shop_search_yields_exactly_one_eligible_slot():
    slots = nail_shop_service.get_slots_by_scope(ALBUM_ID, "FAVORITE_SHOP")
    eligible, excluded = policy.find_eligible_and_excluded_slots(
        slots, _busy_times(), NEXT_WEEK_START, NEXT_WEEK_END
    )
    assert [s["slotId"] for s in eligible] == ["slot_2005"]
    excluded_ids = {e["slotId"] for e in excluded}
    assert excluded_ids == {"slot_2001", "slot_2003"}  # 이번 주 슬롯이라 다음 주 창 밖


def test_next_week_alternative_shop_search_yields_two_eligible_slots():
    slots = nail_shop_service.get_slots_by_scope(ALBUM_ID, "ALTERNATIVE_SHOPS")
    eligible, excluded = policy.find_eligible_and_excluded_slots(
        slots, _busy_times(), NEXT_WEEK_START, NEXT_WEEK_END
    )
    assert {s["slotId"] for s in eligible} == {"slot_2006", "slot_2007"}
    excluded_ids = {e["slotId"] for e in excluded}
    assert excluded_ids == {"slot_2002", "slot_2004"}  # 이번 주 슬롯이라 다음 주 창 밖


def test_determine_recommendation_reason_only_uses_whitelist():
    favorite_slot = {"favoriteShop": True}
    non_favorite_earliest = {"favoriteShop": False}
    non_favorite_other = {"favoriteShop": False}

    assert policy.determine_recommendation_reason(favorite_slot, is_earliest_non_favorite=False) == "선호 네일샵"
    assert (
        policy.determine_recommendation_reason(non_favorite_earliest, is_earliest_non_favorite=True)
        == "빠른 예약 가능 시간"
    )
    assert (
        policy.determine_recommendation_reason(non_favorite_other, is_earliest_non_favorite=False)
        == "권장 관리 구간 내"
    )
