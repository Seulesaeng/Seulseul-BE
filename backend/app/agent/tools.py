# smolagents ToolCallingAgent Tool 3개 (CLAUDE.md 참고).
# Agent에게 사진 분석, 관리 시점 판정, 슬롯 충돌 임의 판정, 실제 예약 확정,
# Calendar 이벤트 생성 기능을 주지 않는다. 슬롯 충돌 판정/후보 준비 로직은
# app/services/policy.py의 순수 함수가 담당하고, 이 파일은 그 함수를 호출만 한다
# (중복 구현 금지). candidates/excludedSlots의 source of truth는 AgentRunContext다.
from __future__ import annotations

import json

from smolagents import Tool

from app.agent.context import AgentRunContext, EventCollector
from app.services import calendar_service, nail_shop_service, policy
from app.settings import get_modes


class GetCalendarBusyTimesTool(Tool):
    name = "get_calendar_busy_times"
    description = (
        "analysis의 역방향 권장 구간(reversePlan) 동안 사용자 캘린더의 바쁜 시간대를 조회한다. "
        "실제 검색 기간은 항상 analysis.reversePlan을 따르며, 이 도구에 전달된 값과 다르더라도 "
        "무시된다. 반환값에는 조회 건수와 기간만 포함되고 바쁜 시간 상세는 포함되지 않는다."
    )
    inputs = {
        "start_datetime": {
            "type": "string",
            "description": "조회 시작 일시 (ISO 8601). 참고용이며 실제로는 analysis.reversePlan 기준 기간을 사용한다.",
        },
        "end_datetime": {
            "type": "string",
            "description": "조회 종료 일시 (ISO 8601). 참고용이며 실제로는 analysis.reversePlan 기준 기간을 사용한다.",
        },
    }
    output_type = "string"

    def __init__(self, context: AgentRunContext, collector: EventCollector, album_id: str):
        self.context = context
        self.collector = collector
        self.album_id = album_id
        super().__init__()

    def forward(self, start_datetime: str, end_datetime: str) -> str:
        # 검색 기간은 항상 analysis.reversePlan을 사용한다. LLM이 새로 만들거나 바꿀 수 없다.
        matches_reverse_plan = (
            start_datetime == self.context.search_start and end_datetime == self.context.search_end
        )
        calendar_mode = get_modes().calendar_mode
        busy_times = calendar_service.get_busy_times(self.album_id, calendar_mode)
        self.context.busy_times = busy_times

        result = {
            "busyCount": len(busy_times),
            "start": self.context.search_start,
            "end": self.context.search_end,
            "success": True,
        }
        message = "캘린더 바쁜 시간 조회 완료"
        if not matches_reverse_plan:
            message += " (요청 기간이 reversePlan과 달라 analysis.reversePlan 기준으로 대체함)"
        self.collector.record(
            "TOOL",
            message,
            tool=self.name,
            result={**result, "requestedStart": start_datetime, "requestedEnd": end_datetime},
        )
        return json.dumps(result, ensure_ascii=False)


