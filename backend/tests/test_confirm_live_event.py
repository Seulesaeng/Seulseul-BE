# POST /api/bookings/{candidate_id}/confirm의 CALENDAR_MODE=LIVE 이벤트 생성(events().insert()) 테스트.
# Google API(googleapiclient)와 파일 I/O는 항상 mock한다 - 실제 네트워크 호출이나 실제
# Google Calendar 이벤트 생성은 하지 않는다. conftest.py의 _safe_calendar_live_by_default가
# 기본적으로 실제 인증도 차단한다.
from __future__ import annotations

import re

import httplib2
import pytest
from fastapi.testclient import TestClient
from googleapiclient.errors import HttpError

from app.main import app
from app.services import calendar_service, confirm_service
from app.state import store

EVENT_ID_CHARSET = re.compile(r"^[0-9a-v]+$")


@pytest.fixture(autouse=True)
def reset_store():
    store.reset()
    yield
    store.reset()


@pytest.fixture()
def client():
    return TestClient(app)


def _create_prepared_candidate(client: TestClient) -> dict:
    analysis_res = client.post("/api/analyses", json={"albumId": "album-001"})
    assert analysis_res.status_code == 200
    analysis_id = analysis_res.json()["analysisId"]

    schedule_res = client.post(f"/api/analyses/{analysis_id}/schedule")
    assert schedule_res.status_code == 200
    candidates = schedule_res.json()["candidates"]
    assert len(candidates) >= 1
    return candidates[0]


def _http_error(status: int, message: str = "error") -> HttpError:
    resp = httplib2.Response({"status": status})
    return HttpError(resp, f'{{"error": {{"message": "{message}"}}}}'.encode())


class _FakeExecutable:
    def __init__(self, result=None, error=None):
        self._result = result
        self._error = error

    def execute(self):
        if self._error is not None:
            raise self._error
        return self._result


class _FakeEventsAPI:
    def __init__(self, calls, insert_response=None, get_response=None):
        self._calls = calls
        self._insert_response = insert_response
        self._get_response = get_response

    def insert(self, calendarId, body):
        self._calls.append(("insert", calendarId, body))
        return self._insert_response

    def get(self, calendarId, eventId):
        self._calls.append(("get", calendarId, eventId))
        return self._get_response

    def list(self, **kwargs):
        raise AssertionError("create_event는 events().list()를 호출하면 안 된다.")


class _FakeLiveService:
    def __init__(self, calls, insert_response=None, get_response=None):
        self._events = _FakeEventsAPI(calls, insert_response, get_response)

    def events(self):
        return self._events

    def calendars(self):
        raise AssertionError("calendars()는 호출되면 안 된다 (현재 SCOPES로는 403).")

    def freebusy(self):
        raise AssertionError("create_event 단위 테스트에서 freebusy()가 호출되면 안 된다.")


def _candidate(candidate_id="schedule_1_slot_2001", **overrides):
    base = {
        "candidateId": candidate_id,
        "slotId": "slot_2001",
        "shop": "프리즘네일 홍대",
        "artist": "김아라",
        "start": "2026-08-12T19:00:00+09:00",
        "end": "2026-08-12T20:30:00+09:00",
        "analysisId": "analysis_1",
    }
    base.update(overrides)
    return base


# ---- calendar_service.create_event(): events.insert body 정확성 ----


def test_insert_called_with_exact_calendar_id_and_body(monkeypatch):
    calls = []
    service = _FakeLiveService(calls, insert_response=_FakeExecutable({"id": "evt1", "htmlLink": "https://x/evt1"}))
    monkeypatch.setattr(calendar_service, "_build_live_service", lambda: service)

    result = calendar_service.create_event(_candidate(), "LIVE")

    assert result == {"eventId": "evt1", "htmlLink": "https://x/evt1"}
    name, cal_id, body = calls[0]
    assert name == "insert"
    assert cal_id == "primary"
    assert body["summary"] == "슬슬 · 네일 예약"
    assert body["description"] == (
        "슬슬 데모에서 생성한 캘린더 일정입니다.\n실제 네일샵 예약 완료 여부는 해당 매장에 별도로 확인해야 합니다."
    )
    assert body["location"] == "프리즘네일 홍대"
    assert body["start"] == {"dateTime": "2026-08-12T19:00:00+09:00", "timeZone": "Asia/Seoul"}
    assert body["end"] == {"dateTime": "2026-08-12T20:30:00+09:00", "timeZone": "Asia/Seoul"}
    assert body["extendedProperties"]["private"] == {
        "source": "seulseul-demo",
        "candidateId": "schedule_1_slot_2001",
        "analysisId": "analysis_1",
    }


