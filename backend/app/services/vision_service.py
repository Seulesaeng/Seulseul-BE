# Vision 서비스. 이 단계는 vision_cache.json(CACHED)만 읽는다.
# VISION_MODE=LIVE 연동은 아직 붙이지 않았다 (CLAUDE.md 구현 순서 참고).
from __future__ import annotations

from typing import Optional, Tuple

from app.fixtures import load_json
from app.models.schemas import ChangeSignal

FALLBACK_REASON = "VISION_CACHE_MISSING_USED_DEFAULT"


def load_change_signal(album_id: str) -> Tuple[ChangeSignal, Optional[str]]:
    cache = load_json("vision_cache.json")
    entry = cache.get(album_id)
    if entry is None:
        fallback_signal = ChangeSignal(
            level="LOW",
            trend="STABLE",
            confidence="LOW",
            reason="캐시된 Vision 결과가 없어 기본값을 사용합니다.",
            visionFailed=True,
        )
        return fallback_signal, FALLBACK_REASON

    signal = ChangeSignal(
        level=entry["changeLevel"],
        trend=entry["trend"],
        confidence=entry["confidence"],
        reason=entry["reason"],
        visionFailed=entry.get("visionFailed", False),
    )
    return signal, None
