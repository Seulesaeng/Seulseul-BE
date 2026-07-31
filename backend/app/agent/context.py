# 한 번의 /schedule 실행에 종속된 실행 컨텍스트. 전역 mutable 상태를 쓰지 않는다 —
# 서로 다른 요청의 데이터가 섞이지 않도록 매 실행마다 새로 만든다 (CLAUDE.md 참고).
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class AgentRunContext:
    analysis_id: str
    schedule_run_id: str
    search_start: str
    search_end: str
    busy_times: List[Dict[str, str]] = field(default_factory=list)
    eligible_slot_ids: List[str] = field(default_factory=list)
    excluded_slots: List[Dict[str, Any]] = field(default_factory=list)
    prepared_candidates: List[Any] = field(default_factory=list)
    execution_logs: List[Dict[str, Any]] = field(default_factory=list)
    searched_scopes: List[str] = field(default_factory=list)
    prepare_call_count: int = 0
    # get_calendar_busy_times(Tool) 또는 결정론 fallback이 실제로 사용한 Calendar 모드/사유.
    # 응답의 calendarMode는 요청된 env 값이 아니라 이 값을 그대로 반영해야 한다.
    calendar_mode: Optional[str] = None
    calendar_fallback_reason: Optional[str] = None


class EventCollector:
    """executionLogs의 source of truth. 실제 Tool wrapper가 실행 시점에 기록하며,
    LLM final answer나 자연어 응답을 파싱해서 만들지 않는다."""

    def __init__(self, context: AgentRunContext):
        self.context = context

    def record(
        self,
        type_: str,
        message: str,
        tool: Optional[str] = None,
        result: Optional[dict] = None,
        exclusion_reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        entry = {
            "step": len(self.context.execution_logs) + 1,
            "type": type_,
            "tool": tool,
            # THIS_WEEK/NEXT_WEEK 검색범위 개념은 retry 전용이다. schedule에는 해당 없다.
            "searchScope": None,
            "result": result,
            "message": message,
            "exclusionReason": exclusion_reason,
        }
        self.context.execution_logs.append(entry)
        return entry
