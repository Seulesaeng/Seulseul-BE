# POST /api/analyses/{analysis_id}/retry 테스트 (MVP: searchScope=NEXT_WEEK 전용).
# 기존 Agent runner/Tool 3개를 그대로 재사용한다 - 새 Agent 구현을 만들지 않는다.
# 실제 OpenAI API는 호출하지 않는다 (conftest.py 기본 AGENT_MODE=FALLBACK, 또는 fake agent monkeypatch).
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

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
    """실제 Tool 3개를 올바른 순서로 직접 호출한다 (기존 app/agent/tools.py 그대로 재사용,
    새 Agent 구현 없음). LLM 판단 자체는 backend/spikes/의 두 스파이크로 이미 라이브 검증했다."""
    from app.agent.tools import GetCalendarBusyTimesTool, PrepareBookingCandidatesTool, SearchNailShopSlotsTool

    get_busy = GetCalendarBusyTimesTool(context, collector, album_id)
    search = SearchNailShopSlotsTool(context, collector, album_id)
    prepare = PrepareBookingCandidatesTool(context, collector, album_id)

    get_busy.forward(context.search_start, context.search_end)
    search.forward("FAVORITE_SHOP")
    if len(set(context.eligible_slot_ids)) < 3:
        search.forward("ALTERNATIVE_SHOPS")
    prepare.forward()
    return "가짜 최종 답변", None


def _fake_agent_run_failure_before_prepare(context, collector, album_id):
    return None, RuntimeError("simulated total agent failure")


def _use_fake_live_agent(monkeypatch, fake):
    monkeypatch.setenv("AGENT_MODE", "LIVE")
    monkeypatch.setattr(schedule_service, "run_agent", fake)


# ---- 1~3. 사전 검증 ----


def test_retry_unknown_analysis_returns_404(client):
    res = client.post("/api/analyses/analysis_unknown/retry", json={"searchScope": "NEXT_WEEK"})
    assert res.status_code == 404
    assert res.json()["code"] == "ANALYSIS_NOT_FOUND"


def test_retry_can_schedule_false_returns_409(client):
    analysis_id = _create_analysis(client)
    store.analyses[analysis_id]["canSchedule"] = False

    res = client.post(f"/api/analyses/{analysis_id}/retry", json={"searchScope": "NEXT_WEEK"})
    assert res.status_code == 409
    assert res.json()["code"] == "SCHEDULE_NOT_APPLICABLE"


def test_retry_next_week_only_other_values_return_400(client):
    analysis_id = _create_analysis(client)

    for bad_scope in ("THIS_WEEK", "TODAY", "NEXT_MONTH"):
        res = client.post(f"/api/analyses/{analysis_id}/retry", json={"searchScope": bad_scope})
        assert res.status_code == 400, bad_scope
        assert res.json()["code"] == "INVALID_REQUEST"


# ---- 4~7. 기존 분석 결과 재사용 (재실행하지 않음) ----


def test_retry_does_not_call_vision_service(client, monkeypatch):
    from app.services import vision_service

    analysis_id = _create_analysis(client)  # 실제 /analyses 호출은 먼저 정상적으로 끝낸다.
    calls = []
    monkeypatch.setattr(vision_service, "load_change_signal", lambda *a, **k: calls.append(1))

    res = client.post(f"/api/analyses/{analysis_id}/retry", json={"searchScope": "NEXT_WEEK"})
    assert res.status_code == 200
    assert calls == []


def test_retry_does_not_call_photo_selection(client, monkeypatch):
    from app.services import photo_service

    analysis_id = _create_analysis(client)
    calls = []
    monkeypatch.setattr(photo_service, "sort_photos_by_taken_at", lambda *a, **k: calls.append(1))
    monkeypatch.setattr(photo_service, "select_baseline_photo", lambda *a, **k: calls.append(1))
    monkeypatch.setattr(photo_service, "select_recent_photos", lambda *a, **k: calls.append(1))

    res = client.post(f"/api/analyses/{analysis_id}/retry", json={"searchScope": "NEXT_WEEK"})
    assert res.status_code == 200
    assert calls == []


