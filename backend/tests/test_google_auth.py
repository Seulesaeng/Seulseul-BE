# app/google_auth.py 테스트. backend/scripts/issue_google_token.py와
# backend/scripts/calendar_live_smoke_test.py, app 런타임(calendar_service.py LIVE)이
# 공유하는 OAuth 토큰 로드/갱신 공용 로직이다. 실제 브라우저나 네트워크는 호출하지 않는다.
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import google_auth

# 임포트 시점(어떤 fixture도 실행되기 전)에 진짜 구현을 잡아둔다 - conftest.py의 autouse
# 안전장치(_safe_calendar_live_by_default)가 google_auth.load_runtime_credentials를 매 테스트마다
# 차단된 버전으로 monkeypatch하기 때문이다. 이 파일은 그 실제 구현 자체를 검증하는 곳이므로,
# 아래 _restore_real_load_runtime_credentials가 각 테스트 시작 전에 원본으로 되돌려놓는다.
_REAL_LOAD_RUNTIME_CREDENTIALS = google_auth.load_runtime_credentials


@pytest.fixture(autouse=True)
def _restore_real_load_runtime_credentials(monkeypatch):
    monkeypatch.setattr(google_auth, "load_runtime_credentials", _REAL_LOAD_RUNTIME_CREDENTIALS)


def _fake_creds(*, valid, expired=False, refresh_token=None):
    return SimpleNamespace(valid=valid, expired=expired, refresh_token=refresh_token, to_json=lambda: '{"fake":true}')


# ---- SCOPES는 변경되면 안 된다 ----


def test_scopes_are_exactly_events_and_freebusy():
    assert google_auth.SCOPES == [
        "https://www.googleapis.com/auth/calendar.events",
        "https://www.googleapis.com/auth/calendar.freebusy",
    ]


# ---- 경로 기본값 + 환경변수 override ----


def test_credentials_path_defaults_to_backend_credentials_json(monkeypatch):
    monkeypatch.delenv("GOOGLE_CREDENTIALS_PATH", raising=False)
    assert google_auth.credentials_path() == google_auth.BACKEND_DIR / "credentials.json"


def test_token_path_defaults_to_backend_token_json(monkeypatch):
    monkeypatch.delenv("GOOGLE_TOKEN_PATH", raising=False)
    assert google_auth.token_path() == google_auth.BACKEND_DIR / "token.json"


def test_credentials_path_honors_env_override(monkeypatch, tmp_path):
    custom = tmp_path / "custom_creds.json"
    monkeypatch.setenv("GOOGLE_CREDENTIALS_PATH", str(custom))
    assert google_auth.credentials_path() == custom


def test_token_path_honors_env_override(monkeypatch, tmp_path):
    custom = tmp_path / "custom_token.json"
    monkeypatch.setenv("GOOGLE_TOKEN_PATH", str(custom))
    assert google_auth.token_path() == custom


# ---- load_existing_credentials(): 실제 파일 파싱(오프라인) ----


def test_load_existing_credentials_returns_none_when_file_missing(tmp_path):
    assert google_auth.load_existing_credentials(tmp_path / "token.json") is None


def test_load_existing_credentials_returns_none_on_malformed_json(tmp_path, capsys):
    tok = tmp_path / "token.json"
    tok.write_text("not valid json {{{", encoding="utf-8")

    result = google_auth.load_existing_credentials(tok)

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
                "scopes": google_auth.SCOPES,
            }
        ),
        encoding="utf-8",
    )

    creds = google_auth.load_existing_credentials(tok)

    from google.oauth2.credentials import Credentials

    assert isinstance(creds, Credentials)
    assert creds.valid is True


# ---- save_token(): 원자적 저장 ----


def test_save_token_writes_file_atomically(tmp_path):
    creds = SimpleNamespace(to_json=lambda: '{"refresh_token": "rt", "client_secret": "cs"}')
    tok_path = tmp_path / "nested" / "token.json"

    google_auth.save_token(creds, tok_path)

    assert tok_path.is_file()
    assert json.loads(tok_path.read_text(encoding="utf-8")) == {"refresh_token": "rt", "client_secret": "cs"}
    assert list(tok_path.parent.glob("*.tmp")) == []


