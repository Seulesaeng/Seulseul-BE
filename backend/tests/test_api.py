# 6개 목(mock) API 테스트. docs/api-contract.md 기준.
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
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


# ---- 1. GET /api/health ----


def test_health_ok(client):
    res = client.get("/api/health")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert body["visionMode"] in {"LIVE", "CACHED"}
    assert body["agentMode"] in {"LIVE", "FALLBACK", "CACHED"}
    assert body["calendarMode"] in {"LIVE", "CACHED"}
    assert "serverTime" in body


# ---- 2. POST /api/albums/connect ----


def test_albums_connect_returns_six_photos_with_full_urls(client):
    res = client.post("/api/albums/connect", json={"source": "DEMO"})
    assert res.status_code == 200
    body = res.json()
    assert body["albumId"] == "album-001"
    assert body["sourceMode"] == "DEMO"
    assert body["scanSummary"]["totalPhotos"] == 6
    assert len(body["photos"]) == 6
    for photo in body["photos"]:
        assert photo["imageUrl"].startswith("http")
        assert "/demo-album/" in photo["imageUrl"]


def test_albums_connect_invalid_source_returns_400(client):
    res = client.post("/api/albums/connect", json={"source": "REAL"})
    assert res.status_code == 400
    assert res.json()["code"] == "INVALID_REQUEST"


# ---- 3. POST /api/analyses ----


def test_analyses_response_shape(client):
    res = client.post("/api/analyses", json={"albumId": "album-001"})
    assert res.status_code == 200
    body = res.json()

    expected_fields = {
        "analysisId",
        "albumId",
        "selectedPhotos",
        "changeSignal",
        "timing",
        "careStatus",
        "upcomingEvent",
        "reversePlan",
        "calendarMarkers",
        "evidenceLogs",
        "canSchedule",
        "visionMode",
        "calendarMode",
        "fallbackReason",
    }
    assert expected_fields.issubset(body.keys())

    # Agent는 이 API에서 실행되지 않는다 -> candidates/executionLogs/agentMode 없음
    assert "candidates" not in body
    assert "executionLogs" not in body
    assert "agentMode" not in body

    assert len(body["selectedPhotos"]) == 4
    assert body["careStatus"]["status"] == "NOW"
    assert body["canSchedule"] is True
    assert body["upcomingEvent"]["title"] == "친구 결혼식"
    assert body["reversePlan"]["basis"] == "UPCOMING_EVENT"
    assert len(body["calendarMarkers"]) >= 1
    assert len(body["evidenceLogs"]) >= 1

    evidence_types = [log["type"] for log in body["evidenceLogs"]]
    assert set(evidence_types) <= {"SYSTEM", "VISION", "POLICY", "CALENDAR"}
    assert "VISION" in evidence_types
    assert "CALENDAR" in evidence_types


def test_analyses_does_not_populate_candidate_store(client):
    assert store.candidates == {}
    client.post("/api/analyses", json={"albumId": "album-001"})
    assert store.candidates == {}
    assert store.schedules == {}


def test_analyses_unknown_album_returns_404(client):
    res = client.post("/api/analyses", json={"albumId": "album-999"})
    assert res.status_code == 404
    assert res.json()["code"] == "ALBUM_NOT_FOUND"


def test_analyses_missing_album_id_returns_400(client):
    res = client.post("/api/analyses", json={})
    assert res.status_code == 400
    assert res.json()["code"] == "INVALID_REQUEST"


# ---- 4. POST /api/analyses/{analysis_id}/schedule ----


def test_schedule_returns_candidates_with_price_and_reason(client):
    analysis_id = _create_analysis(client)
    res = client.post(f"/api/analyses/{analysis_id}/schedule")
    assert res.status_code == 200
    body = res.json()

    assert body["analysisId"] == analysis_id
    assert "scheduleRunId" in body
    assert 1 <= len(body["candidates"]) <= 3
    for candidate in body["candidates"]:
        assert isinstance(candidate["price"], int)
        assert candidate["recommendationReason"] in VALID_REASONS
        assert candidate["status"] == "PREPARED"

    assert len(body["executionLogs"]) >= 1
    assert any(log["type"] == "TOOL" for log in body["executionLogs"])
    tool_names = {log["tool"] for log in body["executionLogs"] if log["tool"]}
    assert tool_names <= {"get_calendar_busy_times", "search_nail_shop_slots", "prepare_booking_candidates"}
    assert "agentMode" in body
    assert "visionMode" not in body  # schedule 응답에는 visionMode가 없다


