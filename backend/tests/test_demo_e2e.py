# backend/scripts/demo_e2e.py 테스트. HTTP는 항상 httpx.MockTransport로 mock한다 -
# 실제 서버나 실제 Google Calendar를 호출하지 않는다.
from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import demo_e2e  # noqa: E402


ANALYSIS_BODY = {
    "analysisId": "analysis_test_1",
    "albumId": "album-001",
    "careStatus": {"status": "NOW", "needsCareNow": True, "reasonCodes": [], "message": "m", "decidedBy": "POLICY", "decidedAt": "t"},
    "upcomingEvent": {"eventId": "evt1", "title": "친구 결혼식", "date": "2026-08-15", "source": "cached"},
    "reversePlan": {"recommendedStart": "2026-08-12", "recommendedEnd": "2026-08-13", "basis": "UPCOMING_EVENT", "reason": "r"},
    "canSchedule": True,
    "calendarMode": "CACHED",
    "fallbackReason": None,
}

SCHEDULE_BODY = {
    "analysisId": "analysis_test_1",
    "scheduleRunId": "schedule_test_1",
    "agentMode": "FALLBACK",
    "calendarMode": "CACHED",
    "candidates": [
        {
            "candidateId": "schedule_test_1_slot_2002",
            "slotId": "slot_2002",
            "shop": "무드네일 합정",
            "artist": "이나연",
            "service": "GEL_NAIL",
            "price": 42000,
            "start": "2026-08-12T14:00:00+09:00",
            "end": "2026-08-12T15:30:00+09:00",
            "recommendationReason": "빠른 예약 가능 시간",
            "status": "PREPARED",
        }
    ],
    "excludedSlots": [
        {"slotId": "slot_2003", "shop": "프리즘네일 홍대", "start": "s", "end": "e", "exclusionReason": "CALENDAR_BUSY"}
    ],
}

RETRY_BODY = {
    "analysisId": "analysis_test_1",
    "retryRunId": "retry_test_1",
    "agentMode": "FALLBACK",
    "calendarMode": "CACHED",
    "candidates": [],
    "excludedSlots": [],
}

CONFIRM_BODY = {
    "status": "CONFIRMED",
    "calendarMode": "CACHED",
    "calendarEvent": {"created": False, "simulated": True, "eventId": None, "htmlLink": None},
}


def _make_client(handler):
    return httpx.Client(base_url="http://test-server", transport=httpx.MockTransport(handler))


def _handler(calls, *, confirm_status=200, confirm_body=None):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path, request.content.decode() if request.content else None))

        if request.url.path == "/api/health":
            return httpx.Response(200, json={"status": "ok", "visionMode": "CACHED", "agentMode": "FALLBACK", "calendarMode": "CACHED"})
        if request.url.path == "/api/albums/connect":
            return httpx.Response(200, json={"albumId": "album-001", "photos": [{}] * 6})
        if request.url.path == "/api/analyses":
            return httpx.Response(200, json=ANALYSIS_BODY)
        if request.url.path == "/api/analyses/analysis_test_1/schedule":
            return httpx.Response(200, json=SCHEDULE_BODY)
        if request.url.path == "/api/analyses/analysis_test_1/retry":
            return httpx.Response(200, json=RETRY_BODY)
        if request.url.path == "/api/bookings/schedule_test_1_slot_2002/confirm":
            return httpx.Response(confirm_status, json=confirm_body or CONFIRM_BODY)
        raise AssertionError(f"예상치 못한 요청: {request.method} {request.url.path}")

    return handler


# ---- 호출 순서 / ID 전파 ----


def test_default_run_calls_in_correct_order_and_propagates_ids(capsys):
    calls = []
    client = _make_client(_handler(calls))

    exit_code = demo_e2e.run_demo(client, confirm=False, retry=False)

    assert exit_code == 0
    paths = [path for _method, path, _body in calls]
    assert paths == [
        "/api/health",
        "/api/albums/connect",
        "/api/analyses",
        "/api/analyses/analysis_test_1/schedule",
    ]
    # analyses 요청 바디에 albums/connect가 돌려준 albumId가 그대로 들어갔는지 확인.
    analyses_body = calls[2][2]
    assert '"albumId": "album-001"' in analyses_body or '"albumId":"album-001"' in analyses_body


def test_default_run_does_not_call_confirm(capsys):
    calls = []
    client = _make_client(_handler(calls))

    demo_e2e.run_demo(client, confirm=False, retry=False)

    assert all(not path.endswith("/confirm") for _m, path, _b in calls)
    out = capsys.readouterr().out
    assert "--confirm이 없어 여기서 멈춥니다" in out


