# POST /api/bookings/{candidate_id}/confirm 테스트.
# confirm은 Agent Tool이 아니다 - smolagents/Agent runner를 호출하지 않는다.
from __future__ import annotations

import inspect

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import calendar_service, confirm_service
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


def _create_prepared_candidate(client: TestClient) -> dict:
    """/analyses -> /schedule(결정론 fallback, conftest.py 기본값)로 실제 PREPARED candidate 하나를 만든다."""
    analysis_res = client.post("/api/analyses", json={"albumId": "album-001"})
    assert analysis_res.status_code == 200
    analysis_id = analysis_res.json()["analysisId"]

    schedule_res = client.post(f"/api/analyses/{analysis_id}/schedule")
    assert schedule_res.status_code == 200
    candidates = schedule_res.json()["candidates"]
    assert len(candidates) >= 1
    return candidates[0]


# ---- 1. 존재하지 않는 candidate ----


def test_confirm_unknown_candidate_returns_404(client):
    res = client.post("/api/bookings/cand_unknown/confirm")
    assert res.status_code == 404
    assert res.json()["code"] == "CANDIDATE_NOT_FOUND"


# ---- 2. PREPARED candidate 정상 confirm ----


def test_prepared_candidate_confirms_successfully(client):
    candidate = _create_prepared_candidate(client)

    res = client.post(f"/api/bookings/{candidate['candidateId']}/confirm")
    assert res.status_code == 200
    body = res.json()

    assert body["candidateId"] == candidate["candidateId"]
    assert body["slotId"] == candidate["slotId"]
    assert body["shop"] == candidate["shop"]
    assert body["artist"] == candidate["artist"]
    assert body["service"] == candidate["service"]
    assert body["price"] == candidate["price"]
    assert body["start"] == candidate["start"]
    assert body["end"] == candidate["end"]
    assert body["recommendationReason"] in VALID_REASONS
    assert body["status"] == "CONFIRMED"
    assert body["reservationMode"] == "SIMULATED"
    assert "confirmedAt" in body
    assert body["recheck"]["conflict"] is False
    assert body["recheck"]["conflictingBusyTimes"] == []
    assert body["shopBooking"]["simulated"] is True
    assert body["shopBooking"]["confirmationCode"]
    assert store.candidates[candidate["candidateId"]]["status"] == "CONFIRMED"


# ---- 3~5. 승인 직전 새 충돌 ----


def test_new_busy_time_before_confirm_causes_conflict(client, monkeypatch):
    candidate = _create_prepared_candidate(client)
    overlapping_busy = [{"start": candidate["start"], "end": candidate["end"], "summary": "갑자기 생긴 일정"}]
    monkeypatch.setattr(calendar_service, "get_busy_times", lambda album_id, mode: overlapping_busy)

    res = client.post(f"/api/bookings/{candidate['candidateId']}/confirm")
    assert res.status_code == 409
    assert res.json()["code"] == "CALENDAR_CONFLICT"
    assert res.json()["detail"]["conflictingBusyTimes"] == overlapping_busy


def test_conflict_keeps_candidate_prepared(client, monkeypatch):
    candidate = _create_prepared_candidate(client)
    overlapping_busy = [{"start": candidate["start"], "end": candidate["end"], "summary": "갑자기 생긴 일정"}]
    monkeypatch.setattr(calendar_service, "get_busy_times", lambda album_id, mode: overlapping_busy)

    client.post(f"/api/bookings/{candidate['candidateId']}/confirm")
    assert store.candidates[candidate["candidateId"]]["status"] == "PREPARED"


def test_conflict_does_not_call_create_event(client, monkeypatch):
    monkeypatch.setenv("CALENDAR_MODE", "LIVE")
    candidate = _create_prepared_candidate(client)
    overlapping_busy = [{"start": candidate["start"], "end": candidate["end"], "summary": "갑자기 생긴 일정"}]
    monkeypatch.setattr(calendar_service, "get_busy_times", lambda album_id, mode: overlapping_busy)

    calls = []
    monkeypatch.setattr(calendar_service, "create_event", lambda c, mode: calls.append(1))

    res = client.post(f"/api/bookings/{candidate['candidateId']}/confirm")
    assert res.status_code == 409
    assert calls == []


# ---- 6~7. CACHED 모드 ----


def test_cached_mode_calendar_event_created_false(client, monkeypatch):
    monkeypatch.setenv("CALENDAR_MODE", "CACHED")
    candidate = _create_prepared_candidate(client)

    body = client.post(f"/api/bookings/{candidate['candidateId']}/confirm").json()
    assert body["calendarEvent"]["created"] is False
    assert body["calendarEvent"]["simulated"] is True


def test_cached_mode_event_id_and_html_link_are_null(client, monkeypatch):
    monkeypatch.setenv("CALENDAR_MODE", "CACHED")
    candidate = _create_prepared_candidate(client)

    body = client.post(f"/api/bookings/{candidate['candidateId']}/confirm").json()
    assert body["calendarEvent"]["eventId"] is None
    assert body["calendarEvent"]["htmlLink"] is None