def test_insert_uses_custom_calendar_id_and_app_timezone(monkeypatch):
    monkeypatch.setenv("GOOGLE_CALENDAR_ID", "team@example.com")
    monkeypatch.setenv("APP_TIMEZONE", "UTC")
    calls = []
    service = _FakeLiveService(calls, insert_response=_FakeExecutable({"id": "evt1", "htmlLink": "https://x/evt1"}))
    monkeypatch.setattr(calendar_service, "_build_live_service", lambda: service)

    calendar_service.create_event(_candidate(), "LIVE")

    name, cal_id, body = calls[0]
    assert cal_id == "team@example.com"
    assert body["start"]["timeZone"] == "UTC"
    assert body["end"]["timeZone"] == "UTC"


def test_location_uses_shop_and_address_when_present(monkeypatch):
    calls = []
    service = _FakeLiveService(calls, insert_response=_FakeExecutable({"id": "evt1", "htmlLink": "x"}))
    monkeypatch.setattr(calendar_service, "_build_live_service", lambda: service)

    calendar_service.create_event(_candidate(address="서울 마포구 어딘가 1층"), "LIVE")

    body = calls[0][2]
    assert body["location"] == "프리즘네일 홍대 서울 마포구 어딘가 1층"


def test_no_attendees_or_conference_data_in_body(monkeypatch):
    calls = []
    service = _FakeLiveService(calls, insert_response=_FakeExecutable({"id": "evt1", "htmlLink": "x"}))
    monkeypatch.setattr(calendar_service, "_build_live_service", lambda: service)

    calendar_service.create_event(_candidate(), "LIVE")

    body = calls[0][2]
    assert "attendees" not in body
    assert "conferenceData" not in body
    assert "reminders" not in body  # 알림 메일도 별도로 구성하지 않는다


# ---- deterministic event ID ----


def test_deterministic_event_id_is_stable_for_same_candidate():
    id1 = calendar_service._deterministic_event_id("schedule_1_slot_2001")
    id2 = calendar_service._deterministic_event_id("schedule_1_slot_2001")
    assert id1 == id2


def test_deterministic_event_id_differs_for_different_candidates():
    id1 = calendar_service._deterministic_event_id("schedule_1_slot_2001")
    id2 = calendar_service._deterministic_event_id("schedule_1_slot_2002")
    assert id1 != id2


def test_deterministic_event_id_only_uses_google_allowed_characters():
    for candidate_id in ("schedule_1_slot_2001", "retry-abc_123", "weird!!id@@2026"):
        event_id = calendar_service._deterministic_event_id(candidate_id)
        assert EVENT_ID_CHARSET.match(event_id), event_id
        assert event_id.startswith("sls")
        assert 5 <= len(event_id) <= 1024


# ---- 409 dedup: events.get으로 같은 candidate 재사용 / 다른 candidate면 실패 ----


def test_insert_409_reuses_existing_event_for_same_candidate(monkeypatch):
    calls = []
    insert_error = _http_error(409)
    get_response = _FakeExecutable(
        {
            "id": "sls-existing",
            "htmlLink": "https://x/existing",
            "extendedProperties": {"private": {"candidateId": "schedule_1_slot_2001"}},
        }
    )
    service = _FakeLiveService(calls, insert_response=_FakeExecutable(error=insert_error), get_response=get_response)
    monkeypatch.setattr(calendar_service, "_build_live_service", lambda: service)

    result = calendar_service.create_event(_candidate(), "LIVE")

    assert result == {"eventId": "sls-existing", "htmlLink": "https://x/existing"}
    call_names = [c[0] for c in calls]
    assert call_names == ["insert", "get"]


def test_insert_409_with_mismatched_candidate_id_raises(monkeypatch):
    calls = []
    insert_error = _http_error(409)
    get_response = _FakeExecutable(
        {
            "id": "sls-existing",
            "htmlLink": "https://x/existing",
            "extendedProperties": {"private": {"candidateId": "OTHER_CANDIDATE"}},
        }
    )
    service = _FakeLiveService(calls, insert_response=_FakeExecutable(error=insert_error), get_response=get_response)
    monkeypatch.setattr(calendar_service, "_build_live_service", lambda: service)

    with pytest.raises(RuntimeError, match="다른 candidate"):
        calendar_service.create_event(_candidate(), "LIVE")