def test_confirm_flag_calls_confirm_with_selected_candidate_id(capsys):
    calls = []
    client = _make_client(_handler(calls))

    exit_code = demo_e2e.run_demo(client, confirm=True, retry=False)

    assert exit_code == 0
    paths = [path for _m, path, _b in calls]
    assert paths[-1] == "/api/bookings/schedule_test_1_slot_2002/confirm"


def test_retry_flag_calls_retry_with_analysis_id(capsys):
    calls = []
    client = _make_client(_handler(calls))

    demo_e2e.run_demo(client, confirm=False, retry=True)

    paths = [path for _m, path, _b in calls]
    assert "/api/analyses/analysis_test_1/retry" in paths
    # retry는 schedule 다음, confirm 이전에 호출된다(이번 호출에서는 confirm이 아예 없음).
    assert paths.index("/api/analyses/analysis_test_1/retry") > paths.index("/api/analyses/analysis_test_1/schedule")


def test_without_retry_flag_retry_is_not_called():
    calls = []
    client = _make_client(_handler(calls))

    demo_e2e.run_demo(client, confirm=False, retry=False)

    assert all("/retry" not in path for _m, path, _b in calls)


# ---- canSchedule=false면 schedule을 호출하지 않는다 ----


def test_can_schedule_false_stops_before_schedule():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.url.path == "/api/health":
            return httpx.Response(200, json={"status": "ok", "visionMode": "CACHED", "agentMode": "FALLBACK", "calendarMode": "CACHED"})
        if request.url.path == "/api/albums/connect":
            return httpx.Response(200, json={"albumId": "album-001", "photos": [{}] * 6})
        if request.url.path == "/api/analyses":
            body = dict(ANALYSIS_BODY)
            body["canSchedule"] = False
            body["upcomingEvent"] = None
            body["reversePlan"] = None
            return httpx.Response(200, json=body)
        raise AssertionError(f"예상치 못한 요청: {request.url.path}")

    client = httpx.Client(base_url="http://test-server", transport=httpx.MockTransport(handler))
    exit_code = demo_e2e.run_demo(client, confirm=False, retry=False)

    assert exit_code == 0
    assert all("/schedule" not in path for _m, path in calls)


# ---- HTTP 오류: status code + 안전한 code/message만 출력 ----


def test_confirm_error_prints_status_and_safe_message_only(capsys):
    calls = []
    error_body = {"code": "CALENDAR_CONFLICT", "message": "겹칩니다", "detail": {"secretish": "not really secret but still detail"}}
    client = _make_client(_handler(calls, confirm_status=409, confirm_body=error_body))

    exit_code = demo_e2e.run_demo(client, confirm=True, retry=False)

    assert exit_code == 1
    err = capsys.readouterr().err
    assert "409" in err
    assert "CALENDAR_CONFLICT" in err
    assert "겹칩니다" in err


def test_connection_error_is_handled_safely(capsys):
    def handler(request: httpx.Request):
        raise httpx.ConnectError("connection refused", request=request)

    client = httpx.Client(base_url="http://test-server", transport=httpx.MockTransport(handler))
    exit_code = demo_e2e.run_demo(client, confirm=False, retry=False)

    assert exit_code == 1
    err = capsys.readouterr().err
    assert "서버에 연결할 수 없습니다" in err


# ---- 출력에 민감정보가 없는지 ----


def test_output_never_contains_secrets_or_full_google_response(capsys):
    calls = []
    confirm_body_with_link = {
        "status": "CONFIRMED",
        "calendarMode": "LIVE",
        "calendarEvent": {
            "created": True,
            "simulated": False,
            "eventId": "sls-fake-event-id",
            "htmlLink": "https://www.google.com/calendar/event?eid=fake",
        },
    }
    client = _make_client(_handler(calls, confirm_body=confirm_body_with_link))

    demo_e2e.run_demo(client, confirm=True, retry=False)

    out = capsys.readouterr().out
    for secret in ("access_token", "refresh_token", "client_secret", "Authorization", "Bearer "):
        assert secret not in out


# ---- base_url()/DEMO_BASE_URL ----


def test_base_url_defaults_when_unset(monkeypatch):
    monkeypatch.delenv("DEMO_BASE_URL", raising=False)
    assert demo_e2e.base_url() == demo_e2e.DEFAULT_BASE_URL


def test_base_url_honors_env_override(monkeypatch):
    monkeypatch.setenv("DEMO_BASE_URL", "http://example.com:9000")
    assert demo_e2e.base_url() == "http://example.com:9000"