class SearchNailShopSlotsTool(Tool):
    name = "search_nail_shop_slots"
    description = (
        "네일샵 예약 가능 슬롯을 조회한다. 캘린더 바쁜 시간은 이전 get_calendar_busy_times 호출 "
        "결과를 내부적으로 사용하며 인자로 다시 전달할 필요가 없다. 반환값에는 조회 범위, 새로 "
        "찾은 슬롯 ID 목록과 개수, 누적 개수, 목표 후보 수 대비 충분한지 여부, 제외된 슬롯 개수만 "
        "포함되고 슬롯 상세 JSON은 포함되지 않는다."
    )
    inputs = {
        "search_scope": {"type": "string", "description": '"FAVORITE_SHOP" 또는 "ALTERNATIVE_SHOPS".'},
    }
    output_type = "string"

    def __init__(self, context: AgentRunContext, collector: EventCollector, album_id: str):
        self.context = context
        self.collector = collector
        self.album_id = album_id
        super().__init__()

    def forward(self, search_scope: str) -> str:
        if search_scope not in ("FAVORITE_SHOP", "ALTERNATIVE_SHOPS"):
            result = {"error": f"알 수 없는 search_scope: {search_scope}"}
            self.collector.record("TOOL", f"알 수 없는 search_scope: {search_scope}", tool=self.name, result=result)
            return json.dumps(result, ensure_ascii=False)

        slots = nail_shop_service.get_slots_by_scope(self.album_id, search_scope)
        eligible, excluded = policy.find_eligible_and_excluded_slots(
            slots, self.context.busy_times, self.context.search_start, self.context.search_end
        )

        new_eligible_ids = [s["slotId"] for s in eligible if s["slotId"] not in self.context.eligible_slot_ids]
        self.context.eligible_slot_ids.extend(new_eligible_ids)
        self.context.excluded_slots.extend(excluded)
        if search_scope not in self.context.searched_scopes:
            self.context.searched_scopes.append(search_scope)

        total_eligible = len(set(self.context.eligible_slot_ids))
        result = {
            "searchScope": search_scope,
            "eligibleSlotIds": new_eligible_ids,
            "eligibleCount": len(new_eligible_ids),
            "totalEligibleCount": total_eligible,
            "targetCandidateCount": policy.MAX_CANDIDATES,
            "hasEnoughCandidates": total_eligible >= policy.MAX_CANDIDATES,
            "excludedCount": len(excluded),
        }
        self.collector.record("TOOL", f"{search_scope} 슬롯 조회 완료", tool=self.name, result=result)

        for excluded_slot in excluded:
            self.collector.record(
                "POLICY",
                f"슬롯 {excluded_slot['slotId']} 제외: {excluded_slot['exclusionReason']}",
                exclusion_reason=excluded_slot["exclusionReason"],
                result={"slotId": excluded_slot["slotId"], "shop": excluded_slot["shop"]},
            )

        if not result["hasEnoughCandidates"]:
            self.collector.record(
                "AGENT",
                f"누적 후보가 {total_eligible}개로 목표({policy.MAX_CANDIDATES}개) 미만이라 추가 검색이 필요합니다.",
                result={"totalEligibleCount": total_eligible},
            )

        return json.dumps(result, ensure_ascii=False)


class PrepareBookingCandidatesTool(Tool):
    name = "prepare_booking_candidates"
    description = (
        "지금까지 조회된 예약 가능 슬롯 ID들로 최종 예약 후보를 준비한다. 인자를 받지 않고 내부 "
        "컨텍스트의 eligible_slot_ids만 사용한다. 중복을 제거하고 결정론적으로 정렬한 뒤 최대 "
        f"{policy.MAX_CANDIDATES}개를 선택하며, fixture에서 상세 정보를 조회한다. 실제 예약이나 "
        "Calendar 이벤트 생성은 하지 않는다. 반환값에는 준비된 후보의 ID 목록과 개수만 포함된다."
    )
    inputs: dict = {}
    output_type = "string"

    def __init__(self, context: AgentRunContext, collector: EventCollector, album_id: str):
        self.context = context
        self.collector = collector
        self.album_id = album_id
        super().__init__()

    def forward(self) -> str:
        candidates, skipped_ids = policy.prepare_candidates_from_slot_ids(
            self.context.eligible_slot_ids, self.album_id, self.context.schedule_run_id
        )
        self.context.prepared_candidates = candidates
        self.context.prepare_call_count += 1

        result = {
            "preparedCandidateIds": [c.candidateId for c in candidates],
            "preparedCandidateCount": len(candidates),
        }
        message = f"예약 후보 {len(candidates)}건 준비 완료"
        if skipped_ids:
            message += f" (존재하지 않는 슬롯 ID {len(skipped_ids)}건 제외)"
        self.collector.record("TOOL", message, tool=self.name, result=result)
        return json.dumps(result, ensure_ascii=False)
