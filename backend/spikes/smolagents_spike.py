#!/usr/bin/env python3
"""
smolagents ToolCallingAgent 두 번째 스파이크.

1차 스파이크(캘린더 조회 -> 단골샵 검색 -> 후보 부족 판단 -> 대체샵 검색 -> 후보 준비)에서
핵심 Agent 판단(Tool 호출 순서)은 성공했다. 이번 스파이크의 목표는 Agent 판단을 다시
검증하는 것이 아니라 **Tool 인터페이스와 종료 과정을 안정화**하는 것이다.

1차 스파이크에서 발견된 문제:
  - prepare_test_candidates와 final_answer를 같은 단계에서 동시 호출
  - Tool 사이에 큰 JSON 문자열을 다시 주고받다 JSON이 손상됨
  - Tool에서는 후보 3개를 준비했지만 최종 자연어 답변에는 2개만 남음

이번 스파이크에서 바뀐 점:
  - 모듈 전역 mutable 상태 대신, 실행마다 새로 만드는 AgentRunContext를 Tool들이 공유한다.
  - Tool은 큰 JSON 문자열을 인자로 받거나 반환하지 않는다. 상세 데이터는 컨텍스트에만 저장하고,
    Agent에게는 개수/ID 목록 같은 짧은 요약만 돌려준다.
  - prepare_test_candidates는 인자를 받지 않고 컨텍스트의 eligible_slot_ids만 읽는다.
  - 프롬프트에 "prepare 다음 단계에서는 final_answer만 단독 호출" 규칙을 명시한다.
  - 애플리케이션의 source of truth는 LLM의 final_answer 문자열이 아니라
    AgentRunContext.prepared_candidates다. execution_logs는 실제 Tool 실행 시점에
    Tool 코드 자신이 기록한다 (LLM 출력 파싱 아님). agent.memory/step_callbacks는
    "final_answer가 단독으로 호출됐는가" 같은 진단 정보를 확인하는 디버깅 보조 용도로만 쓴다.

CodeAgent는 쓰지 않는다. app/*를 import하거나 서비스 API를 수정하지 않는다 - 완전히 독립적이다.

실행 (backend/ 디렉터리에서):
    python spikes/smolagents_spike.py
    (backend/.env 에 OPENAI_API_KEY, AGENT_MODEL이 있으면 자동으로 읽는다)

환경변수:
    OPENAI_API_KEY - 필수. 없으면 즉시 실패한다.
    AGENT_MODEL    - 필수. 값을 추측/하드코딩하지 않는다.
"""
from __future__ import annotations

import json
import os
import sys
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv

KST = timezone(timedelta(hours=9))
SPIKE_DIR = Path(__file__).resolve().parent
BACKEND_DIR = SPIKE_DIR.parent
LOG_PATH = SPIKE_DIR / "spike_run_log.json"

MAX_STEPS = 7
EXPECTED_STEPS = 5
MAX_CANDIDATES = 3

TASK = """당신은 네일 예약 후보를 준비하는 에이전트입니다. 아래 순서를 반드시 지키세요.

1. get_test_calendar_busy_times를 호출해 캘린더 바쁜 시간을 조회하세요.
   start_datetime="2026-08-12T00:00:00+09:00", end_datetime="2026-08-13T23:59:59+09:00"로 호출하세요.
2. search_test_nail_shop_slots를 shop_scope="FAVORITE_SHOP"으로 호출해 선호 네일샵 슬롯을 조회하세요.
3. 지금까지 누적된 가능한 후보 수(eligibleCount 합)가 3개 미만이면, search_test_nail_shop_slots를
   shop_scope="ALTERNATIVE_SHOPS"로 호출해 대체 네일샵 슬롯을 추가로 조회하세요. 3개 이상이면
   이 단계는 건너뛰세요.
4. 충분한 후보를 찾으면 prepare_test_candidates를 호출해 최종 후보를 준비하세요. 이 도구는 인자를
   받지 않습니다.
5. prepare_test_candidates를 호출하는 단계에서는 절대로 final_answer를 함께 호출하지 마세요.
   그 도구 호출 하나만 수행하고 다음 단계로 넘어가세요.
6. prepare_test_candidates가 성공한 바로 다음 단계에서는 다른 도구를 호출하지 말고 final_answer만
   단독으로 호출해 결과를 요약해 반환하세요.

각 단계는 반드시 도구 호출로 수행하고, 직접 값을 추측해 만들어내지 마세요."""

