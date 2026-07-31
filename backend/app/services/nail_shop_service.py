# 네일샵 슬롯 데이터 소스. nail_shop_slots.json(fixture)에서 조회한다.
# 실제 예약/결제는 하지 않는다 (CLAUDE.md 절대 범위 참고).
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.fixtures import load_json


def _all_slots(album_id: str) -> List[Dict[str, Any]]:
    return load_json("nail_shop_slots.json").get(album_id, [])


def get_slots_by_scope(album_id: str, shop_scope: str) -> List[Dict[str, Any]]:
    is_favorite = shop_scope == "FAVORITE_SHOP"
    return [slot for slot in _all_slots(album_id) if slot.get("favoriteShop") == is_favorite]


def get_slot_by_id(album_id: str, slot_id: str) -> Optional[Dict[str, Any]]:
    for slot in _all_slots(album_id):
        if slot["slotId"] == slot_id:
            return slot
    return None
