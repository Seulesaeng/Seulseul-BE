# smolagents ToolCallingAgent 실행. CodeAgent는 쓰지 않는다 (CLAUDE.md 참고).
# candidates/executionLogs의 source of truth는 AgentRunContext다 - 이 모듈이 반환하는
# final_answer 문자열은 참고용일 뿐 후보나 로그 생성에 쓰지 않는다.
from __future__ import annotations

import os
from typing import Optional, Tuple

from app.agent.context import AgentRunContext, EventCollector
from app.agent.tools import GetCalendarBusyTimesTool, PrepareBookingCandidatesTool, SearchNailShopSlotsTool

MAX_STEPS = 7

TASK_TEMPLATE = """당신은 네일 예약 후보를 준비하는 에이전트입니다. 아래 순서를 반드시 지키세요.

1. get_calendar_busy_times를 호출해 캘린더 바쁜 시간을 조회하세요.
   start_datetime="{search_start}", end_datetime="{search_end}"로 호출하세요.
2. search_nail_shop_slots를 search_scope="FAVORITE_SHOP"으로 호출해 선호 네일샵 슬롯을 조회하세요.
3. 반환된 totalEligibleCount가 3보다 작으면, search_nail_shop_slots를 search_scope="ALTERNATIVE_SHOPS"로
   호출해 대체 네일샵 슬롯을 추가로 조회하세요. 3 이상이면 이 단계는 건너뛰세요.
4. 충분한 후보를 찾으면 prepare_booking_candidates를 호출해 최종 후보를 준비하세요. 이 도구는 인자를
   받지 않습니다.
5. prepare_booking_candidates를 호출하는 단계에서는 절대로 final_answer를 함께 호출하지 마세요.
   그 도구 호출 하나만 수행하고 다음 단계로 넘어가세요.
6. prepare_booking_candidates가 성공한 바로 다음 단계에서는 다른 도구를 호출하지 말고 final_answer만
   단독으로 호출해 결과를 요약해 반환하세요.

모든 판단은 도구 호출 결과만 근거로 하고, 직접 값을 추측해 만들어내지 마세요."""


def build_model():
    from smolagents import OpenAIServerModel

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY 환경변수가 설정되어 있지 않습니다.")
    model_id = os.environ.get("AGENT_MODEL")
    if not model_id:
        raise RuntimeError("AGENT_MODEL 환경변수가 설정되어 있지 않습니다.")
    return OpenAIServerModel(model_id=model_id, api_key=api_key)


def run_agent(
    context: AgentRunContext, collector: EventCollector, album_id: str
) -> Tuple[Optional[str], Optional[Exception]]:
    """ToolCallingAgent를 실행한다 (CodeAgent 아님). 성공하면 (final_answer, None)을,
    실패하면 (None, exception)을 반환한다. Tool은 정확히 3개뿐이며, confirm이나 Calendar
    이벤트 생성을 수행하는 Tool은 주지 않는다."""
    from smolagents import ToolCallingAgent

    tools = [
        GetCalendarBusyTimesTool(context, collector, album_id),
        SearchNailShopSlotsTool(context, collector, album_id),
        PrepareBookingCandidatesTool(context, collector, album_id),
    ]
    try:
        model = build_model()
        agent = ToolCallingAgent(tools=tools, model=model, max_steps=MAX_STEPS)
        task = TASK_TEMPLATE.format(search_start=context.search_start, search_end=context.search_end)
        final_answer = agent.run(task)
        return final_answer, None
    except Exception as exc:  # noqa: BLE001 - 상위 schedule_service가 A/B/C로 분기 처리한다.
        return None, exc
