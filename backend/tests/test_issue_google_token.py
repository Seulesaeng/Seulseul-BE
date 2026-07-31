# backend/scripts/issue_google_token.py 테스트.
# OAuth 흐름(run_local_server)과 Google API(googleapiclient.discovery.build)는 항상 mock한다 -
# 이 테스트는 실제 브라우저나 실제 Google API를 절대 호출하지 않는다.
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import issue_google_token as igt  # noqa: E402


# ---- credentials_path()/token_path(): 기본값 + 환경변수 override ----


def test_credentials_path_defaults_to_backend_credentials_json(monkeypatch):
    monkeypatch.delenv("GOOGLE_CREDENTIALS_PATH", raising=False)
    assert igt.credentials_path() == igt.BACKEND_DIR / "credentials.json"


def test_token_path_defaults_to_backend_token_json(monkeypatch):
    monkeypatch.delenv("GOOGLE_TOKEN_PATH", raising=False)
    assert igt.token_path() == igt.BACKEND_DIR / "token.json"


def test_credentials_path_honors_env_override(monkeypatch, tmp_path):
    custom = tmp_path / "custom_creds.json"
    monkeypatch.setenv("GOOGLE_CREDENTIALS_PATH", str(custom))
    assert igt.credentials_path() == custom


def test_token_path_honors_env_override(monkeypatch, tmp_path):
    custom = tmp_path / "custom_token.json"
    monkeypatch.setenv("GOOGLE_TOKEN_PATH", str(custom))
    assert igt.token_path() == custom


# ---- 1. credentials.json이 없을 때 명확히 실패 ----


def test_get_credentials_raises_clearly_when_credentials_file_missing(tmp_path):
    missing_creds = tmp_path / "credentials.json"
    tok = tmp_path / "token.json"

    with pytest.raises(FileNotFoundError, match="credentials.json"):
        igt.get_credentials(missing_creds, tok)


