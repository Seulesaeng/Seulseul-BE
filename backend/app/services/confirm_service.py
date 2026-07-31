# POST /api/bookings/{candidate_id}/confirm 처리.
# confirm은 Agent Tool이 아니다 - smolagents를 실행하지 않는다 (CLAUDE.md 참고).
# 슬롯 충돌 판정은 app/services/policy.py의 기존 순수 함수를 재사용한다(중복 구현 금지).
from __future__ import annotations

from typing import Dict

from app import demo_clock
from app.errors import calendar_conflict, candidate_not_found, external_service_error, invalid_candidate_status
from app.models.schemas import CalendarEventResult, ConfirmResponse, RecheckResult, ShopBooking
from app.services import calendar_service, policy
from app.settings import get_modes


def _generate_confirmation_code(candidate_id: str) -> str:
    return f"SIM-{demo_clock.now().strftime('%Y%m%d%H%M%S')}-{candidate_id[-6:]}"


def confirm_candidate(candidate_id: str, candidates_store: Dict[str, dict], album_id: str) -> ConfirmResponse:
    # 1~2. 인메모리 candidate 조회, 없으면 404.
    candidate = candidates_store.get(candidate_id)
    if candidate is None:
        raise candidate_not_found(candidate_id)

    # 3~4. 이미 CONFIRMED면 멱등 - 재확인/예약/이벤트 생성을 다시 하지 않고 기존 결과를 반환한다.
    if candidate["status"] == "CONFIRMED":
        return ConfirmResponse(**candidate["confirmationResult"])

    if candidate["status"] != "PREPARED":
        raise invalid_candidate_status(candidate_id, candidate["status"])

    modes = get_modes()
    checked_at = demo_clock.now_iso()

    # 5~7. Calendar busy 시간 재조회 -> 기존 결정론 충돌 함수(policy.is_slot_busy) 재사용 -> 충돌 시 409.
    busy_times = calendar_service.get_busy_times(album_id, modes.calendar_mode)
    conflict = policy.is_slot_busy(candidate["start"], candidate["end"], busy_times)
    if conflict:
        conflicting = policy.find_conflicting_busy_times(candidate["start"], candidate["end"], busy_times)
        raise calendar_conflict(
            "재확인 결과 해당 시간대에 캘린더 일정이 생겨 예약할 수 없습니다.",
            detail={"conflict": True, "checkedAt": checked_at, "conflictingBusyTimes": conflicting},
        )

    recheck = RecheckResult(conflict=False, checkedAt=checked_at, conflictingBusyTimes=[])

    # 8. 네일샵 예약은 항상 시뮬레이션이다 (CLAUDE.md 절대 범위 참고).
    shop_booking = ShopBooking(simulated=True, confirmationCode=_generate_confirmation_code(candidate_id))

    # 9. CALENDAR_MODE에 따라 Calendar 이벤트 처리. LIVE 실패 시 candidate는 PREPARED로 유지된다
    #    (아래에서 CONFIRMED로 바꾸는 코드에 아직 도달하지 않았으므로 예외가 그대로 전파되면 된다).
    if modes.calendar_mode == "LIVE":
        try:
            event = calendar_service.create_event(candidate, modes.calendar_mode)
        except Exception as exc:
            raise external_service_error(f"Google Calendar 이벤트 생성에 실패했습니다: {exc}") from exc
        calendar_event = CalendarEventResult(
            created=True, simulated=False, eventId=event["eventId"], htmlLink=event["htmlLink"]
        )
    else:
        calendar_event = CalendarEventResult(created=False, simulated=True, eventId=None, htmlLink=None)

    confirmed_at = demo_clock.now_iso()
    response = ConfirmResponse(
        candidateId=candidate["candidateId"],
        slotId=candidate["slotId"],
        shop=candidate["shop"],
        artist=candidate["artist"],
        service=candidate["service"],
        price=candidate["price"],
        start=candidate["start"],
        end=candidate["end"],
        recommendationReason=candidate["recommendationReason"],
        status="CONFIRMED",
        confirmedAt=confirmed_at,
        reservationMode="SIMULATED",
        recheck=recheck,
        shopBooking=shop_booking,
        calendarEvent=calendar_event,
        calendarMode=modes.calendar_mode,
    )

    # 10~11. 모든 필수 처리가 성공한 뒤에만 CONFIRMED로 바꾸고 확정 결과를 저장한다.
    candidate["status"] = "CONFIRMED"
    candidate["confirmedAt"] = confirmed_at
    candidate["shopBooking"] = shop_booking.model_dump()
    candidate["calendarEvent"] = calendar_event.model_dump()
    candidate["confirmationResult"] = response.model_dump()

    return response