# ---- 고정 fixture (읽기 전용, 이 스파이크 전용 가짜 데이터) ----

FAKE_BUSY_TIMES = [{"start": "2026-08-12T10:00:00+09:00", "end": "2026-08-12T11:00:00+09:00"}]

# 선호샵은 일부러 1개만 있어 Agent가 "3개 미만이라 부족하다"고 판단하고
# 대체샵을 추가로 찾도록 유도한다 (1차 스파이크와 동일한 설계).
SLOT_FIXTURE: dict[str, dict[str, Any]] = {
    "slot_f1": {
        "shop": "프리즘네일 홍대", "artist": "김아라", "service": "GEL_NAIL", "price": 45000,
        "start": "2026-08-12T19:00:00+09:00", "end": "2026-08-12T20:30:00+09:00",
    },
    "slot_a1": {
        "shop": "무드네일 합정", "artist": "박서현", "service": "GEL_NAIL", "price": 40000,
        "start": "2026-08-13T20:00:00+09:00", "end": "2026-08-13T21:30:00+09:00",
    },
    "slot_a2": {
        "shop": "무드네일 합정", "artist": "박서현", "service": "GEL_NAIL", "price": 40000,
        "start": "2026-08-14T14:00:00+09:00", "end": "2026-08-14T15:30:00+09:00",
    },
}
FAVORITE_SLOT_IDS = ["slot_f1"]
ALTERNATIVE_SLOT_IDS = ["slot_a1", "slot_a2"]


