# 인메모리 상태. DB 없음 (CLAUDE.md 참고). 프로세스 재시작 시 초기화된다.
from __future__ import annotations

from typing import Dict


class InMemoryStore:
    def __init__(self) -> None:
        self.analyses: Dict[str, dict] = {}
        self.schedules: Dict[str, dict] = {}
        self.candidates: Dict[str, dict] = {}

    def reset(self) -> None:
        self.analyses.clear()
        self.schedules.clear()
        self.candidates.clear()


store = InMemoryStore()