# ---- 8~9. LIVE 모드 ----


def test_live_success_stores_event_id_and_html_link(client, monkeypatch):
    monkeypatch.setenv("CALENDAR_MODE", "LIVE")
    monkeypatch.setattr(
        calendar_service,
        "create_event",
        lambda c, mode: {"eventId": "g_evt_fake_123", "htmlLink": "https://calendar.google.com/event?eid=fake"},
    )
    candidate = _create_prepared_candidate(client)

    res = client.post(f"/api/bookings/{candidate['candidateId']}/confirm")
    assert res.status_code == 200
    body = res.json()
    assert body["calendarEvent"]["created"] is True
    assert body["calendarEvent"]["simulated"] is False
    assert body["calendarEvent"]["eventId"] == "g_evt_fake_123"
    assert body["calendarEvent"]["htmlLink"] == "https://calendar.google.com/event?eid=fake"
    assert store.candidates[candidate["candidateId"]]["status"] == "CONFIRMED"


def test_live_event_creation_failure_keeps_candidate_prepared(client, monkeypatch):
    # 실제 Google Calendar LIVE 연동은 아직 없다 - 몽키패치 없이도 항상 실패한다(정직한 동작).
    monkeypatch.setenv("CALENDAR_MODE", "LIVE")
    candidate = _create_prepared_candidate(client)

    res = client.post(f"/api/bookings/{candidate['candidateId']}/confirm")
    assert res.status_code == 502
    assert res.json()["code"] == "EXTERNAL_SERVICE_ERROR"
    assert store.candidates[candidate["candidateId"]]["status"] == "PREPARED"


# ---- 10~11. 멱등성 ----


def test_duplicate_confirm_returns_existing_result(client):
    candidate = _create_prepared_candidate(client)

    first = client.post(f"/api/bookings/{candidate['candidateId']}/confirm")
    second = client.post(f"/api/bookings/{candidate['candidateId']}/confirm")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()
    assert first.json()["confirmedAt"] == second.json()["confirmedAt"]
    assert first.json()["shopBooking"]["confirmationCode"] == second.json()["shopBooking"]["confirmationCode"]


def test_duplicate_confirm_does_not_call_create_event_again(client, monkeypatch):
    monkeypatch.setenv("CALENDAR_MODE", "LIVE")
    calls = []

    def fake_create_event(c, mode):
        calls.append(1)
        return {"eventId": "g_evt_fake_123", "htmlLink": "https://calendar.google.com/event?eid=fake"}

    monkeypatch.setattr(calendar_service, "create_event", fake_create_event)
    candidate = _create_prepared_candidate(client)

    client.post(f"/api/bookings/{candidate['candidateId']}/confirm")
    client.post(f"/api/bookings/{candidate['candidateId']}/confirm")

    assert len(calls) == 1


# ---- 12. Agent 미실행 ----


def test_confirm_does_not_invoke_agent_runner(client, monkeypatch):
    from app.agent import runner

    calls = []
    monkeypatch.setattr(runner, "run_agent", lambda *a, **k: calls.append(1))

    candidate = _create_prepared_candidate(client)
    client.post(f"/api/bookings/{candidate['candidateId']}/confirm")

    assert calls == []


def test_confirm_service_module_has_no_agent_imports():
    # 주석에 "smolagents를 실행하지 않는다"처럼 설명이 있을 수 있으므로 실제 import 문만 검사한다.
    import_lines = [
        line.strip()
        for line in inspect.getsource(confirm_service).splitlines()
        if line.strip().startswith("import ") or line.strip().startswith("from ")
    ]
    assert not any("smolagents" in line for line in import_lines)
    assert not any("app.agent" in line for line in import_lines)


# ---- 보너스: 상태 이상값, 충돌 함수 재사용 ----


def test_invalid_candidate_status_returns_409(client):
    candidate = _create_prepared_candidate(client)
    store.candidates[candidate["candidateId"]]["status"] = "SOMETHING_ELSE"  # 방어적 케이스

    res = client.post(f"/api/bookings/{candidate['candidateId']}/confirm")
    assert res.status_code == 409
    assert res.json()["code"] == "INVALID_CANDIDATE_STATUS"


def test_confirm_reuses_existing_conflict_primitive_no_new_formula():
    # find_conflicting_busy_times와 is_slot_busy는 같은 _slots_overlap을 공유해야 한다.
    from app.services import policy

    slot_start, slot_end = "2026-08-12T19:00:00+09:00", "2026-08-12T20:30:00+09:00"
    overlapping = [{"start": "2026-08-12T19:15:00+09:00", "end": "2026-08-12T19:45:00+09:00"}]
    non_overlapping = [{"start": "2026-08-12T09:00:00+09:00", "end": "2026-08-12T10:00:00+09:00"}]

    assert policy.is_slot_busy(slot_start, slot_end, overlapping) is True
    assert policy.find_conflicting_busy_times(slot_start, slot_end, overlapping) == overlapping
    assert policy.is_slot_busy(slot_start, slot_end, non_overlapping) is False
    assert policy.find_conflicting_busy_times(slot_start, slot_end, non_overlapping) == []
