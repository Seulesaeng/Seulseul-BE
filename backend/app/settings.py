
# 환경 모드 조회. CLAUDE.md의 기본값 규칙을 따른다.
from __future__ import annotations

import os
from dataclasses import dataclass


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


def get_frontend_origin() -> str:
    return os.environ.get("FRONTEND_ORIGIN", "http://localhost:5173")
