# POST /api/analyses/{analysis_id}/schedule 통합 테스트.
# 실제 OpenAI API는 호출하지 않는다: LIVE 경로를 검증하는 테스트는
# schedule_service.run_agent를 monkeypatch해서 실제 Tool 클래스를 올바른 순서로 직접
# 호출하는 가짜 에이전트로 대체한다 (LLM의 실제 판단 자체는 backend/spikes/의 두 스파이크로
# 이미 라이브 검증했다).
from __future__ import annotations

import inspect
import json

import pytest
from fastapi.testclient import TestClient

from app.agent.context import AgentRunContext, EventCollector
from app.agent.tools import GetCalendarBusyTimesTool
from app.main import app
from app.services import schedule_service
from app.state import store

VALID_REASONS = {"권장 관리 구간 내", "Calendar 충돌 없음", "선호 네일샵", "빠른 예약 가능 시간"}


@pytest.fixture(autouse=True)
def reset_store():
    store.reset()
    yield
    store.reset()


@pytest.fixture()
def client():
    return TestClient(app)


def _create_analysis(client: TestClient) -> str:
    res = client.post("/api/analyses", json={"albumId": "album-001"})
    assert res.status_code == 200
    return res.json()["analysisId"]


def _fake_agent_run_success(context, collector, album_id):
    """실제 Tool 3개를 올바른 순서로 직접 호출해 정상적인 Agent 행동을 재현한다.
    일부러 틀린 final answer를 반환해 '파싱하지 않음'을 검증할 수 있게 한다."""
    from app.agent.tools import PrepareBookingCandidatesTool, SearchNailShopSlotsTool

    get_busy = GetCalendarBusyTimesTool(context, collector, album_id)
    search = SearchNailShopSlotsTool(context, collector, album_id)
    prepare = PrepareBookingCandidatesTool(context, collector, album_id)

    get_busy.forward(context.search_start, context.search_end)
    search.forward("FAVORITE_SHOP")
    if len(set(context.eligible_slot_ids)) < 3:
        search.forward("ALTERNATIVE_SHOPS")
    prepare.forward()
    return "가짜 최종 답변: 예약 후보 2개가 준비되었습니다.", None  # 개수가 일부러 틀림


def _fake_agent_run_failure_after_prepare(context, collector, album_id):
    _fake_agent_run_success(context, collector, album_id)
    return None, RuntimeError("simulated finalization failure")


def _fake_agent_run_failure_before_prepare(context, collector, album_id):
    return None, RuntimeError("simulated total agent failure")


def _use_fake_live_agent(monkeypatch, fake):
    monkeypatch.setenv("AGENT_MODE", "LIVE")
    monkeypatch.setattr(schedule_service, "run_agent", fake)


# ---- 1~3. 사전 검증 ----


def test_schedule_unknown_analysis_returns_404(client):
    res = client.post("/api/analyses/analysis_unknown/schedule")
    assert res.status_code == 404
    assert res.json()["code"] == "ANALYSIS_NOT_FOUND"


def test_schedule_can_schedule_false_returns_409(client):
    analysis_id = _create_analysis(client)
    store.analyses[analysis_id]["canSchedule"] = False

    res = client.post(f"/api/analyses/{analysis_id}/schedule")
    assert res.status_code == 409
    assert res.json()["code"] == "SCHEDULE_NOT_APPLICABLE"


def test_schedule_missing_reverse_plan_returns_409(client):
    analysis_id = _create_analysis(client)
    store.analyses[analysis_id]["reversePlan"] = None

    res = client.post(f"/api/analyses/{analysis_id}/schedule")
    assert res.status_code == 409
    assert res.json()["code"] == "SCHEDULE_NOT_APPLICABLE"


def test_search_window_always_uses_reverse_plan_not_llm_input():
    # LLM이 다른 기간을 요청해도 실제 계산은 항상 analysis.reversePlan을 사용한다.
    context = AgentRunContext(
        analysis_id="a1",
        schedule_run_id="s1",
        search_start="2026-08-12T00:00:00+09:00",
        search_end="2026-08-13T23:59:59+09:00",
    )
    collector = EventCollector(context)
    tool = GetCalendarBusyTimesTool(context, collector, "album-001")

    result = json.loads(tool.forward(start_datetime="2099-01-01T00:00:00+09:00", end_datetime="2099-01-02T00:00:00+09:00"))
    assert result["start"] == context.search_start
    assert result["end"] == context.search_end


# ---- 4~5. Agent 실행 위치 ----


def test_analysis_service_module_has_no_agent_imports():
    from app.services import analysis_service

    source = inspect.getsource(analysis_service)
    assert "smolagents" not in source
    assert "app.agent" not in source
    assert "run_agent" not in source


