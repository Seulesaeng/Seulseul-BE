# POST /api/analyses/{analysis_id}/schedule, POST /api/analyses/{analysis_id}/retry 파이프라인 조립.
# candidates/excludedSlots의 source of truth는 AgentRunContext, executionLogs의 source of truth는
# EventCollector다. LLM final answer 문자열은 후보나 로그 생성에 쓰지 않는다 (CLAUDE.md 참고).
# /schedule과 /retry는 기존 Agent runner(app/agent/runner.py)와 Tool 3개(app/agent/tools.py)를
# 그대로 재사용하며, Agent 실행/fallback 판단 로직(_execute_agent_or_fallback)도 공유한다.
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

from app.agent.context import AgentRunContext, EventCollector
from app.agent.runner import run_agent
from app.models.schemas import Candidate, ExcludedSlot, ExecutionLogEntry, RecommendedWindow
from app.services import calendar_service, nail_shop_service, policy
from app.settings import Modes, get_modes

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


@dataclass
class RetryResult:
    retryRunId: str
    searchWindow: RecommendedWindow
    candidates: List[Candidate]
    excludedSlots: List[ExcludedSlot]
    executionLogs: List[ExecutionLogEntry]
    agentMode: str
    calendarMode: str
    fallbackReason: Optional[str]


def _to_window_bounds(start_date: str, end_date: str) -> Tuple[str, str]:
    return f"{start_date}T00:00:00+09:00", f"{end_date}T23:59:59+09:00"


def _run_deterministic_fallback(
    context: AgentRunContext, collector: EventCollector, album_id: str, calendar_mode: str
) -> None:
    """Agent 없이 정확히 같은 결정론 함수(app/services/policy.py)로 후보를 준비한다.
    Agent Tool(app/agent/tools.py)과 로직을 공유하며 중복 구현하지 않는다."""
    context.busy_times, context.calendar_mode, context.calendar_fallback_reason = calendar_service.get_busy_times(
        album_id, calendar_mode, context.search_start, context.search_end
    )
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


def _execute_agent_or_fallback(
    context: AgentRunContext, collector: EventCollector, album_id: str, modes: Modes
) -> Tuple[str, Optional[str]]:
    """Agent를 실행하거나(LIVE) 결정론 fallback을 실행해 (agentMode, fallbackReason)을 반환한다.
    /schedule과 /retry가 이 함수를 공유하며 fallback 코드를 중복 구현하지 않는다."""
    if modes.agent_mode == "LIVE":
        _final_answer, agent_error = run_agent(context, collector, album_id)
    else:
        agent_error = RuntimeError(f"AGENT_MODE={modes.agent_mode}이라 Agent를 실행하지 않습니다.")

    if agent_error is not None:
        if context.prepared_candidates:
            # A. Agent 실행 중 오류가 있었지만 이미 후보를 준비했다 -> 그 결과를 그대로 쓴다.
            collector.record(
                "SYSTEM",
                "Agent 종료 단계에서 오류가 발생했지만 이미 준비된 후보를 사용합니다.",
                result={"error": str(agent_error)},
            )
            return "LIVE", FALLBACK_REASON_AFTER_PREPARE

        # B. 후보가 하나도 준비되지 않은 채 실패 -> 결정론 fallback 실행.
        collector.record(
            "SYSTEM", "Agent 실행 실패로 결정론 fallback을 사용합니다.", result={"error": str(agent_error)}
        )
        _run_deterministic_fallback(context, collector, album_id, modes.calendar_mode)
        return "FALLBACK", FALLBACK_REASON_BEFORE_PREPARE

    if context.prepared_candidates:
        # C. Agent가 정상 완료.
        return "LIVE", None

    # 방어적 경우: 예외는 없었지만 Agent가 후보를 준비하지 못함 -> fallback.
    collector.record("SYSTEM", "Agent가 후보를 준비하지 못해 결정론 fallback을 사용합니다.", result=None)
    _run_deterministic_fallback(context, collector, album_id, modes.calendar_mode)
    return "FALLBACK", FALLBACK_REASON_BEFORE_PREPARE


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

    agent_mode, fallback_reason = _execute_agent_or_fallback(context, collector, album_id, modes)
    # get_calendar_busy_times(Tool) 또는 결정론 fallback이 실제로 사용한 모드를 그대로 반영한다
    # (context.calendar_mode는 둘 중 하나가 항상 채운다 - get_busy_times가 호출되지 않는 경로는 없다).
    calendar_mode = context.calendar_mode or modes.calendar_mode
    calendar_fallback_reason = context.calendar_fallback_reason

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
        calendarMode=calendar_mode,
        fallbackReason=calendar_service.combine_fallback_reasons(fallback_reason, calendar_fallback_reason),
    )


def run_retry(analysis_id: str, analysis: dict, album_id: str, retry_run_id: str) -> RetryResult:
    """POST /api/analyses/{id}/retry (MVP: searchScope=NEXT_WEEK 전용).

    기존 analysis의 분석 결과(사진 선택, Vision, 시술 주기, careStatus, upcomingEvent, reversePlan)는
    재계산하지 않고 그대로 재사용한다. reversePlan의 날짜에 +7일한 창에서만 새로운 AgentRunContext로
    Agent(또는 fallback)를 다시 실행한다. 기존 schedule 후보는 건드리지 않는다(호출자가 store.candidates에
    이 결과만 추가한다).
    """
    reverse_plan = analysis["reversePlan"]
    next_week_start, next_week_end = policy.calculate_next_week_window(
        reverse_plan["recommendedStart"], reverse_plan["recommendedEnd"]
    )
    search_start, search_end = _to_window_bounds(next_week_start, next_week_end)

    context = AgentRunContext(
        analysis_id=analysis_id,
        schedule_run_id=retry_run_id,
        search_start=search_start,
        search_end=search_end,
    )
    collector = EventCollector(context)
    modes = get_modes()

    collector.record(
        "SYSTEM",
        "다음 주(NEXT_WEEK) 예약 후보 재탐색을 시작합니다.",
        result={"searchStart": search_start, "searchEnd": search_end},
    )

    agent_mode, fallback_reason = _execute_agent_or_fallback(context, collector, album_id, modes)
    calendar_mode = context.calendar_mode or modes.calendar_mode
    calendar_fallback_reason = context.calendar_fallback_reason

    search_window = RecommendedWindow(start=next_week_start, end=next_week_end, basis=reverse_plan["basis"])
    excluded_slots = [ExcludedSlot(**slot) for slot in context.excluded_slots]
    execution_logs = [ExecutionLogEntry(**entry) for entry in context.execution_logs]

    return RetryResult(
        retryRunId=retry_run_id,
        searchWindow=search_window,
        candidates=context.prepared_candidates,
        excludedSlots=excluded_slots,
        executionLogs=execution_logs,
        agentMode=agent_mode,
        calendarMode=calendar_mode,
        fallbackReason=calendar_service.combine_fallback_reasons(fallback_reason, calendar_fallback_reason),
    )