def _overlaps(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> bool:
    return a_start < b_end and b_start < a_end


def _is_slot_busy(slot: dict[str, Any], busy_times: list[dict[str, str]]) -> bool:
    slot_start = datetime.fromisoformat(slot["start"])
    slot_end = datetime.fromisoformat(slot["end"])
    for busy in busy_times:
        if _overlaps(slot_start, slot_end, datetime.fromisoformat(busy["start"]), datetime.fromisoformat(busy["end"])):
            return True
    return False


# ---- 실행 컨텍스트: 모듈 전역 mutable 상태 대신, 한 번의 Agent 실행에 종속된다 ----


@dataclass
class AgentRunContext:
    busy_times: list[dict[str, str]] = field(default_factory=list)
    eligible_slot_ids: list[str] = field(default_factory=list)
    excluded_slots: list[dict[str, str]] = field(default_factory=list)
    prepared_candidates: list[dict[str, Any]] = field(default_factory=list)
    execution_logs: list[dict[str, Any]] = field(default_factory=list)

    def log(self, tool_name: str, arguments: dict[str, Any], result: dict[str, Any]) -> None:
        # execution_logs는 Tool wrapper(이 코드) 자신이 실행 시점에 기록한다.
        # LLM의 최종 답변 문자열을 파싱해서 만드는 것이 아니다.
        self.execution_logs.append(
            {"step": len(self.execution_logs) + 1, "tool": tool_name, "arguments": arguments, "result": result}
        )


from smolagents import Tool  # noqa: E402


class GetCalendarBusyTimesTool(Tool):
    name = "get_test_calendar_busy_times"
    description = (
        "지정한 기간 동안 사용자 캘린더의 바쁜 시간대를 조회한다(테스트용 가짜 데이터). "
        "상세 데이터는 내부 컨텍스트에만 저장되고, 반환값에는 조회 건수와 기간만 포함된다."
    )
    inputs = {
        "start_datetime": {"type": "string", "description": "조회 시작 일시 (ISO 8601)."},
        "end_datetime": {"type": "string", "description": "조회 종료 일시 (ISO 8601)."},
    }
    output_type = "string"

    def __init__(self, context: AgentRunContext):
        self.context = context
        super().__init__()

    def forward(self, start_datetime: str, end_datetime: str) -> str:
        busy = list(FAKE_BUSY_TIMES)
        self.context.busy_times = busy
        result = {"count": len(busy), "startDatetime": start_datetime, "endDatetime": end_datetime}
        self.context.log(self.name, {"start_datetime": start_datetime, "end_datetime": end_datetime}, result)
        return json.dumps(result, ensure_ascii=False)


class SearchNailShopSlotsTool(Tool):
    name = "search_test_nail_shop_slots"
    description = (
        "네일샵 예약 가능 슬롯을 조회한다(테스트용 가짜 데이터). 캘린더 바쁜 시간은 인자로 받지 않고 "
        "내부 컨텍스트에서 직접 읽어 충돌 여부를 판정한다. 반환값에는 조회 범위, 가능한 슬롯 ID 목록과 "
        "개수, 제외된 슬롯 개수만 포함되며 슬롯 상세 JSON은 포함되지 않는다."
    )
    inputs = {
        "shop_scope": {"type": "string", "description": '"FAVORITE_SHOP" 또는 "ALTERNATIVE_SHOPS".'},
    }
    output_type = "string"

    def __init__(self, context: AgentRunContext):
        self.context = context
        super().__init__()

    def forward(self, shop_scope: str) -> str:
        if shop_scope == "FAVORITE_SHOP":
            candidate_ids = FAVORITE_SLOT_IDS
        elif shop_scope == "ALTERNATIVE_SHOPS":
            candidate_ids = ALTERNATIVE_SLOT_IDS
        else:
            result = {"error": f"알 수 없는 shop_scope: {shop_scope}"}
            self.context.log(self.name, {"shop_scope": shop_scope}, result)
            return json.dumps(result, ensure_ascii=False)

        eligible: list[str] = []
        excluded: list[dict[str, str]] = []
        for slot_id in candidate_ids:
            slot = SLOT_FIXTURE[slot_id]
            if _is_slot_busy(slot, self.context.busy_times):
                excluded.append({"slotId": slot_id, "reason": "CALENDAR_BUSY"})
            else:
                eligible.append(slot_id)

        self.context.eligible_slot_ids.extend(eligible)
        self.context.excluded_slots.extend(excluded)

        result = {
            "searchScope": shop_scope,
            "eligibleSlotIds": eligible,
            "eligibleCount": len(eligible),
            "excludedCount": len(excluded),
        }
        self.context.log(self.name, {"shop_scope": shop_scope}, result)
        return json.dumps(result, ensure_ascii=False)


class PrepareCandidatesTool(Tool):
    name = "prepare_test_candidates"
    description = (
        "지금까지 조회된 예약 가능 슬롯 ID들로 최종 예약 후보를 준비한다(테스트용). 인자를 받지 않고 "
        "내부 컨텍스트의 eligible_slot_ids만 사용한다. 중복을 제거하고 결정론적으로 정렬한 뒤 최대 "
        f"{MAX_CANDIDATES}개를 선택하며, fixture에서 상세 정보를 조회해 컨텍스트에 저장한다. 반환값에는 "
        "준비된 후보의 ID 목록과 개수만 포함된다."
    )
    inputs: dict[str, Any] = {}
    output_type = "string"

    def __init__(self, context: AgentRunContext):
        self.context = context
        super().__init__()

    def forward(self) -> str:
        unique_sorted_ids = sorted(set(self.context.eligible_slot_ids))  # 결정론 정렬
        selected_ids = unique_sorted_ids[:MAX_CANDIDATES]

        prepared = []
        for slot_id in selected_ids:
            detail = SLOT_FIXTURE[slot_id]
            prepared.append(
                {
                    "candidateId": f"cand_{slot_id}",
                    "slotId": slot_id,
                    "shop": detail["shop"],
                    "artist": detail["artist"],
                    "service": detail["service"],
                    "price": detail["price"],
                    "start": detail["start"],
                    "end": detail["end"],
                    "status": "PREPARED",
                }
            )
        self.context.prepared_candidates = prepared

        result = {
            "preparedCandidateIds": [c["candidateId"] for c in prepared],
            "preparedCandidateCount": len(prepared),
        }
        self.context.log(self.name, {}, result)
        return json.dumps(result, ensure_ascii=False)


def build_model():
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY 환경변수가 설정되어 있지 않습니다.")
    model_id = os.environ.get("AGENT_MODEL")
    if not model_id:
        raise RuntimeError("AGENT_MODEL 환경변수가 설정되어 있지 않습니다.")

    from smolagents import OpenAIServerModel

    return OpenAIServerModel(model_id=model_id, api_key=api_key)


def make_step_recorder():
    """agent.memory 기반 step_callback. 진단(디버깅 보조) 목적으로만 쓴다 -
    final_answer가 다른 Tool과 같은 단계에서 호출됐는지, 어떤 에러가 났는지 확인하는 용도다.
    실제 후보 데이터의 source of truth는 AgentRunContext다."""
    from smolagents import ActionStep

    raw_events: list[dict[str, Any]] = []

    def on_step(step: ActionStep) -> None:
        if not isinstance(step, ActionStep):
            return
        tool_names = [call.name for call in (step.tool_calls or [])]
        raw_events.append(
            {
                "step": step.step_number,
                "toolNames": tool_names,
                "hasError": step.error is not None,
                "errorMessage": str(step.error) if step.error is not None else None,
            }
        )

    return raw_events, on_step


def main() -> int:
    load_dotenv(BACKEND_DIR / ".env")

    import smolagents
    from smolagents import ToolCallingAgent

    context = AgentRunContext()  # 이번 실행 전용. 모듈 전역 mutable 상태를 쓰지 않는다.
    raw_events, on_step = make_step_recorder()

    run_record: dict[str, Any] = {
        "startedAt": datetime.now(KST).isoformat(timespec="seconds"),
        "smolagentsVersion": smolagents.__version__,
        "agentClass": "ToolCallingAgent",
        "model": os.environ.get("AGENT_MODEL"),
        "maxSteps": MAX_STEPS,
        "expectedSteps": EXPECTED_STEPS,
        "finalAnswer": None,
        "error": None,
        "traceback": None,
    }

    try:
        tools = [
            GetCalendarBusyTimesTool(context),
            SearchNailShopSlotsTool(context),
            PrepareCandidatesTool(context),
        ]
        model = build_model()
        agent = ToolCallingAgent(tools=tools, model=model, max_steps=MAX_STEPS, step_callbacks=[on_step])
        final_answer = agent.run(TASK)
        run_record["finalAnswer"] = final_answer
    except Exception:
        run_record["error"] = "".join(traceback.format_exception_only(*sys.exc_info()[:2])).strip()
        run_record["traceback"] = traceback.format_exc()
    finally:
        run_record["endedAt"] = datetime.now(KST).isoformat(timespec="seconds")

    # ---- 파생 지표 계산 ----
    ordered_tool_calls = [entry["tool"] for entry in context.execution_logs]
    prepare_call_count = ordered_tool_calls.count("prepare_test_candidates")
    prepared_candidate_ids = [c["candidateId"] for c in context.prepared_candidates]
    prepared_candidate_count = len(context.prepared_candidates)

    final_answer_steps = [e for e in raw_events if "final_answer" in e["toolNames"]]
    final_answer_called_separately = any(e["toolNames"] == ["final_answer"] for e in final_answer_steps)
    step_errors = [e["errorMessage"] for e in raw_events if e["hasError"]]
    had_any_error = bool(step_errors) or run_record["error"] is not None
    recovered_after_finalization_failure = bool(
        step_errors
        and prepare_call_count == 1
        and prepared_candidate_count == MAX_CANDIDATES
        and final_answer_called_separately
    )

    run_record.update(
        {
            "orderedToolCalls": ordered_tool_calls,
            "prepareCallCount": prepare_call_count,
            "preparedCandidateIds": prepared_candidate_ids,
            "preparedCandidateCount": prepared_candidate_count,
            "finalAnswerCalledSeparately": final_answer_called_separately,
            "recoveredAfterFinalizationFailure": recovered_after_finalization_failure,
            "errors": step_errors,
            # 참고용 상세 정보 (필수 항목은 위까지)
            "excludedSlots": context.excluded_slots,
            "preparedCandidates": context.prepared_candidates,
            "executionLogs": context.execution_logs,
            "rawStepEvents": raw_events,
        }
    )

    LOG_PATH.write_text(json.dumps(run_record, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    # ---- 사람이 읽을 수 있는 요약 (API 키는 출력하지 않는다) ----
    print("=== Tool 호출 순서 (context.execution_logs 기준) ===")
    for i, entry in enumerate(context.execution_logs, start=1):
        print(f"{i}. {entry['tool']}({entry['arguments']}) -> {entry['result']}")

    print()
    print(f"prepare_test_candidates 호출 횟수: {prepare_call_count}")
    print(f"준비된 후보 수: {prepared_candidate_count} / ID: {prepared_candidate_ids}")
    print(f"final_answer 단독 호출 여부: {final_answer_called_separately}")
    print(f"단계 오류 발생: {had_any_error} (errors={step_errors})")
    print(f"final_answer 원문: {run_record['finalAnswer']}")
    print(f"로그 저장 위치: {LOG_PATH}")

    if run_record["error"]:
        print()
        print(f"[FAIL] {run_record['error']}")
        print(run_record["traceback"])
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
