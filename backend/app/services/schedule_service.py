# POST /api/analyses/{analysis_id}/schedule 파이프라인 조립.
# candidates/excludedSlots의 source of truth는 AgentRunContext, executionLogs의 source of truth는
# EventCollector다. LLM final answer 문자열은 후보나 로그 생성에 쓰지 않는다 (CLAUDE.md 참고).
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

from app.agent.context import AgentRunContext, EventCollector
from app.agent.runner import run_agent
from app.models.schemas import Candidate, ExcludedSlot, ExecutionLogEntry, RecommendedWindow
from app.services import calendar_service, nail_shop_service, policy
from app.settings import get_modes

FALLBACK_REASON_AFTER_PREPARE = "AGENT_FINALIZATION_FAILED_AFTER_PREPARE"
FALLBACK_REASON_BEFORE_PREPARE = "AGENT_EXECUTION_FAILED_BEFORE_PREPARE"


@dataclass
class ScheduleResult:
    scheduleRunId: str
    recommendedWindow: RecommendedWindow
    excludedSlots: List[ExcludedSlot]
    candidates: List[Candidate]
    executionLogs: List[ExecutionLogEntry]
    agentMode: str
    calendarMode: str
    fallbackReason: Optional[str]


def _to_window_bounds(recommended_start_date: str, recommended_end_date: str) -> Tuple[str, str]:
    return f"{recommended_start_date}T00:00:00+09:00", f"{recommended_end_date}T23:59:59+09:00"


def _run_deterministic_fallback(
    context: AgentRunContext, collector: EventCollector, album_id: str, calendar_mode: str
) -> None:
    """Agent 없이 정확히 같은 결정론 함수(app/services/policy.py)로 후보를 준비한다.
    Agent Tool(app/agent/tools.py)과 로직을 공유하며 중복 구현하지 않는다."""
    context.busy_times = calendar_service.get_busy_times(album_id, calendar_mode)
    collector.record("SYSTEM", "결정론 fallback 경로를 실행합니다.", result={"reason": "agent_unavailable_or_failed"})

    for scope in ("FAVORITE_SHOP", "ALTERNATIVE_SHOPS"):
        if len(set(context.eligible_slot_ids)) >= policy.MAX_CANDIDATES:
            break
        slots = nail_shop_service.get_slots_by_scope(album_id, scope)
        eligible, excluded = policy.find_eligible_and_excluded_slots(
            slots, context.busy_times, context.search_start, context.search_end
        )
        new_ids = [s["slotId"] for s in eligible if s["slotId"] not in context.eligible_slot_ids]
        context.eligible_slot_ids.extend(new_ids)
        context.excluded_slots.extend(excluded)
        context.searched_scopes.append(scope)
        collector.record(
            "TOOL",
            f"{scope} 슬롯 조회 완료 (fallback)",
            tool="search_nail_shop_slots",
            result={"eligibleCount": len(new_ids), "excludedCount": len(excluded)},
        )

    candidates, _skipped = policy.prepare_candidates_from_slot_ids(
        context.eligible_slot_ids, album_id, context.schedule_run_id
    )
    context.prepared_candidates = candidates
    context.prepare_call_count += 1
    collector.record(
        "TOOL",
        f"예약 후보 {len(candidates)}건 준비 완료 (fallback)",
        tool="prepare_booking_candidates",
        result={"preparedCandidateCount": len(candidates)},
    )


def run_schedule(analysis_id: str, analysis: dict, album_id: str, schedule_run_id: str) -> ScheduleResult:
    reverse_plan = analysis["reversePlan"]
    search_start, search_end = _to_window_bounds(reverse_plan["recommendedStart"], reverse_plan["recommendedEnd"])

    context = AgentRunContext(
        analysis_id=analysis_id,
        schedule_run_id=schedule_run_id,
        search_start=search_start,
        search_end=search_end,
    )
    collector = EventCollector(context)
    modes = get_modes()

    collector.record(
        "SYSTEM", "예약 후보 탐색을 시작합니다.", result={"searchStart": search_start, "searchEnd": search_end}
    )

    if modes.agent_mode == "LIVE":
        _final_answer, agent_error = run_agent(context, collector, album_id)
    else:
        agent_error = RuntimeError(f"AGENT_MODE={modes.agent_mode}이라 Agent를 실행하지 않습니다.")

    fallback_reason: Optional[str] = None
    if agent_error is not None:
        if context.prepared_candidates:
            # A. Agent 실행 중 오류가 있었지만 이미 후보를 준비했다 -> 그 결과를 그대로 쓴다.
            agent_mode = "LIVE"
            fallback_reason = FALLBACK_REASON_AFTER_PREPARE
            collector.record(
                "SYSTEM",
                "Agent 종료 단계에서 오류가 발생했지만 이미 준비된 후보를 사용합니다.",
                result={"error": str(agent_error)},
            )
        else:
            # B. 후보가 하나도 준비되지 않은 채 실패 -> 결정론 fallback 실행.
            agent_mode = "FALLBACK"
            fallback_reason = FALLBACK_REASON_BEFORE_PREPARE
            collector.record(
                "SYSTEM", "Agent 실행 실패로 결정론 fallback을 사용합니다.", result={"error": str(agent_error)}
            )
            _run_deterministic_fallback(context, collector, album_id, modes.calendar_mode)
    else:
        if context.prepared_candidates:
            # C. Agent가 정상 완료.
            agent_mode = "LIVE"
            fallback_reason = None
        else:
            # 방어적 경우: 예외는 없었지만 Agent가 후보를 준비하지 못함 -> fallback.
            agent_mode = "FALLBACK"
            fallback_reason = FALLBACK_REASON_BEFORE_PREPARE
            collector.record("SYSTEM", "Agent가 후보를 준비하지 못해 결정론 fallback을 사용합니다.", result=None)
            _run_deterministic_fallback(context, collector, album_id, modes.calendar_mode)

    recommended_window = RecommendedWindow(
        start=reverse_plan["recommendedStart"],
        end=reverse_plan["recommendedEnd"],
        basis=reverse_plan["basis"],
    )
    excluded_slots = [ExcludedSlot(**slot) for slot in context.excluded_slots]
    execution_logs = [ExecutionLogEntry(**entry) for entry in context.execution_logs]

    return ScheduleResult(
        scheduleRunId=schedule_run_id,
        recommendedWindow=recommended_window,
        excludedSlots=excluded_slots,
        candidates=context.prepared_candidates,
        executionLogs=execution_logs,
        agentMode=agent_mode,
        calendarMode=modes.calendar_mode,
        fallbackReason=fallback_reason,
    )