def test_retry_does_not_recalculate_timing_policy(client, monkeypatch):
    from app.services import policy

    analysis_id = _create_analysis(client)
    calls = []
    monkeypatch.setattr(policy, "calculate_cycle_stats", lambda *a, **k: calls.append(1))
    monkeypatch.setattr(policy, "build_timing", lambda *a, **k: calls.append(1))
    monkeypatch.setattr(policy, "decide_care_status", lambda *a, **k: calls.append(1))

    res = client.post(f"/api/analyses/{analysis_id}/retry", json={"searchScope": "NEXT_WEEK"})
    assert res.status_code == 200
    assert calls == []


def test_retry_does_not_recalculate_reverse_plan(client, monkeypatch):
    from app.services import policy

    analysis_id = _create_analysis(client)
    calls = []
    monkeypatch.setattr(policy, "calculate_reverse_plan", lambda *a, **k: calls.append(1))

    res = client.post(f"/api/analyses/{analysis_id}/retry", json={"searchScope": "NEXT_WEEK"})
    assert res.status_code == 200
    assert calls == []


# ---- 8. NEXT_WEEK 검색 기간 +7일 ----


def test_retry_search_window_is_exactly_plus_seven_days(client):
    analysis_res = client.post("/api/analyses", json={"albumId": "album-001"}).json()
    analysis_id = analysis_res["analysisId"]
    original_start = analysis_res["reversePlan"]["recommendedStart"]
    original_end = analysis_res["reversePlan"]["recommendedEnd"]

    body = client.post(f"/api/analyses/{analysis_id}/retry", json={"searchScope": "NEXT_WEEK"}).json()

    from datetime import date, timedelta

    assert body["searchWindow"]["start"] == (date.fromisoformat(original_start) + timedelta(days=7)).isoformat()
    assert body["searchWindow"]["end"] == (date.fromisoformat(original_end) + timedelta(days=7)).isoformat()
    assert body["searchWindow"]["start"] == "2026-08-19"
    assert body["searchWindow"]["end"] == "2026-08-20"


# ---- 9. 새 retryRunId ----


def test_retry_generates_new_retry_run_id(client):
    analysis_id = _create_analysis(client)

    first = client.post(f"/api/analyses/{analysis_id}/retry", json={"searchScope": "NEXT_WEEK"}).json()
    second = client.post(f"/api/analyses/{analysis_id}/retry", json={"searchScope": "NEXT_WEEK"}).json()

    assert first["retryRunId"] != second["retryRunId"]


# ---- 10~11. 후보 ID 분리 / 기존 후보 유지 ----


def test_retry_candidates_have_different_ids_than_schedule(client, monkeypatch):
    _use_fake_live_agent(monkeypatch, _fake_agent_run_success)
    analysis_id = _create_analysis(client)

    schedule_body = client.post(f"/api/analyses/{analysis_id}/schedule").json()
    retry_body = client.post(f"/api/analyses/{analysis_id}/retry", json={"searchScope": "NEXT_WEEK"}).json()

    schedule_ids = {c["candidateId"] for c in schedule_body["candidates"]}
    retry_ids = {c["candidateId"] for c in retry_body["candidates"]}
    assert schedule_ids.isdisjoint(retry_ids)


def test_retry_does_not_remove_existing_schedule_candidates(client, monkeypatch):
    _use_fake_live_agent(monkeypatch, _fake_agent_run_success)
    analysis_id = _create_analysis(client)

    schedule_body = client.post(f"/api/analyses/{analysis_id}/schedule").json()
    schedule_ids = {c["candidateId"] for c in schedule_body["candidates"]}

    retry_body = client.post(f"/api/analyses/{analysis_id}/retry", json={"searchScope": "NEXT_WEEK"}).json()
    retry_ids = {c["candidateId"] for c in retry_body["candidates"]}

    # 기존 schedule 후보가 삭제/덮어쓰기 되지 않고 그대로 PREPARED로 남아있다.
    for cid in schedule_ids:
        assert store.candidates[cid]["status"] == "PREPARED"
    for cid in retry_ids:
        assert store.candidates[cid]["status"] == "PREPARED"
    assert set(store.candidates.keys()) == schedule_ids | retry_ids


# ---- 12. AgentRunContext가 요청마다 새로 생성됨 ----


