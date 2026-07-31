# 결정론 정책 로직 (순수 함수). Agent는 이 판정을 하지 않는다 (CLAUDE.md 참고).
# 슬롯 충돌 판정/후보 준비 로직은 여기 한 곳에만 있다 - Agent Tool(app/agent/tools.py)과
# 결정론 fallback(app/services/schedule_service.py)이 이 함수들을 공유하며 중복 구현하지 않는다.
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from app.models.schemas import CalendarMarker, Candidate, CareStatus, ReversePlan, Timing, UpcomingEvent

REVERSE_PLAN_LEAD_DAYS_START = 3
REVERSE_PLAN_LEAD_DAYS_END = 2
REVERSE_PLAN_REASON_CODE = "EVENT_MINUS_2_TO_3_DAYS"

MAX_CANDIDATES = 3
NEXT_WEEK_OFFSET_DAYS = 7

RECOMMENDATION_REASON_FAVORITE_SHOP = "선호 네일샵"
RECOMMENDATION_REASON_NO_CONFLICT = "Calendar 충돌 없음"
RECOMMENDATION_REASON_WITHIN_WINDOW = "권장 관리 구간 내"
RECOMMENDATION_REASON_FAST_AVAILABLE = "빠른 예약 가능 시간"


@dataclass(frozen=True)
class CycleStats:
    minDays: int
    avgDays: float
    maxDays: int


def calculate_cycle_stats(service_dates: List[str]) -> CycleStats:
    parsed = sorted(date.fromisoformat(d) for d in service_dates)
    intervals = [(b - a).days for a, b in zip(parsed, parsed[1:])]
    if not intervals:
        raise ValueError("주기를 계산하려면 시술 기록이 2건 이상 있어야 합니다.")
    return CycleStats(minDays=min(intervals), avgDays=sum(intervals) / len(intervals), maxDays=max(intervals))


def calculate_days_since(last_service_date: date, today: date) -> int:
    return (today - last_service_date).days


def build_timing(service_dates: List[str], cycle_stats: CycleStats, today: date) -> Timing:
    last_service_date = max(date.fromisoformat(d) for d in service_dates)
    days_since = calculate_days_since(last_service_date, today)
    cycle_days = round(cycle_stats.avgDays)
    next_due_date = last_service_date + timedelta(days=cycle_days)
    return Timing(
        cycleDays=cycle_days,
        lastServiceDate=last_service_date.isoformat(),
        daysSinceLastService=days_since,
        nextDueDate=next_due_date.isoformat(),
    )


def decide_due(
    days_since_last: int,
    cycle_stats: CycleStats,
    change_level: str,
    vision_failed: bool,
) -> Tuple[bool, str]:
    """관리 시점(due) 여부를 판정한다. Vision 신호만으로는 due를 결정하지 않는다 —
    항상 날짜 조건(주기)과 함께여야 due가 된다."""
    if days_since_last >= cycle_stats.maxDays:
        return True, "CYCLE_MAX_REACHED"
    if days_since_last >= cycle_stats.minDays and change_level == "HIGH":
        return True, "EARLY_HIGH_CHANGE"
    if vision_failed and days_since_last >= cycle_stats.avgDays:
        return True, "VISION_FAILED_CYCLE_AVG_REACHED"
    return False, "CYCLE_ON_TRACK"


def decide_care_status(
    days_since_last: int,
    cycle_stats: CycleStats,
    change_level: str,
    vision_failed: bool,
    decided_at: str,
) -> CareStatus:
    due, reason_code = decide_due(days_since_last, cycle_stats, change_level, vision_failed)
    status = "NOW" if due else "FRESH"
    message = _build_message(status, cycle_stats, days_since_last, reason_code)

    return CareStatus(
        status=status,
        needsCareNow=due,
        reasonCodes=[reason_code],
        message=message,
        decidedBy="POLICY",
        decidedAt=decided_at,
    )


def _build_message(status: str, cycle_stats: CycleStats, days_since_last: int, reason_code: str) -> str:
    cycle_days = round(cycle_stats.avgDays)
    if status == "NOW":
        by_reason = {
            "CYCLE_MAX_REACHED": f"최대 주기({cycle_stats.maxDays}일)를 넘었습니다",
            "EARLY_HIGH_CHANGE": f"최소 주기({cycle_stats.minDays}일) 이후 변화 수준이 HIGH로 감지되었습니다",
            "VISION_FAILED_CYCLE_AVG_REACHED": f"Vision 분석에 실패했고 평균 주기({cycle_days}일)에 도달했습니다",
        }
        reason_text = by_reason.get(reason_code, f"관리 주기({cycle_days}일)에 도달했습니다")
        return f"{reason_text} (경과 {days_since_last}일)."
    return f"아직 관리 시점이 아닙니다 (경과 {days_since_last}일, 평균 주기 {cycle_days}일)."


