# backend/scripts/calendar_live_smoke_test.py 테스트.
# Google API(googleapiclient.discovery.build)와 파일 I/O(token 로드/refresh/저장)는 항상 mock한다 -
# 이 테스트는 실제 네트워크 호출도, 실제 backend/token.json 파일도 건드리지 않는다.
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import calendar_live_smoke_test as clst  # noqa: E402
from app.google_auth import load_runtime_credentials as _real_load_runtime_credentials  # noqa: E402


def _fake_creds(*, valid, expired=False, refresh_token=None):
    return SimpleNamespace(valid=valid, expired=expired, refresh_token=refresh_token, to_json=lambda: '{"fake":true}')


# ---- calendar_id()/app_timezone(): 기본값 + 환경변수 override ----


def test_calendar_id_defaults_to_primary(monkeypatch):
    monkeypatch.delenv("GOOGLE_CALENDAR_ID", raising=False)
    assert clst.calendar_id() == "primary"


def test_calendar_id_honors_env_override(monkeypatch):
    monkeypatch.setenv("GOOGLE_CALENDAR_ID", "team@example.com")
    assert clst.calendar_id() == "team@example.com"


def test_app_timezone_defaults_to_asia_seoul(monkeypatch):
    monkeypatch.delenv("APP_TIMEZONE", raising=False)
    assert clst.app_timezone() == "Asia/Seoul"


def test_app_timezone_honors_env_override(monkeypatch):
    monkeypatch.setenv("APP_TIMEZONE", "UTC")
    assert clst.app_timezone() == "UTC"


# ---- 3~4. token 로드/refresh: app.google_auth.load_runtime_credentials()를 그대로 재사용한다.
# (그 함수 자체의 로드/refresh/원자적 저장 단위 테스트는 tests/test_google_auth.py에 있다 -
# 여기서는 run_smoke_test()가 그 함수를 올바르게 호출/전파하는지만 확인한다.) ----


def test_load_runtime_credentials_is_reused_not_reimplemented():
    # conftest.py의 autouse 안전장치가 google_auth.load_runtime_credentials를 이미 monkeypatch했을
    # 수 있으므로, 모듈 최상단에서 임포트 시점(어떤 fixture도 실행되기 전)에 잡아둔 원본과 비교한다.
    assert clst.load_runtime_credentials is _real_load_runtime_credentials


def test_run_smoke_test_propagates_load_runtime_credentials_failure(tmp_path, monkeypatch):
    def _fail(tok_path):
        raise FileNotFoundError(f"{tok_path}가 없습니다.")

    monkeypatch.setattr(clst, "load_runtime_credentials", _fail)

    with pytest.raises(FileNotFoundError, match="없습니다"):
        clst.run_smoke_test(tmp_path / "token.json", "primary", "Asia/Seoul")


def test_never_triggers_browser_auth():
    # issue_google_token 전용인 run_installed_app_flow(브라우저 인증)를 이 모듈이 임포트하지 않는지 확인한다.
    assert not hasattr(clst, "run_installed_app_flow")


# ---- 5. events.list / freebusy.query: 정확한 인자 + 응답에서 필요한 필드만 추출 ----


class _FakeEvents:
    def __init__(self, calls, items):
        self._calls = calls
        self._items = items

    def list(self, **kwargs):
        self._calls.append(("events.list", kwargs))
        return self

    def execute(self):
        return {"items": self._items}

    def insert(self, **kwargs):
        raise AssertionError("events().insert()가 호출되면 안 된다 - 읽기 전용 smoke test다.")

    def delete(self, **kwargs):
        raise AssertionError("events().delete()가 호출되면 안 된다 - 읽기 전용 smoke test다.")


class _FakeFreebusy:
    def __init__(self, calls, busy):
        self._calls = calls
        self._busy = busy

    def query(self, **kwargs):
        self._calls.append(("freebusy.query", kwargs))
        return self

    def execute(self):
        return {"calendars": {"primary": {"busy": self._busy}}}


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
        raise AssertionError("calendars()는 호출되면 안 된다 (현재 SCOPES로는 403이 발생한다).")