def test_schedule_unknown_analysis_returns_404(client):
    res = client.post("/api/analyses/analysis_unknown/schedule")
    assert res.status_code == 404
    assert res.json()["code"] == "ANALYSIS_NOT_FOUND"


# ---- 5. POST /api/analyses/{analysis_id}/retry ----


def test_retry_returns_next_week_candidates(client):
    analysis_id = _create_analysis(client)
    client.post(f"/api/analyses/{analysis_id}/schedule")

    res = client.post(f"/api/analyses/{analysis_id}/retry", json={"searchScope": "NEXT_WEEK"})
    assert res.status_code == 200
    body = res.json()
    assert body["recommendedWindow"]["start"] == "2026-08-19"
    assert 1 <= len(body["candidates"]) <= 3
    for candidate in body["candidates"]:
        assert candidate["start"].startswith("2026-08-19") or candidate["start"].startswith("2026-08-20")


def test_retry_unknown_analysis_returns_404(client):
    res = client.post("/api/analyses/analysis_unknown/retry", json={"searchScope": "NEXT_WEEK"})
    assert res.status_code == 404
    assert res.json()["code"] == "ANALYSIS_NOT_FOUND"


def test_retry_invalid_search_scope_returns_400(client):
    analysis_id = _create_analysis(client)
    res = client.post(f"/api/analyses/{analysis_id}/retry", json={"searchScope": "TODAY"})
    assert res.status_code == 400
    assert res.json()["code"] == "INVALID_REQUEST"


# ---- 6. POST /api/bookings/{candidate_id}/confirm ----


def test_confirm_success_with_cached_calendar_mode(client, monkeypatch):
    monkeypatch.setenv("CALENDAR_MODE", "CACHED")
    analysis_id = _create_analysis(client)
    schedule = client.post(f"/api/analyses/{analysis_id}/schedule").json()
    candidate_id = schedule["candidates"][0]["candidateId"]

    res = client.post(f"/api/bookings/{candidate_id}/confirm")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "CONFIRMED"
    assert body["reservationMode"] == "SIMULATED"
    assert body["recheck"]["conflict"] is False
    assert body["shopBooking"]["simulated"] is True
    assert body["calendarEvent"]["created"] is False
    assert body["calendarEvent"]["simulated"] is True
    assert body["calendarEvent"]["eventId"] is None
    assert body["calendarEvent"]["htmlLink"] is None
    assert body["calendarMode"] == "CACHED"


def test_confirm_live_calendar_mode_currently_fails_honestly(client, monkeypatch):
    # 실제 Google Calendar LIVE 연동은 아직 없다(CLAUDE.md 9단계) - 성공한 척 가짜 이벤트를
    # 만들지 않고, 정직하게 EXTERNAL_SERVICE_ERROR로 실패하며 candidate는 PREPARED로 남는다.
    monkeypatch.setenv("CALENDAR_MODE", "LIVE")
    analysis_id = _create_analysis(client)
    schedule = client.post(f"/api/analyses/{analysis_id}/schedule").json()
    candidate_id = schedule["candidates"][0]["candidateId"]

    res = client.post(f"/api/bookings/{candidate_id}/confirm")
    assert res.status_code == 502
    assert res.json()["code"] == "EXTERNAL_SERVICE_ERROR"
    assert store.candidates[candidate_id]["status"] == "PREPARED"


def test_confirm_unknown_candidate_returns_404(client):
    res = client.post("/api/bookings/cand_unknown/confirm")
    assert res.status_code == 404
    assert res.json()["code"] == "CANDIDATE_NOT_FOUND"


def test_confirm_already_confirmed_candidate_returns_same_result(client):
    # 멱등: 두 번째 confirm은 실패하지 않고 첫 번째와 동일한 결과를 그대로 반환한다.
    analysis_id = _create_analysis(client)
    schedule = client.post(f"/api/analyses/{analysis_id}/schedule").json()
    candidate_id = schedule["candidates"][0]["candidateId"]

    first = client.post(f"/api/bookings/{candidate_id}/confirm")
    assert first.status_code == 200

    second = client.post(f"/api/bookings/{candidate_id}/confirm")
    assert second.status_code == 200
    assert second.json() == first.json()