def test_main_fails_with_exit_1_when_credentials_missing(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("GOOGLE_CREDENTIALS_PATH", str(tmp_path / "credentials.json"))
    monkeypatch.setenv("GOOGLE_TOKEN_PATH", str(tmp_path / "token.json"))

    exit_code = igt.main()

    assert exit_code == 1
    captured = capsys.readouterr()
    assert "FAIL" in captured.err


# ---- load_existing_credentials: 실제 파일 파싱(오프라인, 네트워크 없음) ----


def test_load_existing_credentials_returns_none_when_file_missing(tmp_path):
    assert igt.load_existing_credentials(tmp_path / "token.json") is None


def test_load_existing_credentials_returns_none_on_malformed_json(tmp_path, capsys):
    tok = tmp_path / "token.json"
    tok.write_text("this is not valid json {{{", encoding="utf-8")

    result = igt.load_existing_credentials(tok)

    assert result is None
    assert "WARN" in capsys.readouterr().err


def test_load_existing_credentials_parses_valid_token_file(tmp_path):
    future_expiry = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    tok = tmp_path / "token.json"
    tok.write_text(
        json.dumps(
            {
                "token": "fake-access-token",
                "refresh_token": "fake-refresh-token",
                "client_id": "fake-client-id",
                "client_secret": "fake-client-secret",
                "expiry": future_expiry,
                "scopes": igt.SCOPES,
            }
        ),
        encoding="utf-8",
    )

    creds = igt.load_existing_credentials(tok)

    from google.oauth2.credentials import Credentials

    assert isinstance(creds, Credentials)
    assert creds.valid is True


# ---- 2~5. get_credentials() 오케스트레이션: 재사용 / refresh / 새 인증 ----


def _fake_creds(*, valid, expired=False, refresh_token=None):
    return SimpleNamespace(valid=valid, expired=expired, refresh_token=refresh_token, to_json=lambda: '{"fake":true}')


def test_get_credentials_reuses_valid_existing_token(tmp_path, monkeypatch, capsys):
    creds_path = tmp_path / "credentials.json"
    creds_path.write_text("{}", encoding="utf-8")
    tok_path = tmp_path / "token.json"

    valid_creds = _fake_creds(valid=True)
    monkeypatch.setattr(igt, "load_existing_credentials", lambda p: valid_creds)

    def _fail_if_called(*a, **k):
        raise AssertionError("유효한 토큰이 있는데 브라우저 인증 플로우가 호출되면 안 된다.")

    monkeypatch.setattr(igt, "run_installed_app_flow", _fail_if_called)

    saved = []
    monkeypatch.setattr(igt, "save_token", lambda c, p: saved.append((c, p)))

    result = igt.get_credentials(creds_path, tok_path)

    assert result is valid_creds
    assert saved == []  # 재사용 시에는 다시 저장하지 않는다
    assert "재사용" in capsys.readouterr().out


def test_get_credentials_refreshes_expired_token_with_refresh_token(tmp_path, monkeypatch):
    creds_path = tmp_path / "credentials.json"
    creds_path.write_text("{}", encoding="utf-8")
    tok_path = tmp_path / "token.json"

    expired_creds = _fake_creds(valid=False, expired=True, refresh_token="rt_123")
    monkeypatch.setattr(igt, "load_existing_credentials", lambda p: expired_creds)

    refresh_calls = []
    monkeypatch.setattr(igt, "refresh_credentials", lambda c: (refresh_calls.append(c), True)[1])

    def _fail_if_called(*a, **k):
        raise AssertionError("refresh가 성공했는데 브라우저 인증 플로우가 호출되면 안 된다.")

    monkeypatch.setattr(igt, "run_installed_app_flow", _fail_if_called)

    saved = []
    monkeypatch.setattr(igt, "save_token", lambda c, p: saved.append((c, p)))

    result = igt.get_credentials(creds_path, tok_path)

    assert result is expired_creds
    assert refresh_calls == [expired_creds]
    assert saved == [(expired_creds, tok_path)]


def test_get_credentials_falls_back_to_full_flow_when_no_token(tmp_path, monkeypatch):
    creds_path = tmp_path / "credentials.json"
    creds_path.write_text("{}", encoding="utf-8")
    tok_path = tmp_path / "token.json"

    monkeypatch.setattr(igt, "load_existing_credentials", lambda p: None)

    new_creds = _fake_creds(valid=True)
    flow_calls = []
    monkeypatch.setattr(igt, "run_installed_app_flow", lambda p: (flow_calls.append(p), new_creds)[1])

    saved = []
    monkeypatch.setattr(igt, "save_token", lambda c, p: saved.append((c, p)))

    result = igt.get_credentials(creds_path, tok_path)

    assert result is new_creds
    assert flow_calls == [creds_path]
    assert saved == [(new_creds, tok_path)]


def test_get_credentials_falls_back_to_full_flow_when_refresh_fails(tmp_path, monkeypatch):
    creds_path = tmp_path / "credentials.json"
    creds_path.write_text("{}", encoding="utf-8")
    tok_path = tmp_path / "token.json"

    expired_creds = _fake_creds(valid=False, expired=True, refresh_token="rt_123")
    monkeypatch.setattr(igt, "load_existing_credentials", lambda p: expired_creds)
    monkeypatch.setattr(igt, "refresh_credentials", lambda c: False)

    new_creds = _fake_creds(valid=True)
    flow_calls = []
    monkeypatch.setattr(igt, "run_installed_app_flow", lambda p: (flow_calls.append(p), new_creds)[1])
    monkeypatch.setattr(igt, "save_token", lambda c, p: None)

    result = igt.get_credentials(creds_path, tok_path)

    assert result is new_creds
    assert flow_calls == [creds_path]  # refresh 실패 시 새 인증으로 폴백


# ---- save_token: 원자적 저장 + 파일 내용 확인 ----


def test_save_token_writes_file_atomically(tmp_path):
    creds = SimpleNamespace(to_json=lambda: '{"refresh_token": "rt", "client_secret": "cs"}')
    tok_path = tmp_path / "nested" / "token.json"

    igt.save_token(creds, tok_path)

    assert tok_path.is_file()
    assert json.loads(tok_path.read_text(encoding="utf-8")) == {"refresh_token": "rt", "client_secret": "cs"}
    # 임시 파일이 남아있지 않아야 한다.
    assert list(tok_path.parent.glob("*.tmp")) == []


# ---- 8~9. verify_connection(): Google API는 mock. SCOPES(events, freebusy)로 실제 호출 가능한
# events.list / freebusy.query만 사용하고, calendars.get은 호출하지 않는다(403 insufficient scopes 원인). ----


class _FakeEvents:
    def __init__(self, calls):
        self._calls = calls

    def list(self, **kwargs):
        self._calls.append(("events.list", kwargs))
        return self

    def execute(self):
        return {"items": []}


class _FakeFreebusy:
    def __init__(self, calls):
        self._calls = calls

    def query(self, **kwargs):
        self._calls.append(("freebusy.query", kwargs))
        return self

    def execute(self):
        return {"calendars": {"primary": {"busy": []}}}


class _FakeCalendarsCalledError(AssertionError):
    pass


class _FakeService:
    def __init__(self, calls):
        self._calls = calls

    def events(self):
        return _FakeEvents(self._calls)

    def freebusy(self):
        return _FakeFreebusy(self._calls)

    def calendars(self):
        raise _FakeCalendarsCalledError(
            "calendars()는 호출되면 안 된다 - calendar.events/calendar.freebusy 스코프만으로는 "
            "calendars.get이 403 insufficient authentication scopes를 반환한다."
        )


def test_verify_connection_calls_events_list_and_freebusy_query(monkeypatch):
    calls = []

    def _fake_build(serviceName, version, credentials=None, cache_discovery=None):
        assert serviceName == "calendar"
        assert version == "v3"
        return _FakeService(calls)

    monkeypatch.setattr("googleapiclient.discovery.build", _fake_build)

    result = igt.verify_connection(SimpleNamespace())

    assert result == {"eventsAccessVerified": True, "freeBusyAccessVerified": True}

    call_names = [name for name, _ in calls]
    assert "events.list" in call_names
    assert "freebusy.query" in call_names

    events_kwargs = next(kwargs for name, kwargs in calls if name == "events.list")
    assert events_kwargs == {"calendarId": "primary", "maxResults": 1, "singleEvents": True}

    freebusy_kwargs = next(kwargs for name, kwargs in calls if name == "freebusy.query")
    body = freebusy_kwargs["body"]
    assert body["timeZone"] == "Asia/Seoul"
    assert body["items"] == [{"id": "primary"}]
    assert body["timeMin"].endswith("Z")
    assert body["timeMax"].endswith("Z")
    assert body["timeMin"] < body["timeMax"]


def test_verify_connection_never_calls_calendars_get(monkeypatch):
    calls = []

    def _fake_build(serviceName, version, credentials=None, cache_discovery=None):
        return _FakeService(calls)

    monkeypatch.setattr("googleapiclient.discovery.build", _fake_build)

    # calendars()를 호출하면 _FakeCalendarsCalledError가 터진다 - 예외 없이 끝나야
    # calendars().get(...)이 실제로 호출되지 않았다는 뜻이다.
    igt.verify_connection(SimpleNamespace())


# ---- main(): 성공 경로 end-to-end (전부 mock), 비밀정보 미출력 ----


def test_main_success_path_prints_verification_only_no_secrets(tmp_path, monkeypatch, capsys):
    creds_path = tmp_path / "credentials.json"
    creds_path.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("GOOGLE_CREDENTIALS_PATH", str(creds_path))
    monkeypatch.setenv("GOOGLE_TOKEN_PATH", str(tmp_path / "token.json"))

    secret_creds = _fake_creds(valid=True)
    monkeypatch.setattr(igt, "load_existing_credentials", lambda p: secret_creds)
    monkeypatch.setattr(
        igt, "verify_connection", lambda c: {"eventsAccessVerified": True, "freeBusyAccessVerified": True}
    )

    exit_code = igt.main()

    assert exit_code == 0
    captured = capsys.readouterr()
    assert "[OK] Google Calendar 연결 검증 성공" in captured.out
    assert "이벤트" in captured.out
    assert "Free/Busy" in captured.out
    for secret in ("fake-refresh-token", "fake-access-token", "client_secret", "refresh_token"):
        assert secret not in captured.out
        assert secret not in captured.err


def test_main_prints_exact_two_scopes(tmp_path, monkeypatch, capsys):
    creds_path = tmp_path / "credentials.json"
    creds_path.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("GOOGLE_CREDENTIALS_PATH", str(creds_path))
    monkeypatch.setenv("GOOGLE_TOKEN_PATH", str(tmp_path / "token.json"))
    monkeypatch.setattr(igt, "load_existing_credentials", lambda p: _fake_creds(valid=True))
    monkeypatch.setattr(
        igt, "verify_connection", lambda c: {"eventsAccessVerified": True, "freeBusyAccessVerified": True}
    )

    igt.main()

    out = capsys.readouterr().out
    assert "https://www.googleapis.com/auth/calendar.events" in out
    assert "https://www.googleapis.com/auth/calendar.freebusy" in out
    assert igt.SCOPES == [
        "https://www.googleapis.com/auth/calendar.events",
        "https://www.googleapis.com/auth/calendar.freebusy",
    ]
