# backend/data/의 모든 고정 JSON fixture가 유효한 JSON인지, 최소 구조를 갖췄는지 검증한다.
from __future__ import annotations

import json
from pathlib import Path

import pytest

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

REQUIRED_FILES = [
    "album.json",
    "treatment_history.json",
    "vision_cache.json",
    "calendar_events.json",
    "calendar_busy.json",
    "nail_shop_slots.json",
]


@pytest.mark.parametrize("filename", REQUIRED_FILES)
def test_fixture_file_exists_and_parses_as_json(filename):
    path = DATA_DIR / filename
    assert path.is_file(), f"{filename}이(가) backend/data/에 없습니다."
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)  # 파싱 실패 시 json.JSONDecodeError로 테스트 실패
    assert isinstance(data, dict)


def _load(filename: str) -> dict:
    with (DATA_DIR / filename).open("r", encoding="utf-8") as f:
        return json.load(f)


def test_album_json_has_six_photos_with_required_fields():
    album = _load("album.json")
    assert album["albumId"] == "album-001"
    assert len(album["photos"]) == 6
    for photo in album["photos"]:
        for field in ("photoId", "filename", "takenAt", "label"):
            assert field in photo


def test_treatment_history_json_has_four_service_dates():
    history = _load("treatment_history.json")
    assert history["album-001"]["serviceDates"] == [
        "2026-04-29",
        "2026-05-23",
        "2026-06-18",
        "2026-07-16",
    ]


def test_vision_cache_json_has_no_mm_or_appearance_or_care_judgement_fields():
    entry = _load("vision_cache.json")["album-001"]
    assert entry["changeLevel"] == "HIGH"
    assert entry["trend"] == "INCREASING"
    assert entry["confidence"] == "MEDIUM"
    forbidden_keys = {"mm", "lengthMm", "growthMm", "appearance", "beautyScore", "needsCare", "needsCareNow"}
    assert forbidden_keys.isdisjoint(entry.keys())


def test_calendar_events_json_has_wedding_event():
    events = _load("calendar_events.json")["album-001"]
    assert len(events) == 1
    event = events[0]
    assert event["summary"] == "친구 결혼식"
    assert event["date"] == "2026-08-15"
    assert event["careRelevant"] is True


def test_calendar_busy_json_has_team_meeting():
    busy = _load("calendar_busy.json")["album-001"]
    assert len(busy) == 1
    assert busy[0]["summary"] == "팀 회의"
    assert busy[0]["start"] == "2026-08-13T18:00:00+09:00"
    assert busy[0]["end"] == "2026-08-13T19:30:00+09:00"


def test_nail_shop_slots_json_has_seven_slots_with_required_fields():
    slots = _load("nail_shop_slots.json")["album-001"]
    assert len(slots) == 7
    required_fields = {
        "slotId",
        "shop",
        "artist",
        "service",
        "price",
        "start",
        "end",
        "favoriteShop",
        "searchScope",
        "reasonHint",
    }
    for slot in slots:
        assert required_fields.issubset(slot.keys())
        assert slot["service"] == "GEL_NAIL"

    scopes = [slot["searchScope"] for slot in slots]
    assert scopes.count("FAVORITE_SHOP") == 2
    assert scopes.count("ALTERNATIVE_SHOPS") == 2
    assert scopes.count("NEXT_WEEK") == 3

    favorite_slots = [s for s in slots if s["favoriteShop"] is True]
    alternative_slots = [s for s in slots if s["favoriteShop"] is False]
    assert len(favorite_slots) == 3  # slot_2001(정상), slot_2003(충돌), slot_2005(다음 주, 정상)
    assert len(alternative_slots) == 4  # slot_2002, slot_2004, slot_2006(다음 주), slot_2007(다음 주)

    conflict_slot = next(s for s in slots if s["reasonHint"] == "CALENDAR_CONFLICT")
    assert conflict_slot["start"] == "2026-08-13T18:30:00+09:00"
    assert conflict_slot["end"] == "2026-08-13T20:00:00+09:00"