def test_insert_non_409_error_is_not_treated_as_success(monkeypatch):
    calls = []
    insert_error = _http_error(500)
    service = _FakeLiveService(calls, insert_response=_FakeExecutable(error=insert_error))
    monkeypatch.setattr(calendar_service, "_build_live_service", lambda: service)

    with pytest.raises(HttpError):
        calendar_service.create_event(_candidate(), "LIVE")

    assert [c[0] for c in calls] == ["insert"]  # get()은 호출되지 않는다(409가 아니므로)


# ---- confirm_service 통합: 처리 순서, 실패 시 상태, 응답 필드 ----


def _mock_live_busy(monkeypatch, busy=None, mode="LIVE", reason=None):
    monkeypatch.setattr(
        calendar_service, "get_busy_times", lambda album_id, m, start, end: (busy or [], mode, reason)
    )


def test_cached_confirm_never_calls_create_event(client, monkeypatch):
    monkeypatch.setenv("CALENDAR_MODE", "CACHED")

    def _fail_if_called(candidate, mode):
        raise AssertionError("CACHED 모드에서는 create_event가 호출되면 안 된다.")

    monkeypatch.setattr(calendar_service, "create_event", _fail_if_called)
    candidate = _create_prepared_candidate(client)

    res = client.post(f"/api/bookings/{candidate['candidateId']}/confirm")
    assert res.status_code == 200
    assert res.json()["calendarEvent"] == {
        "created": False,
        "simulated": True,
        "eventId": None,
        "htmlLink": None,
    }


def test_live_confirm_rechecks_busy_then_calls_insert(client, monkeypatch):
    monkeypatch.setenv("CALENDAR_MODE", "LIVE")
    _mock_live_busy(monkeypatch)
    calls = []
    monkeypatch.setattr(
        calendar_service, "create_event", lambda c, mode: (calls.append(c["candidateId"]), {"eventId": "e1", "htmlLink": "h1"})[1]
    )
    candidate = _create_prepared_candidate(client)

    res = client.post(f"/api/bookings/{candidate['candidateId']}/confirm")

    assert res.status_code == 200
    assert calls == [candidate["candidateId"]]
    body = res.json()
    assert body["calendarEvent"] == {"created": True, "simulated": False, "eventId": "e1", "htmlLink": "h1"}


def test_live_confirm_success_transitions_to_confirmed_and_stores_event(client, monkeypatch):
    monkeypatch.setenv("CALENDAR_MODE", "LIVE")
    _mock_live_busy(monkeypatch)
    monkeypatch.setattr(calendar_service, "create_event", lambda c, mode: {"eventId": "e1", "htmlLink": "h1"})
    candidate = _create_prepared_candidate(client)

    client.post(f"/api/bookings/{candidate['candidateId']}/confirm")

    stored = store.candidates[candidate["candidateId"]]
    assert stored["status"] == "CONFIRMED"
    assert stored["calendarEvent"]["eventId"] == "e1"
    assert stored["calendarEvent"]["htmlLink"] == "h1"


def test_live_insert_failure_returns_502_and_keeps_prepared(client, monkeypatch):
    monkeypatch.setenv("CALENDAR_MODE", "LIVE")
    _mock_live_busy(monkeypatch)

    def _fail(candidate, mode):
        raise RuntimeError("simulated insert failure")

    monkeypatch.setattr(calendar_service, "create_event", _fail)
    candidate = _create_prepared_candidate(client)

    res = client.post(f"/api/bookings/{candidate['candidateId']}/confirm")

    assert res.status_code == 502
    assert res.json()["code"] == "EXTERNAL_SERVICE_ERROR"
    assert store.candidates[candidate["candidateId"]]["status"] == "PREPARED"
    assert "eventId" not in store.candidates[candidate["candidateId"]] or "calendarEvent" not in store.candidates[
        candidate["candidateId"]
    ]


