# CalendarClient. 목표 이벤트(결혼식 등) 조회는 Agent Tool이 아니라 일반 함수다 (CLAUDE.md 참고).
# get_calendar_busy_times(Agent Tool, app/agent/tools.py)는 이 모듈의 get_busy_times()를 호출만
# 하며 조회 로직을 중복 구현하지 않는다.
from __future__ import annotations

from datetime import date
from typing import List, Optional

from app.fixtures import load_json
from app.models.schemas import UpcomingEvent


def _parse_date(value: str) -> date:
    return date.fromisoformat(value)


def get_important_events(album_id: str) -> List[dict]:
    events = load_json("calendar_events.json")
    return events.get(album_id, [])


def select_nearest_care_relevant_event(album_id: str, today: date) -> Optional[UpcomingEvent]:
    events = get_important_events(album_id)
    upcoming = [e for e in events if e.get("careRelevant") and _parse_date(e["date"]) >= today]
    if not upcoming:
        return None
    nearest = min(upcoming, key=lambda e: _parse_date(e["date"]))
    return UpcomingEvent(eventId=nearest["eventId"], title=nearest["summary"], date=nearest["date"], source="cached")


def get_busy_times(album_id: str, calendar_mode: str) -> List[dict]:
    """CALENDAR_MODE=CACHED면 calendar_busy.json을 읽는다.
    CALENDAR_MODE=LIVE 실제 Google Calendar 연동은 아직 붙이지 않았다 (CLAUDE.md 구현 순서 9단계) —
    현재는 CACHED와 동일하게 fixture를 사용한다."""
    busy = load_json("calendar_busy.json")
    return busy.get(album_id, [])
