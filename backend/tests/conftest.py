# 전역 pytest 설정. 개발자의 실제 backend/.env(OPENAI_API_KEY 등)가 있어도
# 테스트가 실제 OpenAI API를 호출하지 않도록 기본값을 안전하게 고정한다.
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _safe_agent_mode_by_default(monkeypatch):
    # 기본값은 FALLBACK: 실제 LLM을 호출하지 않고 결정론 fallback 경로만 쓴다.
    # (fallback도 app/services/policy.py의 동일한 순수 함수를 쓰므로 후보 품질은 동일하다.)
    # LIVE 경로 자체를 검증하는 테스트는 AGENT_MODE=LIVE로 재설정하고
    # schedule_service.run_agent를 monkeypatch해서 실제 API 호출을 막는다.
    monkeypatch.setenv("AGENT_MODE", "FALLBACK")