def test_live_busy_conflict_never_calls_insert(client, monkeypatch):
    monkeypatch.setenv("CALENDAR_MODE", "LIVE")
    candidate = _create_prepared_candidate(client)
    overlapping_busy = [{"start": candidate["start"], "end": candidate["end"]}]
    _mock_live_busy(monkeypatch, busy=overlapping_busy)

    def _fail_if_called(c, mode):
        raise AssertionError("충돌 시 create_event가 호출되면 안 된다.")

    monkeypatch.setattr(calendar_service, "create_event", _fail_if_called)

    res = client.post(f"/api/bookings/{candidate['candidateId']}/confirm")

    assert res.status_code == 409
    assert res.json()["code"] == "CALENDAR_CONFLICT"
    assert store.candidates[candidate["candidateId"]]["status"] == "PREPARED"


def test_live_exact_touching_busy_does_not_conflict(client, monkeypatch):
    monkeypatch.setenv("CALENDAR_MODE", "LIVE")
    candidate = _create_prepared_candidate(client)
    # candidate.end와 정확히 맞닿는 busy 구간 (겹치지 않아야 한다 - half-open [start, end)).
    touching_busy = [{"start": candidate["end"], "end": "2099-01-01T00:00:00+09:00"}]
    _mock_live_busy(monkeypatch, busy=touching_busy)
    monkeypatch.setattr(calendar_service, "create_event", lambda c, mode: {"eventId": "e1", "htmlLink": "h1"})

    res = client.post(f"/api/bookings/{candidate['candidateId']}/confirm")

    assert res.status_code == 200
    assert res.json()["calendarEvent"]["created"] is True


def test_live_busy_recheck_failure_does_not_confirm_with_cached_data(client, monkeypatch):
    # LIVE를 요청했는데 재조회가 CACHED로 조용히 대체됐다면(안전장치 기본 차단 등) confirm을
    # 그 CACHED 데이터로 확정하면 안 된다 - 502로 안전하게 실패해야 한다(분석 fallback 정책과 다름).
    monkeypatch.setenv("CALENDAR_MODE", "LIVE")

    def _fail_if_called(c, mode):
        raise AssertionError("busy 재조회가 실패했는데 create_event가 호출되면 안 된다.")

    monkeypatch.setattr(calendar_service, "create_event", _fail_if_called)
    # get_busy_times는 monkeypatch하지 않는다 - conftest.py의 안전장치가 실제 인증을 막아
    # LIVE 요청이 자동으로 CACHED로 fallback되도록 둔다.
    candidate = _create_prepared_candidate(client)

    res = client.post(f"/api/bookings/{candidate['candidateId']}/confirm")

    assert res.status_code == 502
    assert res.json()["code"] == "EXTERNAL_SERVICE_ERROR"
    assert store.candidates[candidate["candidateId"]]["status"] == "PREPARED"


def test_live_duplicate_confirm_calls_insert_only_once(client, monkeypatch):
    monkeypatch.setenv("CALENDAR_MODE", "LIVE")
    _mock_live_busy(monkeypatch)
    calls = []
    monkeypatch.setattr(
        calendar_service, "create_event", lambda c, mode: (calls.append(1), {"eventId": "e1", "htmlLink": "h1"})[1]
    )
    candidate = _create_prepared_candidate(client)

    first = client.post(f"/api/bookings/{candidate['candidateId']}/confirm")
    second = client.post(f"/api/bookings/{candidate['candidateId']}/confirm")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()
    assert len(calls) == 1


# ---- 비밀정보 미노출 ----


def test_insert_failure_response_has_no_detail_or_traceback(client, monkeypatch):
    # 실제 googleapiclient HttpError는 토큰/시크릿을 메시지에 담지 않는다(별도 확인 완료).
    # 여기서는 confirm_service가 traceback이나 raw detail을 응답에 얹지 않는지만 회귀 감지한다.
    monkeypatch.setenv("CALENDAR_MODE", "LIVE")
    _mock_live_busy(monkeypatch)

    def _fail(c, mode):
        raise RuntimeError("simulated insert failure")

    monkeypatch.setattr(calendar_service, "create_event", _fail)
    candidate = _create_prepared_candidate(client)

    res = client.post(f"/api/bookings/{candidate['candidateId']}/confirm")

    body = res.json()
    assert body["code"] == "EXTERNAL_SERVICE_ERROR"
    assert body.get("detail") is None
    assert "Traceback" not in body["message"]
    assert "token" not in body["message"].lower()