def test_check_events_calls_with_exact_arguments_and_extracts_summary_start_end():
    calls = []
    items = [
        {
            "summary": "네일 예약",
            "start": {"dateTime": "2026-08-12T19:00:00+09:00"},
            "end": {"dateTime": "2026-08-12T20:30:00+09:00"},
            "attendees": [{"email": "someone@example.com"}],  # 추출 대상이 아님을 확인
            "htmlLink": "https://calendar.google.com/event?eid=secret",
        }
    ]
    service = _FakeService(calls, items=items)

    result = clst.check_events(service, "primary")

    assert result == [
        {"summary": "네일 예약", "start": "2026-08-12T19:00:00+09:00", "end": "2026-08-12T20:30:00+09:00"}
    ]
    name, kwargs = calls[0]
    assert name == "events.list"
    assert kwargs["calendarId"] == "primary"
    assert kwargs["singleEvents"] is True
    assert kwargs["orderBy"] == "startTime"
    assert kwargs["maxResults"] == 10
    assert kwargs["timeMin"].endswith("Z")
    assert kwargs["timeMax"].endswith("Z")

    time_min = datetime.strptime(kwargs["timeMin"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    time_max = datetime.strptime(kwargs["timeMax"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    assert abs((time_max - time_min) - timedelta(days=30)) < timedelta(seconds=5)


def test_check_events_uses_custom_calendar_id():
    calls = []
    service = _FakeService(calls, items=[])

    clst.check_events(service, "team@example.com")

    assert calls[0][1]["calendarId"] == "team@example.com"


def test_check_freebusy_calls_with_exact_body_and_extracts_start_end_only():
    calls = []
    busy = [{"start": "2026-08-13T18:00:00+09:00", "end": "2026-08-13T19:30:00+09:00", "extraField": "ignored"}]
    service = _FakeService(calls, busy=busy)

    result = clst.check_freebusy(service, "primary", "Asia/Seoul")

    assert result == [{"start": "2026-08-13T18:00:00+09:00", "end": "2026-08-13T19:30:00+09:00"}]
    name, kwargs = calls[0]
    assert name == "freebusy.query"
    body = kwargs["body"]
    assert body["timeZone"] == "Asia/Seoul"
    assert body["items"] == [{"id": "primary"}]
    assert body["timeMin"].endswith("Z")
    assert body["timeMax"].endswith("Z")

    time_min = datetime.strptime(body["timeMin"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    time_max = datetime.strptime(body["timeMax"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    assert abs((time_max - time_min) - timedelta(days=7)) < timedelta(seconds=5)


def test_check_freebusy_uses_custom_calendar_id_in_items():
    calls = []
    service = _FakeService(calls, busy=[])

    clst.check_freebusy(service, "team@example.com", "Asia/Seoul")

    assert calls[0][1]["body"]["items"] == [{"id": "team@example.com"}]


# ---- 6. run_smoke_test(): 이벤트 생성/수정/삭제 없음, calendars() 미호출 (읽기 전용) ----


def test_run_smoke_test_never_mutates_or_calls_calendars_get(tmp_path, monkeypatch):
    valid_creds = _fake_creds(valid=True)
    monkeypatch.setattr(clst, "load_runtime_credentials", lambda p: valid_creds)

    calls = []
    monkeypatch.setattr("googleapiclient.discovery.build", lambda *a, **k: _FakeService(calls))

    result = clst.run_smoke_test(tmp_path / "token.json", "primary", "Asia/Seoul")

    assert result == {"events": [], "busy": []}
    call_names = [name for name, _ in calls]
    assert call_names == ["events.list", "freebusy.query"]  # insert/delete/calendars 없이 이 둘만


# ---- main(): 성공/실패 경로, 비밀정보 미출력 ----


def test_main_success_path_prints_summary_only_no_secrets(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(clst, "token_path", lambda: tmp_path / "token.json")
    monkeypatch.delenv("GOOGLE_CALENDAR_ID", raising=False)
    monkeypatch.delenv("APP_TIMEZONE", raising=False)

    secret_creds = _fake_creds(valid=True)
    monkeypatch.setattr(clst, "load_runtime_credentials", lambda p: secret_creds)

    items = [
        {
            "summary": "네일 예약",
            "start": {"dateTime": "2026-08-12T19:00:00+09:00"},
            "end": {"dateTime": "2026-08-12T20:30:00+09:00"},
        }
    ]
    busy = [{"start": "2026-08-13T18:00:00+09:00", "end": "2026-08-13T19:30:00+09:00"}]
    calls = []
    monkeypatch.setattr(
        "googleapiclient.discovery.build", lambda *a, **k: _FakeService(calls, items=items, busy=busy)
    )

    exit_code = clst.main()

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "[OK] events.list 검증 성공" in out
    assert "[OK] freebusy.query 검증 성공" in out
    assert "네일 예약" in out
    assert "2026-08-12T19:00:00+09:00" in out
    assert "2026-08-13T18:00:00+09:00" in out
    for secret in ("fake-refresh-token", "fake-access-token", "client_secret", "refresh_token", "attendees"):
        assert secret not in out


def test_main_fails_with_exit_1_when_token_missing(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(clst, "token_path", lambda: tmp_path / "token.json")

    def _fail(tok_path):
        raise FileNotFoundError(f"{tok_path}가 없거나 읽을 수 없습니다.")

    monkeypatch.setattr(clst, "load_runtime_credentials", _fail)

    exit_code = clst.main()

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "FAIL" in captured.err
    assert "token.json" in captured.err