@dataclass(frozen=True)
class ReversePlanCalc:
    targetEvent: UpcomingEvent
    recommendedStart: str
    recommendedEnd: str
    reasonCode: str
    userMessage: str


def calculate_reverse_plan(upcoming_event: Optional[UpcomingEvent]) -> Optional[ReversePlanCalc]:
    """네일 정책: 중요 일정 2~3일 전이 권장 구간이다 (고정 정책)."""
    if upcoming_event is None:
        return None
    event_date = date.fromisoformat(upcoming_event.date)
    start = event_date - timedelta(days=REVERSE_PLAN_LEAD_DAYS_START)
    end = event_date - timedelta(days=REVERSE_PLAN_LEAD_DAYS_END)
    user_message = f"{upcoming_event.title}({upcoming_event.date}) 기준 시술 2~3일 전이 가장 좋은 상태입니다."
    return ReversePlanCalc(
        targetEvent=upcoming_event,
        recommendedStart=start.isoformat(),
        recommendedEnd=end.isoformat(),
        reasonCode=REVERSE_PLAN_REASON_CODE,
        userMessage=user_message,
    )


def to_api_reverse_plan(calc: Optional[ReversePlanCalc]) -> Optional[ReversePlan]:
    """계약 필드(ReversePlan: recommendedStart/recommendedEnd/basis/reason)로 변환한다.
    API 계약은 바꾸지 않는다 — targetEvent/reasonCode/userMessage는 evidenceLogs에만 남긴다."""
    if calc is None:
        return None
    return ReversePlan(
        recommendedStart=calc.recommendedStart,
        recommendedEnd=calc.recommendedEnd,
        basis="UPCOMING_EVENT",
        reason=calc.userMessage,
    )


def build_calendar_markers(
    today: date,
    needs_care_now: bool,
    reverse_plan: Optional[ReversePlan],
    upcoming_event: Optional[UpcomingEvent],
) -> List[CalendarMarker]:
    markers: List[CalendarMarker] = []
    if needs_care_now:
        markers.append(CalendarMarker(date=today.isoformat(), type="CARE_NOW", label="지금 관리"))
    if reverse_plan is not None:
        markers.append(CalendarMarker(date=reverse_plan.recommendedStart, type="RECOMMENDED", label="네일 권장"))
        if reverse_plan.recommendedEnd != reverse_plan.recommendedStart:
            markers.append(CalendarMarker(date=reverse_plan.recommendedEnd, type="RECOMMENDED", label="네일 권장"))
    if upcoming_event is not None:
        markers.append(CalendarMarker(date=upcoming_event.date, type="USER_EVENT", label=upcoming_event.title))
    return markers


# ---- 네일샵 슬롯 검색/충돌 판정/후보 준비 (POST /api/analyses/{id}/schedule 전용) ----


