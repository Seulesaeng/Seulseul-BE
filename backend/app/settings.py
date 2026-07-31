
# 환경 모드 조회. CLAUDE.md의 기본값 규칙을 따른다.
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import List

# 프론트 로컬 개발 기본 후보 (FRONTEND_ORIGIN 미설정 시). Vite(5173)/CRA(3000) 기본 포트 +
# localhost/127.0.0.1 두 호스트 표기를 모두 허용한다.
DEFAULT_FRONTEND_ORIGINS = [
    "http://localhost:3000",
    "http://localhost:5173",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:5173",
]


@dataclass(frozen=True)
class Modes:
    vision_mode: str
    agent_mode: str
    calendar_mode: str


def get_modes() -> Modes:
    return Modes(
        vision_mode=os.environ.get("VISION_MODE", "CACHED"),
        agent_mode=os.environ.get("AGENT_MODE", "LIVE"),
        calendar_mode=os.environ.get("CALENDAR_MODE", "CACHED"),
    )


def get_frontend_origins() -> List[str]:
    """CORS allow_origins 목록. 기존 FRONTEND_ORIGIN 환경변수 이름을 그대로 쓰되, 쉼표로 구분해
    여러 origin을 지정할 수 있게 한다(예: "http://localhost:5173,http://localhost:3000").
    설정하지 않으면 로컬 개발 기본 후보 4개를 사용한다."""
    raw = os.environ.get("FRONTEND_ORIGIN", "")
    origins = [origin.strip() for origin in raw.split(",") if origin.strip()]
    return origins if origins else list(DEFAULT_FRONTEND_ORIGINS)
