# app/services/calendar_service.py의 CALENDAR_MODE=LIVE 읽기 경로(resolve_upcoming_event,
# get_busy_times) 테스트. Google API(googleapiclient.discovery.build)는 항상 mock한다 -
# conftest.py의 _safe_calendar_live_by_default가 기본적으로 실제 인증도 차단한다.
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from app.services import calendar_service

ALBUM_ID = "album-001"
DEMO_TODAY = date(2026, 8, 10)


class _FakeEvents:
    def __init__(self, calls, items):
        self._calls = calls
        self._items = items

    def list(self, **kwargs):
        self._calls.append(("events.list", kwargs))
        return self

    def execute(self):
        return {"items": self._items}


class _FakeFreebusy:
    def __init__(self, calls, busy):
        self._calls = calls
        self._busy = busy

    def query(self, **kwargs):
        self._calls.append(("freebusy.query", kwargs))
        return self

    def execute(self):
        return {"calendars": {calendar_service._calendar_id(): {"busy": self._busy}}}


class _FakeService:
    def __init__(self, calls, items=None, busy=None):
        self._calls = calls
        self._items = items or []
        self._busy = busy or []

    def events(self):
        return _FakeEvents(self._calls, self._items)

    def freebusy(self):
        return _FakeFreebusy(self._calls, self._busy)

    def calendars(self):
        raise AssertionError("calendars()는 호출되면 안 된다 (현재 SCOPES로는 403 insufficient scopes).")


def _use_fake_live_service(monkeypatch, **kwargs):
    calls = []
    service = _FakeService(calls, **kwargs)
    monkeypatch.setattr(calendar_service, "_build_live_service", lambda: service)
    return calls


# ---- resolve_upcoming_event(): LIVE events.list 정확한 인자 ----