def test_retry_creates_fresh_agent_run_context_each_call(client, monkeypatch):
    _use_fake_live_agent(monkeypatch, _fake_agent_run_success)
    analysis_id = _create_analysis(client)

    first = client.post(f"/api/analyses/{analysis_id}/retry", json={"searchScope": "NEXT_WEEK"}).json()
    second = client.post(f"/api/analyses/{analysis_id}/retry", json={"searchScope": "NEXT_WEEK"}).json()

    # 컨텍스트가 재사용(누적)됐다면 두 번째 호출의 executionLogs/candidates가 두 배로 늘어나야 한다.
    # 매번 새 컨텍스트라면 각 응답은 독립적으로 동일한 형태(최대 3개, 자체 실행 로그)를 갖는다.
    assert len(first["candidates"]) <= 3
    assert len(second["candidates"]) <= 3
    assert first["retryRunId"] != second["retryRunId"]
    first_tool_calls = [log for log in first["executionLogs"] if log["tool"] == "prepare_booking_candidates"]
    second_tool_calls = [log for log in second["executionLogs"] if log["tool"] == "prepare_booking_candidates"]
    assert len(first_tool_calls) == 1
    assert len(second_tool_calls) == 1


# ---- 13. NEXT_WEEK Tool 호출 순서 ----


def test_retry_tool_call_order_next_week(client, monkeypatch):
    _use_fake_live_agent(monkeypatch, _fake_agent_run_success)
    analysis_id = _create_analysis(client)

    body = client.post(f"/api/analyses/{analysis_id}/retry", json={"searchScope": "NEXT_WEEK"}).json()
    ordered_tools = [log["tool"] for log in body["executionLogs"] if log["tool"]]

    assert ordered_tools[0] == "get_calendar_busy_times"
    search_logs = [log for log in body["executionLogs"] if log["tool"] == "search_nail_shop_slots"]
    assert len(search_logs) == 2
    assert search_logs[0]["result"]["searchScope"] == "FAVORITE_SHOP"
    assert search_logs[1]["result"]["searchScope"] == "ALTERNATIVE_SHOPS"
    assert ordered_tools[-1] == "prepare_booking_candidates"

    # NEXT_WEEK 슬롯(08-19/08-20)만 조회 대상이었는지 확인.
    for candidate in body["candidates"]:
        assert candidate["start"].startswith("2026-08-19") or candidate["start"].startswith("2026-08-20")


# ---- 14. Agent 실패 시 fallback ----


def test_retry_agent_failure_triggers_deterministic_fallback(client, monkeypatch):
    _use_fake_live_agent(monkeypatch, _fake_agent_run_failure_before_prepare)
    analysis_id = _create_analysis(client)

    res = client.post(f"/api/analyses/{analysis_id}/retry", json={"searchScope": "NEXT_WEEK"})
    assert res.status_code == 200
    body = res.json()
    assert body["agentMode"] == "FALLBACK"
    assert body["fallbackReason"] == "AGENT_EXECUTION_FAILED_BEFORE_PREPARE"
    assert len(body["candidates"]) == 3  # 결정론 fallback도 동일한 정책 함수로 동일하게 준비함


def test_retry_default_conftest_fallback_mode_works(client):
    # conftest.py 기본값(AGENT_MODE=FALLBACK)에서도 정상 동작해야 한다 (별도 monkeypatch 없이).
    analysis_id = _create_analysis(client)
    res = client.post(f"/api/analyses/{analysis_id}/retry", json={"searchScope": "NEXT_WEEK"})
    assert res.status_code == 200
    assert res.json()["agentMode"] == "FALLBACK"


# ---- 15. 후보 최대 3개 ----


def test_retry_candidates_at_most_three(client, monkeypatch):
    _use_fake_live_agent(monkeypatch, _fake_agent_run_success)
    analysis_id = _create_analysis(client)

    body = client.post(f"/api/analyses/{analysis_id}/retry", json={"searchScope": "NEXT_WEEK"}).json()
    assert len(body["candidates"]) == 3
    for candidate in body["candidates"]:
        assert isinstance(candidate["price"], int)
        assert candidate["recommendationReason"] in VALID_REASONS
        assert candidate["status"] == "PREPARED"


# ---- 기존 Agent runner/Tool 재사용 확인 (새 Agent 구현 없음) ----


def test_retry_reuses_same_run_agent_reference_as_schedule():
    from app.agent import runner
    from app.services import schedule_service as svc

    assert svc.run_agent is runner.run_agent