# ---- load_runtime_credentials(): 브라우저 인증 없이 로드/재사용/refresh/명확한 실패 ----


def test_load_runtime_credentials_fails_clearly_when_token_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(google_auth, "load_existing_credentials", lambda p: None)

    with pytest.raises(FileNotFoundError, match="token.json"):
        google_auth.load_runtime_credentials(tmp_path / "token.json")


def test_load_runtime_credentials_reuses_valid_token_without_refresh_or_save(tmp_path, monkeypatch):
    valid_creds = _fake_creds(valid=True)
    monkeypatch.setattr(google_auth, "load_existing_credentials", lambda p: valid_creds)

    def _fail_if_called(*a, **k):
        raise AssertionError("유효한 토큰은 refresh하면 안 된다.")

    monkeypatch.setattr(google_auth, "refresh_credentials", _fail_if_called)
    saved = []
    monkeypatch.setattr(google_auth, "save_token", lambda c, p: saved.append((c, p)))

    result = google_auth.load_runtime_credentials(tmp_path / "token.json")

    assert result is valid_creds
    assert saved == []


def test_load_runtime_credentials_refreshes_expired_token_and_saves_atomically(tmp_path, monkeypatch):
    tok_path = tmp_path / "token.json"
    expired_creds = _fake_creds(valid=False, expired=True, refresh_token="rt_123")
    monkeypatch.setattr(google_auth, "load_existing_credentials", lambda p: expired_creds)

    refresh_calls = []
    monkeypatch.setattr(google_auth, "refresh_credentials", lambda c: (refresh_calls.append(c), True)[1])
    saved = []
    monkeypatch.setattr(google_auth, "save_token", lambda c, p: saved.append((c, p)))

    result = google_auth.load_runtime_credentials(tok_path)

    assert result is expired_creds
    assert refresh_calls == [expired_creds]
    assert saved == [(expired_creds, tok_path)]


def test_load_runtime_credentials_fails_clearly_when_refresh_fails(tmp_path, monkeypatch):
    expired_creds = _fake_creds(valid=False, expired=True, refresh_token="rt_123")
    monkeypatch.setattr(google_auth, "load_existing_credentials", lambda p: expired_creds)
    monkeypatch.setattr(google_auth, "refresh_credentials", lambda c: False)

    with pytest.raises(RuntimeError, match="갱신하지 못했습니다"):
        google_auth.load_runtime_credentials(tmp_path / "token.json")


def test_load_runtime_credentials_fails_clearly_when_not_valid_and_no_refresh_token(tmp_path, monkeypatch):
    broken_creds = _fake_creds(valid=False, expired=False, refresh_token=None)
    monkeypatch.setattr(google_auth, "load_existing_credentials", lambda p: broken_creds)

    with pytest.raises(RuntimeError, match="유효하지 않고"):
        google_auth.load_runtime_credentials(tmp_path / "token.json")


def test_load_runtime_credentials_defaults_to_token_path_when_omitted(tmp_path, monkeypatch):
    monkeypatch.setattr(google_auth, "token_path", lambda: tmp_path / "token.json")
    valid_creds = _fake_creds(valid=True)
    monkeypatch.setattr(google_auth, "load_existing_credentials", lambda p: valid_creds)

    result = google_auth.load_runtime_credentials()

    assert result is valid_creds


def test_load_runtime_credentials_never_triggers_browser_auth(tmp_path, monkeypatch):
    monkeypatch.setattr(google_auth, "load_existing_credentials", lambda p: None)

    def _fail_if_called(*a, **k):
        raise AssertionError("load_runtime_credentials는 브라우저 인증을 절대 실행하면 안 된다.")

    monkeypatch.setattr(google_auth, "run_installed_app_flow", _fail_if_called)

    with pytest.raises(FileNotFoundError):
        google_auth.load_runtime_credentials(tmp_path / "token.json")
