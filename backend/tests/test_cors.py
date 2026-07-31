# CORS 설정(app/main.py, app/settings.py get_frontend_origins) 테스트.
from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

from app import settings


def _reload_app(monkeypatch):
    """FRONTEND_ORIGIN 환경변수를 바꾼 뒤 CORSMiddleware가 새 값을 읽도록 app.main을 다시 로드한다
    (CORSMiddleware는 add_middleware 시점의 allow_origins를 고정해서 쓴다).

    app.main은 모듈 최상단에서 load_dotenv(BACKEND_DIR / ".env")를 호출한다 - reload 시 이게
    다시 실행되면 개발자의 실제 backend/.env에 있는 FRONTEND_ORIGIN이 테스트가 방금 지운 값
    자리에 다시 채워져 monkeypatch를 덮어쓴다. dotenv.load_dotenv 자체를 no-op으로 바꿔 막는다
    (conftest.py의 다른 안전장치들과 동일한 패턴)."""
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: False)
    import app.main as main_module

    importlib.reload(main_module)
    return main_module.app


# ---- 기본 후보(FRONTEND_ORIGIN 미설정) ----


def test_default_origins_allow_localhost_5173(monkeypatch):
    monkeypatch.delenv("FRONTEND_ORIGIN", raising=False)
    client = TestClient(_reload_app(monkeypatch))

    res = client.get("/api/health", headers={"Origin": "http://localhost:5173"})

    assert res.status_code == 200
    assert res.headers["access-control-allow-origin"] == "http://localhost:5173"


@pytest.mark.parametrize(
    "origin",
    ["http://localhost:3000", "http://localhost:5173", "http://127.0.0.1:3000", "http://127.0.0.1:5173"],
)
def test_default_origins_include_all_four_candidates(monkeypatch, origin):
    monkeypatch.delenv("FRONTEND_ORIGIN", raising=False)
    client = TestClient(_reload_app(monkeypatch))

    res = client.get("/api/health", headers={"Origin": origin})

    assert res.headers.get("access-control-allow-origin") == origin


def test_disallowed_origin_gets_no_cors_header(monkeypatch):
    monkeypatch.delenv("FRONTEND_ORIGIN", raising=False)
    client = TestClient(_reload_app(monkeypatch))

    res = client.get("/api/health", headers={"Origin": "http://evil.example.com"})

    # 요청 자체는 서버에서 200으로 처리되지만(CORS는 브라우저가 강제), CORS 허용 헤더가 없으므로
    # 브라우저는 이 origin의 JS가 응답을 읽지 못하게 막는다.
    assert res.status_code == 200
    assert "access-control-allow-origin" not in res.headers


def test_disallowed_origin_preflight_is_rejected(monkeypatch):
    monkeypatch.delenv("FRONTEND_ORIGIN", raising=False)
    client = TestClient(_reload_app(monkeypatch))

    res = client.options(
        "/api/analyses",
        headers={
            "Origin": "http://evil.example.com",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )

    assert "access-control-allow-origin" not in res.headers


# ---- FRONTEND_ORIGIN 환경변수로 커스텀/복수 origin 지정 ----


def test_custom_single_origin_from_env(monkeypatch):
    monkeypatch.setenv("FRONTEND_ORIGIN", "http://example.com:4000")
    client = TestClient(_reload_app(monkeypatch))

    allowed = client.get("/api/health", headers={"Origin": "http://example.com:4000"})
    assert allowed.headers.get("access-control-allow-origin") == "http://example.com:4000"

    default_candidate_now_blocked = client.get("/api/health", headers={"Origin": "http://localhost:5173"})
    assert "access-control-allow-origin" not in default_candidate_now_blocked.headers


def test_custom_comma_separated_origins_from_env(monkeypatch):
    monkeypatch.setenv("FRONTEND_ORIGIN", "http://localhost:5173, http://localhost:3000")
    client = TestClient(_reload_app(monkeypatch))

    for origin in ("http://localhost:5173", "http://localhost:3000"):
        res = client.get("/api/health", headers={"Origin": origin})
        assert res.headers.get("access-control-allow-origin") == origin


def test_get_frontend_origins_parses_comma_separated_list(monkeypatch):
    monkeypatch.setenv("FRONTEND_ORIGIN", "http://a.com, http://b.com ,http://c.com")
    assert settings.get_frontend_origins() == ["http://a.com", "http://b.com", "http://c.com"]


def test_get_frontend_origins_defaults_when_unset(monkeypatch):
    monkeypatch.delenv("FRONTEND_ORIGIN", raising=False)
    assert settings.get_frontend_origins() == settings.DEFAULT_FRONTEND_ORIGINS


# ---- allow_origins=["*"] + allow_credentials=True 조합 금지 ----


def test_allow_credentials_is_not_enabled(monkeypatch):
    monkeypatch.delenv("FRONTEND_ORIGIN", raising=False)
    client = TestClient(_reload_app(monkeypatch))

    res = client.get("/api/health", headers={"Origin": "http://localhost:5173"})

    assert "access-control-allow-credentials" not in res.headers


# ---- 필요한 method/header만 허용 ----


def test_preflight_allows_post_and_content_type_for_allowed_origin(monkeypatch):
    monkeypatch.delenv("FRONTEND_ORIGIN", raising=False)
    client = TestClient(_reload_app(monkeypatch))

    res = client.options(
        "/api/analyses",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )

    assert res.status_code == 200
    assert "POST" in res.headers.get("access-control-allow-methods", "")
    assert "content-type" in res.headers.get("access-control-allow-headers", "").lower()


def test_preflight_allows_get_for_health(monkeypatch):
    monkeypatch.delenv("FRONTEND_ORIGIN", raising=False)
    client = TestClient(_reload_app(monkeypatch))

    res = client.options(
        "/api/health",
        headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"},
    )

    assert res.status_code == 200
    assert "GET" in res.headers.get("access-control-allow-methods", "")