def test_live_events_list_called_with_exact_arguments(monkeypatch):
    calls = _use_fake_live_service(monkeypatch, items=[])

    calendar_service.resolve_upcoming_event(ALBUM_ID, DEMO_TODAY, "LIVE")

    assert len(calls) == 1
    name, kwargs = calls[0]
    assert name == "events.list"
    assert kwargs["calendarId"] == "primary"
    assert kwargs["singleEvents"] is True
    assert kwargs["orderBy"] == "startTime"
    assert kwargs["maxResults"] == calendar_service.IMPORTANT_EVENTS_MAX_RESULTS
    assert kwargs["timeMin"].endswith("Z")
    assert kwargs["timeMax"].endswith("Z")

    time_min = datetime.strptime(kwargs["timeMin"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    time_max = datetime.strptime(kwargs["timeMax"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    assert abs((time_max - time_min) - timedelta(days=calendar_service.IMPORTANT_EVENT_LOOKAHEAD_DAYS)) < timedelta(
        seconds=5
    )


def test_live_events_list_uses_custom_calendar_id(monkeypatch):
    monkeypatch.setenv("GOOGLE_CALENDAR_ID", "team@example.com")
    calls = _use_fake_live_service(monkeypatch, items=[])

    calendar_service.resolve_upcoming_event(ALBUM_ID, DEMO_TODAY, "LIVE")

    assert calls[0][1]["calendarId"] == "team@example.com"


def test_live_never_calls_calendars_get(monkeypatch):
    _use_fake_live_service(monkeypatch, items=[], busy=[])

    calendar_service.resolve_upcoming_event(ALBUM_ID, DEMO_TODAY, "LIVE")
    calendar_service.get_busy_times(ALBUM_ID, "LIVE", "2026-08-12T00:00:00+09:00", "2026-08-13T23:59:59+09:00")
    # 위 두 호출에서 calendars()가 불렸다면 _FakeService.calendars()가 이미 AssertionError를 던졌을 것이다.


# ---- resolve_upcoming_event(): 키워드 필터 + 가장 가까운 일정 + dateTime/all-day 파싱 ----


def test_live_filters_by_keyword_and_picks_first_ordered_match(monkeypatch):
    items = [
        {"id": "evt_1", "summary": "팀 회의", "start": {"dateTime": "2026-08-11T10:00:00+09:00"}},
        {"id": "evt_2", "summary": "친구 결혼식", "start": {"dateTime": "2026-08-15T14:00:00+09:00"}},
        {"id": "evt_3", "summary": "동창회", "start": {"dateTime": "2026-08-20T10:00:00+09:00"}},
    ]
    _use_fake_live_service(monkeypatch, items=items)

    event, mode, reason = calendar_service.resolve_upcoming_event(ALBUM_ID, DEMO_TODAY, "LIVE")

    assert mode == "LIVE"
    assert reason is None
    assert event is not None
    assert event.eventId == "evt_2"
    assert event.title == "친구 결혼식"
    assert event.date == "2026-08-15"
    assert event.source == "live"


def test_live_all_day_event_is_parsed_safely():
    boundary = calendar_service._parse_event_boundary({"date": "2026-08-15"})
    assert boundary is not None
    assert boundary.tzinfo is not None
    assert boundary.date().isoformat() == "2026-08-15"


def test_live_datetime_event_is_parsed_as_timezone_aware():
    boundary = calendar_service._parse_event_boundary({"dateTime": "2026-08-15T14:00:00+09:00"})
    assert boundary is not None
    assert boundary.tzinfo is not None


def test_live_unparseable_boundary_returns_none():
    assert calendar_service._parse_event_boundary({}) is None
    assert calendar_service._parse_event_boundary({"date": "not-a-date"}) is None


def test_live_all_day_wedding_event_is_selected(monkeypatch):
    items = [{"id": "evt_allday", "summary": "웨딩 촬영", "start": {"date": "2026-09-01"}}]
    _use_fake_live_service(monkeypatch, items=items)

    event, mode, _reason = calendar_service.resolve_upcoming_event(ALBUM_ID, DEMO_TODAY, "LIVE")

    assert mode == "LIVE"
    assert event.eventId == "evt_allday"
    assert event.date == "2026-09-01"


def test_live_non_matching_keywords_are_ignored(monkeypatch):
    items = [
        {"id": "evt_1", "summary": "팀 회의", "start": {"dateTime": "2026-08-11T10:00:00+09:00"}},
        {"id": "evt_2", "summary": "점심 약속", "start": {"dateTime": "2026-08-12T12:00:00+09:00"}},
    ]
    _use_fake_live_service(monkeypatch, items=items)

    event, mode, reason = calendar_service.resolve_upcoming_event(ALBUM_ID, DEMO_TODAY, "LIVE")

    assert mode == "LIVE"
    assert reason is None
    assert event is None


# ---- resolve_upcoming_event(): LIVE 실패 -> CACHED fallback, calendarMode=CACHED ----


def test_live_events_failure_falls_back_to_cached_with_calendar_mode_cached(monkeypatch):
    def _raise():
        raise RuntimeError("simulated live failure")

    monkeypatch.setattr(calendar_service, "_build_live_service", _raise)

    event, mode, reason = calendar_service.resolve_upcoming_event(ALBUM_ID, DEMO_TODAY, "LIVE")

    assert mode == "CACHED"  # 요청은 LIVE였지만 실제로는 CACHED를 썼다 -> 반드시 CACHED로 보고
    assert reason == f"{calendar_service.CALENDAR_LIVE_EVENTS_FAILED_REASON}: RuntimeError"
    # CACHED fallback도 기존 fixture 기준 결혼식 이벤트를 정상적으로 찾는다.
    assert event is not None
    assert event.eventId == "evt_wedding_001"
    assert event.source == "cached"


def test_live_events_default_blocked_auth_also_falls_back_to_cached(monkeypatch):
    # _build_live_service를 따로 monkeypatch하지 않으면 conftest.py의 안전장치(_blocked)가
    # google_auth.load_runtime_credentials를 막아 자동으로 이 경로를 타야 한다.
    event, mode, reason = calendar_service.resolve_upcoming_event(ALBUM_ID, DEMO_TODAY, "LIVE")

    assert mode == "CACHED"
    assert reason is not None
    assert reason.startswith(calendar_service.CALENDAR_LIVE_EVENTS_FAILED_REASON)


def test_cached_mode_never_attempts_live_call(monkeypatch):
    def _fail_if_called():
        raise AssertionError("CACHED 요청인데 _build_live_service가 호출되면 안 된다.")

    monkeypatch.setattr(calendar_service, "_build_live_service", _fail_if_called)

    event, mode, reason = calendar_service.resolve_upcoming_event(ALBUM_ID, DEMO_TODAY, "CACHED")

    assert mode == "CACHED"
    assert reason is None
    assert event is not None
    assert event.eventId == "evt_wedding_001"


# ---- get_busy_times(): LIVE freebusy.query 정확한 body ----

WINDOW_START = "2026-08-12T00:00:00+09:00"
WINDOW_END = "2026-08-13T23:59:59+09:00"


def test_live_freebusy_query_called_with_exact_body(monkeypatch):
    calls = _use_fake_live_service(monkeypatch, busy=[])

    calendar_service.get_busy_times(ALBUM_ID, "LIVE", WINDOW_START, WINDOW_END)

    assert len(calls) == 1
    name, kwargs = calls[0]
    assert name == "freebusy.query"
    body = kwargs["body"]
    assert body["timeZone"] == "Asia/Seoul"
    assert body["items"] == [{"id": "primary"}]
    assert body["timeMin"].endswith("Z")
    assert body["timeMax"].endswith("Z")


def test_live_freebusy_query_uses_custom_calendar_id_and_timezone(monkeypatch):
    monkeypatch.setenv("GOOGLE_CALENDAR_ID", "team@example.com")
    monkeypatch.setenv("APP_TIMEZONE", "UTC")
    calls = _use_fake_live_service(monkeypatch, busy=[])

    calendar_service.get_busy_times(ALBUM_ID, "LIVE", WINDOW_START, WINDOW_END)

    body = calls[0][1]["body"]
    assert body["items"] == [{"id": "team@example.com"}]
    assert body["timeZone"] == "UTC"


def test_live_freebusy_extracts_start_end_only(monkeypatch):
    busy = [{"start": "2026-08-13T18:00:00+09:00", "end": "2026-08-13T19:30:00+09:00", "extra": "ignored"}]
    _use_fake_live_service(monkeypatch, busy=busy)

    busy_times, mode, reason = calendar_service.get_busy_times(ALBUM_ID, "LIVE", WINDOW_START, WINDOW_END)

    assert mode == "LIVE"
    assert reason is None
    assert busy_times == [{"start": "2026-08-13T18:00:00+09:00", "end": "2026-08-13T19:30:00+09:00"}]


def test_live_busy_failure_falls_back_to_cached_with_calendar_mode_cached(monkeypatch):
    def _raise():
        raise RuntimeError("simulated live failure")

    monkeypatch.setattr(calendar_service, "_build_live_service", _raise)

    busy_times, mode, reason = calendar_service.get_busy_times(ALBUM_ID, "LIVE", WINDOW_START, WINDOW_END)

    assert mode == "CACHED"
    assert reason == f"{calendar_service.CALENDAR_LIVE_BUSY_FAILED_REASON}: RuntimeError"
    # CACHED fallback은 기존 fixture 원본(팀 회의, summary 포함)을 그대로 반환한다(기존 동작 유지).
    assert busy_times == [{"summary": "팀 회의", "start": "2026-08-13T18:00:00+09:00", "end": "2026-08-13T19:30:00+09:00"}]


def test_live_busy_default_blocked_auth_also_falls_back_to_cached():
    # _build_live_service를 monkeypatch하지 않으면 conftest.py의 안전장치가 인증을 막아
    # 자동으로 CACHED fallback을 타야 한다 (실제 네트워크 호출 없음).
    busy_times, mode, reason = calendar_service.get_busy_times(ALBUM_ID, "LIVE", WINDOW_START, WINDOW_END)

    assert mode == "CACHED"
    assert reason is not None
    assert reason.startswith(calendar_service.CALENDAR_LIVE_BUSY_FAILED_REASON)


def test_cached_busy_never_attempts_live_call(monkeypatch):
    def _fail_if_called():
        raise AssertionError("CACHED 요청인데 _build_live_service가 호출되면 안 된다.")

    monkeypatch.setattr(calendar_service, "_build_live_service", _fail_if_called)

    busy_times, mode, reason = calendar_service.get_busy_times(ALBUM_ID, "CACHED", WINDOW_START, WINDOW_END)

    assert mode == "CACHED"
    assert reason is None
    assert busy_times == [{"summary": "팀 회의", "start": "2026-08-13T18:00:00+09:00", "end": "2026-08-13T19:30:00+09:00"}]


# ---- 구간 충돌 정책: half-open [start, end) 유지 확인 (LIVE로 얻은 busy_times도 동일 함수로 판정) ----


def test_exact_touching_busy_interval_does_not_conflict(monkeypatch):
    from app.services import policy

    # LIVE에서 받은 busy time이 슬롯의 end와 정확히 맞닿아 있다 (겹치지 않아야 한다).
    touching_busy = [{"start": "2026-08-12T20:30:00+09:00", "end": "2026-08-12T21:30:00+09:00"}]
    _use_fake_live_service(monkeypatch, busy=touching_busy)

    busy_times, _mode, _reason = calendar_service.get_busy_times(
        ALBUM_ID, "LIVE", "2026-08-12T00:00:00+09:00", "2026-08-12T23:59:59+09:00"
    )

    slot_start, slot_end = "2026-08-12T19:00:00+09:00", "2026-08-12T20:30:00+09:00"
    assert policy.is_slot_busy(slot_start, slot_end, busy_times) is False