def test_agent_runs_only_for_schedule_not_for_analyses(monkeypatch, client):
    calls = []

    def spy(ctx, col, album_id):
        calls.append(True)
        return _fake_agent_run_success(ctx, col, album_id)

    _use_fake_live_agent(monkeypatch, spy)

    analysis_id = _create_analysis(client)
    assert calls == []  # /analyses 시점에는 아직 Agent가 실행되지 않는다

    client.post(f"/api/analyses/{analysis_id}/schedule")
    assert calls == [True]  # /schedule에서 정확히 1회 실행됨


# ---- 6~13. Tool 호출 순서 / 후보 구성 ----


def test_calendar_tool_called_before_shop_search(monkeypatch, client):
    _use_fake_live_agent(monkeypatch, _fake_agent_run_success)
    analysis_id = _create_analysis(client)

    body = client.post(f"/api/analyses/{analysis_id}/schedule").json()
    ordered_tools = [log["tool"] for log in body["executionLogs"] if log["tool"]]

    assert ordered_tools[0] == "get_calendar_busy_times"
    assert ordered_tools[1] == "search_nail_shop_slots"


def test_favorite_shop_searched_before_alternative(monkeypatch, client):
    _use_fake_live_agent(monkeypatch, _fake_agent_run_success)
    analysis_id = _create_analysis(client)

    body = client.post(f"/api/analyses/{analysis_id}/schedule").json()
    search_logs = [log for log in body["executionLogs"] if log["tool"] == "search_nail_shop_slots"]

    assert len(search_logs) == 2  # 선호샵 1개뿐이라 대체샵도 검색됨
    assert search_logs[0]["result"]["searchScope"] == "FAVORITE_SHOP"
    assert search_logs[1]["result"]["searchScope"] == "ALTERNATIVE_SHOPS"


def test_prepare_called_exactly_once(monkeypatch, client):
    _use_fake_live_agent(monkeypatch, _fake_agent_run_success)
    analysis_id = _create_analysis(client)

    body = client.post(f"/api/analyses/{analysis_id}/schedule").json()
    prepare_logs = [log for log in body["executionLogs"] if log["tool"] == "prepare_booking_candidates"]
    assert len(prepare_logs) == 1


def test_candidates_exactly_three_with_one_favorite_two_alternative(monkeypatch, client):
    _use_fake_live_agent(monkeypatch, _fake_agent_run_success)
    analysis_id = _create_analysis(client)

    body = client.post(f"/api/analyses/{analysis_id}/schedule").json()
    candidates = body["candidates"]

    assert len(candidates) == 3
    favorite = [c for c in candidates if c["recommendationReason"] == "선호 네일샵"]
    assert len(favorite) == 1
    assert len(candidates) - len(favorite) == 2

    for candidate in candidates:
        assert isinstance(candidate["price"], int)
        assert candidate["recommendationReason"] in VALID_REASONS
        assert candidate["status"] == "PREPARED"


# ---- 15~16. executionLogs 출처 / final answer 미사용 ----


def test_execution_logs_are_populated_by_tool_execution_itself():
    context = AgentRunContext(
        analysis_id="a1", schedule_run_id="s1",
        search_start="2026-08-12T00:00:00+09:00", search_end="2026-08-13T23:59:59+09:00",
    )
    collector = EventCollector(context)
    assert context.execution_logs == []

    tool = GetCalendarBusyTimesTool(context, collector, "album-001")
    tool.forward(context.search_start, context.search_end)

    assert len(context.execution_logs) == 1  # LLM 응답을 기다리지 않고 즉시 기록됨
    assert context.execution_logs[0]["tool"] == "get_calendar_busy_times"


def test_final_answer_text_is_not_parsed_for_candidates(monkeypatch, client):
    # _fake_agent_run_success는 일부러 틀린 개수("2개")를 최종 답변으로 반환한다.
    _use_fake_live_agent(monkeypatch, _fake_agent_run_success)
    analysis_id = _create_analysis(client)

    body = client.post(f"/api/analyses/{analysis_id}/schedule").json()
    assert len(body["candidates"]) == 3  # 실제 context 기준 3개, final answer의 "2개"와 무관


# ---- 17~18. Agent 오류 처리 (A/B) ----


def test_finalization_failure_after_prepare_keeps_candidates(monkeypatch, client):
    _use_fake_live_agent(monkeypatch, _fake_agent_run_failure_after_prepare)
    analysis_id = _create_analysis(client)

    res = client.post(f"/api/analyses/{analysis_id}/schedule")
    assert res.status_code == 200
    body = res.json()
    assert len(body["candidates"]) == 3
    assert body["agentMode"] == "LIVE"
    assert body["fallbackReason"] == "AGENT_FINALIZATION_FAILED_AFTER_PREPARE"