def _slots_overlap(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> bool:
    return a_start < b_end and b_start < a_end


def is_slot_busy(slot_start: str, slot_end: str, busy_times: List[dict]) -> bool:
    start = datetime.fromisoformat(slot_start)
    end = datetime.fromisoformat(slot_end)
    for busy in busy_times:
        if _slots_overlap(start, end, datetime.fromisoformat(busy["start"]), datetime.fromisoformat(busy["end"])):
            return True
    return False


def find_conflicting_busy_times(slot_start: str, slot_end: str, busy_times: List[dict]) -> List[dict]:
    """is_slot_busy와 동일한 충돌 판정(_slots_overlap)을 재사용해, 실제로 겹치는
    busy time만 추린다 (POST /api/bookings/{id}/confirm의 재확인 상세용)."""
    start = datetime.fromisoformat(slot_start)
    end = datetime.fromisoformat(slot_end)
    return [
        busy
        for busy in busy_times
        if _slots_overlap(start, end, datetime.fromisoformat(busy["start"]), datetime.fromisoformat(busy["end"]))
    ]


def is_within_window(slot_start: str, slot_end: str, window_start: str, window_end: str) -> bool:
    start = datetime.fromisoformat(slot_start)
    end = datetime.fromisoformat(slot_end)
    return datetime.fromisoformat(window_start) <= start and end <= datetime.fromisoformat(window_end)


def find_eligible_and_excluded_slots(
    slots: List[Dict[str, Any]], busy_times: List[dict], window_start: str, window_end: str
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """슬롯 목록을 조회 기간(window)과 캘린더 충돌 기준으로 eligible/excluded로 나눈다."""
    eligible: List[Dict[str, Any]] = []
    excluded: List[Dict[str, Any]] = []
    for slot in slots:
        if not is_within_window(slot["start"], slot["end"], window_start, window_end):
            excluded.append(
                {
                    "slotId": slot["slotId"],
                    "shop": slot["shop"],
                    "start": slot["start"],
                    "end": slot["end"],
                    "exclusionReason": "OUTSIDE_SEARCH_SCOPE",
                }
            )
            continue
        if is_slot_busy(slot["start"], slot["end"], busy_times):
            excluded.append(
                {
                    "slotId": slot["slotId"],
                    "shop": slot["shop"],
                    "start": slot["start"],
                    "end": slot["end"],
                    "exclusionReason": "CALENDAR_BUSY",
                }
            )
            continue
        eligible.append(slot)
    return eligible, excluded


def determine_recommendation_reason(slot: Dict[str, Any], is_earliest_non_favorite: bool) -> str:
    """recommendationReason은 LLM이 생성하지 않는다 - 화이트리스트 매핑만 사용한다."""
    if slot.get("favoriteShop"):
        return RECOMMENDATION_REASON_FAVORITE_SHOP
    if is_earliest_non_favorite:
        return RECOMMENDATION_REASON_FAST_AVAILABLE
    return RECOMMENDATION_REASON_WITHIN_WINDOW


def prepare_candidates_from_slot_ids(
    eligible_slot_ids: List[str], album_id: str, schedule_run_id: str
) -> Tuple[List[Candidate], List[str]]:
    """중복 제거 -> fixture 존재 검증 -> 결정론 정렬 -> 최대 MAX_CANDIDATES개 선택 ->
    recommendationReason 화이트리스트 부여. Agent Tool과 결정론 fallback이 이 함수를 공유한다."""
    from app.services import nail_shop_service  # 지연 임포트로 순환 의존을 피한다.

    unique_ids = sorted(set(eligible_slot_ids))
    valid_slots: List[Dict[str, Any]] = []
    skipped_ids: List[str] = []
    for slot_id in unique_ids:
        detail = nail_shop_service.get_slot_by_id(album_id, slot_id)
        if detail is None:
            skipped_ids.append(slot_id)
            continue
        valid_slots.append(detail)

    valid_slots.sort(key=lambda s: (s["start"], s["slotId"]))  # 결정론 정렬
    selected = valid_slots[:MAX_CANDIDATES]

    non_favorite = [s for s in selected if not s.get("favoriteShop")]
    earliest_non_favorite_id = min(non_favorite, key=lambda s: s["start"])["slotId"] if non_favorite else None

    candidates: List[Candidate] = []
    for slot in selected:
        reason = determine_recommendation_reason(slot, slot["slotId"] == earliest_non_favorite_id)
        candidates.append(
            Candidate(
                candidateId=f"{schedule_run_id}_{slot['slotId']}",
                slotId=slot["slotId"],
                shop=slot["shop"],
                artist=slot["artist"],
                service=slot["service"],
                price=slot["price"],
                start=slot["start"],
                end=slot["end"],
                recommendationReason=reason,
                status="PREPARED",
            )
        )
    return candidates, skipped_ids


# ---- POST /api/analyses/{id}/retry (searchScope=NEXT_WEEK) 전용 ----


def calculate_next_week_window(recommended_start: str, recommended_end: str) -> Tuple[str, str]:
    """retry(NEXT_WEEK) 검색창: 기존 reversePlan의 recommendedStart/recommendedEnd에 각각
    7일을 더한다 (순수 함수). 예: 2026-08-12~2026-08-13 -> 2026-08-19~2026-08-20."""
    start = date.fromisoformat(recommended_start) + timedelta(days=NEXT_WEEK_OFFSET_DAYS)
    end = date.fromisoformat(recommended_end) + timedelta(days=NEXT_WEEK_OFFSET_DAYS)
    return start.isoformat(), end.isoformat()
