# 전역 pytest 설정. 개발자의 실제 backend/.env(OPENAI_API_KEY, GOOGLE_TOKEN_PATH 등)가 있어도
# 테스트가 실제 OpenAI/Google Calendar API를 호출하지 않도록 기본값을 안전하게 고정한다.
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _safe_agent_mode_by_default(monkeypatch):
    # 기본값은 FALLBACK: 실제 LLM을 호출하지 않고 결정론 fallback 경로만 쓴다.
    # (fallback도 app/services/policy.py의 동일한 순수 함수를 쓰므로 후보 품질은 동일하다.)
    # LIVE 경로 자체를 검증하는 테스트는 AGENT_MODE=LIVE로 재설정하고
    # schedule_service.run_agent를 monkeypatch해서 실제 API 호출을 막는다.
    monkeypatch.setenv("AGENT_MODE", "FALLBACK")


@pytest.fixture(autouse=True)
def _safe_calendar_live_by_default(monkeypatch):
    # app.main이 import 시점에 backend/.env를 로드하므로, 개발자의 실제 GOOGLE_TOKEN_PATH(실제
    # credentials.json/token.json 경로)가 테스트 프로세스 전체에 그대로 남는다. CALENDAR_MODE=LIVE를
    # 쓰는 테스트가 실수로 실제 Google Calendar API를 호출하지 않도록, 기본적으로는 항상 인증
    # 실패로 처리해 LIVE 코드가 즉시 CACHED로 fallback하게 만든다. LIVE '성공' 경로를 검증하는
    # 테스트는 app.google_auth.load_runtime_credentials나 calendar_service._build_live_service를
    # 직접 monkeypatch해서 재정의한다.
    from app import google_auth

    def _blocked(*args, **kwargs):
        raise RuntimeError("테스트에서는 실제 Google Calendar 인증을 사용하지 않습니다.")

    monkeypatch.setattr(google_auth, "load_runtime_credentials", _blocked)