def test_failure_before_prepare_uses_deterministic_fallback(monkeypatch, client):
    _use_fake_live_agent(monkeypatch, _fake_agent_run_failure_before_prepare)
    analysis_id = _create_analysis(client)

    res = client.post(f"/api/analyses/{analysis_id}/schedule")
    assert res.status_code == 200
    body = res.json()
    assert len(body["candidates"]) == 3  # 결정론 fallback으로 동일하게 준비됨
    assert body["agentMode"] == "FALLBACK"
    assert body["fallbackReason"] == "AGENT_EXECUTION_FAILED_BEFORE_PREPARE"


def test_agent_mode_fallback_env_skips_agent_entirely(monkeypatch, client):
    # conftest.py의 기본값(AGENT_MODE=FALLBACK)을 명시적으로 재확인한다.
    monkeypatch.setenv("AGENT_MODE", "FALLBACK")
    analysis_id = _create_analysis(client)

    res = client.post(f"/api/analyses/{analysis_id}/schedule")
    assert res.status_code == 200
    body = res.json()
    assert len(body["candidates"]) == 3
    assert body["agentMode"] == "FALLBACK"


# ---- 19. 요청 간 컨텍스트 격리 ----


def test_different_schedule_requests_do_not_share_context(monkeypatch, client):
    _use_fake_live_agent(monkeypatch, _fake_agent_run_success)

    analysis_id_1 = _create_analysis(client)
    analysis_id_2 = _create_analysis(client)

    body1 = client.post(f"/api/analyses/{analysis_id_1}/schedule").json()
    body2 = client.post(f"/api/analyses/{analysis_id_2}/schedule").json()

    assert body1["scheduleRunId"] != body2["scheduleRunId"]
    ids1 = {c["candidateId"] for c in body1["candidates"]}
    ids2 = {c["candidateId"] for c in body2["candidates"]}
    assert ids1.isdisjoint(ids2)  # candidateId가 scheduleRunId로 스코프돼 서로 겹치지 않는다

    # 두 실행분 후보가 store에 전부(6건) 살아있고, 서로 덮어쓰지 않았다.
    assert ids1.issubset(store.candidates.keys())
    assert ids2.issubset(store.candidates.keys())


# ---- 20. Agent에게 confirm/Calendar 생성 Tool이 없음 ----


def test_agent_has_exactly_three_tools_no_confirm_or_calendar_creation():
    import app.agent.tools as tools_module
    from smolagents import Tool

    tool_classes = [
        obj
        for obj in vars(tools_module).values()
        if isinstance(obj, type) and issubclass(obj, Tool) and obj is not Tool
    ]
    tool_names = {cls.name for cls in tool_classes}

    assert tool_names == {"get_calendar_busy_times", "search_nail_shop_slots", "prepare_booking_candidates"}
    assert not any("confirm" in name.lower() for name in tool_names)
    assert not any("event" in name.lower() for name in tool_names)


# ---- 21. CALENDAR_MODE=LIVE: get_calendar_busy_times Tool이 실제 LIVE busy time을 사용한다 ----


def test_schedule_uses_live_busy_times_via_agent_tool_and_excludes_conflicting_slot(monkeypatch, client):
    from app.services import calendar_service

    class _FakeFreebusy:
        def query(self, body):
            self._body = body
            return self

        def execute(self):
            # slot_2001(선호샵, 2026-08-12T19:00~20:30)과 정확히 겹치는 busy 구간을 돌려준다.
            # slot_2003(선호샵, 2026-08-13T18:30~20:00)도 CACHED와 동일하게 겹치도록 포함해
            # 두 선호샵 슬롯이 모두 LIVE busy time으로 제외되는 것을 확인한다.
            return {
                "calendars": {
                    "primary": {
                        "busy": [
                            {"start": "2026-08-12T19:00:00+09:00", "end": "2026-08-12T20:30:00+09:00"},
                            {"start": "2026-08-13T18:00:00+09:00", "end": "2026-08-13T19:30:00+09:00"},
                        ]
                    }
                }
            }

    class _FakeLiveService:
        def freebusy(self):
            return _FakeFreebusy()

    monkeypatch.setenv("CALENDAR_MODE", "LIVE")
    monkeypatch.setattr(calendar_service, "_build_live_service", lambda: _FakeLiveService())
    _use_fake_live_agent(monkeypatch, _fake_agent_run_success)

    analysis_id = _create_analysis(client)
    body = client.post(f"/api/analyses/{analysis_id}/schedule").json()

    assert body["calendarMode"] == "LIVE"  # 실제로 LIVE freebusy.query를 써서 성공했다
    excluded_ids = {e["slotId"]: e["exclusionReason"] for e in body["excludedSlots"]}
    assert excluded_ids["slot_2001"] == "CALENDAR_BUSY"  # LIVE busy time으로 실제 제외됨
    candidate_slot_ids = {c["slotId"] for c in body["candidates"]}
    assert "slot_2001" not in candidate_slot_ids
    assert candidate_slot_ids == {"slot_2002", "slot_2004"}  # 대체샵 2개만 남는다
