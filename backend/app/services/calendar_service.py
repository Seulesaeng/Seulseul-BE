# CalendarClient. 목표 이벤트(결혼식 등) 조회는 Agent Tool이 아니라 일반 함수다 (CLAUDE.md 참고).
# get_calendar_busy_times(Agent Tool, app/agent/tools.py)는 이 모듈의 get_busy_times()를 호출만
# 하며 조회 로직을 중복 구현하지 않는다.
#
# CALENDAR_MODE=LIVE 읽기(이벤트 조회/Free-Busy 조회)는 app/google_auth.py의
# load_runtime_credentials()(브라우저 인증 없음)로 얻은 토큰으로 실제 Google Calendar API를
# 호출한다. 실패하면 항상 CACHED fixture로 fallback하고, 그 사실을 (actual_mode, fallback_reason)
# 으로 정직하게 반환한다 - 호출자가 응답의 calendarMode를 실제 사용한 값으로 표시해야 한다.
# calendars().get()은 호출하지 않는다 - SCOPES(calendar.events, calendar.freebusy)만으로는
# 403 insufficient authentication scopes가 발생한다.
#
# POST /api/bookings/{id}/confirm의 실제 Calendar 이벤트 생성(create_event)은 이 작업 범위 밖이다
# (CLAUDE.md 구현 순서 9단계 중 이벤트 생성 부분) - 항상 실패하는 기존 동작을 그대로 유지한다.
from __future__ import annotations

import os
import sys
from datetime import date, datetime, timedelta, timezone
from typing import List, Optional, Tuple

from app.fixtures import load_json
from app.models.schemas import UpcomingEvent

CALENDAR_LIVE_EVENTS_FAILED_REASON = "CALENDAR_LIVE_EVENTS_FAILED"
CALENDAR_LIVE_BUSY_FAILED_REASON = "CALENDAR_LIVE_BUSY_FAILED"

# 중요 일정 키워드: 이 프로젝트에 별도 정책 함수가 없으므로 요청받은 키워드를 그대로 사용한다.
# CACHED 경로는 이 키워드를 쓰지 않는다 - fixture의 careRelevant 플래그를 그대로 쓰는 기존 동작 유지.
IMPORTANT_EVENT_KEYWORDS = ["결혼식", "웨딩", "촬영", "행사"]
IMPORTANT_EVENT_LOOKAHEAD_DAYS = 180  # LIVE 조회 시 앞으로 볼 기간 (약 6개월).
IMPORTANT_EVENTS_MAX_RESULTS = 20  # 키워드로 걸러내야 하므로 여유 있게 조회한다.


def _parse_date(value: str) -> date:
    return date.fromisoformat(value)


def combine_fallback_reasons(*reasons: Optional[str]) -> Optional[str]:
    parts = [reason for reason in reasons if reason]
    return "; ".join(parts) if parts else None


def _calendar_id() -> str:
    return os.environ.get("GOOGLE_CALENDAR_ID", "primary")


def _app_timezone() -> str:
    return os.environ.get("APP_TIMEZONE", "Asia/Seoul")


def _rfc3339(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _build_live_service():
    """app.google_auth.load_runtime_credentials()로 얻은 토큰으로 Calendar v3 service를 만든다.
    브라우저 인증은 절대 하지 않는다 - 토큰이 없거나 갱신 불가능하면 예외가 그대로 전파된다."""
    from googleapiclient.discovery import build

    from app import google_auth

    creds = google_auth.load_runtime_credentials()
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def _parse_event_boundary(value: dict) -> Optional[datetime]:
    """이벤트의 start/end 객체({"dateTime": ...} 또는 종일 일정 {"date": ...})를
    timezone-aware datetime으로 안전하게 파싱한다. 파싱할 수 없으면 None을 반환한다."""
    date_time = value.get("dateTime")
    if date_time:
        try:
            parsed = datetime.fromisoformat(date_time)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed

    all_day = value.get("date")
    if all_day:
        try:
            return datetime.combine(date.fromisoformat(all_day), datetime.min.time(), tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def _is_important_by_keyword(summary: str) -> bool:
    return any(keyword in summary for keyword in IMPORTANT_EVENT_KEYWORDS)


def _live_nearest_important_event() -> Optional[UpcomingEvent]:
    """LIVE: events.list(지금 ~ +180일, singleEvents=True, orderBy=startTime)를 호출해
    키워드로 걸러낸 뒤 가장 가까운 일정을 고른다. calendars.get은 호출하지 않는다."""
    service = _build_live_service()
    now = datetime.now(timezone.utc)

    result = (
        service.events()
        .list(
            calendarId=_calendar_id(),
            timeMin=_rfc3339(now),
            timeMax=_rfc3339(now + timedelta(days=IMPORTANT_EVENT_LOOKAHEAD_DAYS)),
            singleEvents=True,
            orderBy="startTime",
            maxResults=IMPORTANT_EVENTS_MAX_RESULTS,
        )
        .execute()
    )

    for item in result.get("items", []):
        summary = item.get("summary", "")
        if not _is_important_by_keyword(summary):
            continue
        start = _parse_event_boundary(item.get("start", {}))
        if start is None:
            continue
        # orderBy=startTime이라 이미 시간순 정렬돼 있다 - 첫 키워드 일치가 곧 가장 가까운 일정이다.
        return UpcomingEvent(eventId=item.get("id", ""), title=summary, date=start.date().isoformat(), source="live")
    return None


def get_important_events(album_id: str) -> List[dict]:
    """CACHED 전용 - 기존 동작 그대로 calendar_events.json의 앨범 이벤트 목록을 반환한다."""
    events = load_json("calendar_events.json")
    return events.get(album_id, [])


def select_nearest_care_relevant_event(album_id: str, today: date) -> Optional[UpcomingEvent]:
    """CACHED 전용 - 기존 동작 그대로. LIVE 분기는 resolve_upcoming_event()가 담당한다."""
    events = get_important_events(album_id)
    upcoming = [e for e in events if e.get("careRelevant") and _parse_date(e["date"]) >= today]
    if not upcoming:
        return None
    nearest = min(upcoming, key=lambda e: _parse_date(e["date"]))
    return UpcomingEvent(eventId=nearest["eventId"], title=nearest["summary"], date=nearest["date"], source="cached")


def resolve_upcoming_event(
    album_id: str, today: date, calendar_mode: str
) -> Tuple[Optional[UpcomingEvent], str, Optional[str]]:
    """POST /api/analyses 전용 진입점. (event, actual_mode, fallback_reason)을 반환한다.
    CALENDAR_MODE=LIVE면 실제 Google Calendar에서 중요 일정을 찾고, 실패하면 CACHED로
    fallback한다 - 요청이 LIVE였더라도 실제로 CACHED를 썼다면 actual_mode는 반드시 "CACHED"다."""
    if calendar_mode == "LIVE":
        try:
            return _live_nearest_important_event(), "LIVE", None
        except Exception as exc:
            print(f"[WARN] Calendar LIVE 이벤트 조회 실패, CACHED로 대체합니다: {type(exc).__name__}", file=sys.stderr)
            fallback_event = select_nearest_care_relevant_event(album_id, today)
            return fallback_event, "CACHED", f"{CALENDAR_LIVE_EVENTS_FAILED_REASON}: {type(exc).__name__}"
    return select_nearest_care_relevant_event(album_id, today), calendar_mode, None


def get_busy_times(
    album_id: str, calendar_mode: str, start: str, end: str
) -> Tuple[List[dict], str, Optional[str]]:
    """POST /api/analyses/{id}/schedule, /retry(Agent Tool 경유), /api/bookings/{id}/confirm(재확인)
    전용 진입점. (busy_times, actual_mode, fallback_reason)을 반환한다.
    CACHED: 기존 calendar_busy.json fixture와 기존 동작을 그대로 유지한다(start/end는 쓰지 않는다).
    LIVE: freebusy.query(start~end)를 실제로 호출하고, 실패하면 CACHED로 fallback한다."""
    if calendar_mode == "LIVE":
        try:
            service = _build_live_service()
            cal_id = _calendar_id()
            result = (
                service.freebusy()
                .query(
                    body={
                        "timeMin": _rfc3339(datetime.fromisoformat(start)),
                        "timeMax": _rfc3339(datetime.fromisoformat(end)),
                        "timeZone": _app_timezone(),
                        "items": [{"id": cal_id}],
                    }
                )
                .execute()
            )
            busy_blocks = result.get("calendars", {}).get(cal_id, {}).get("busy", [])
            busy_times = [{"start": b.get("start"), "end": b.get("end")} for b in busy_blocks]
            return busy_times, "LIVE", None
        except Exception as exc:
            print(f"[WARN] Calendar LIVE Free/Busy 조회 실패, CACHED로 대체합니다: {type(exc).__name__}", file=sys.stderr)
            fallback_busy = load_json("calendar_busy.json").get(album_id, [])
            return fallback_busy, "CACHED", f"{CALENDAR_LIVE_BUSY_FAILED_REASON}: {type(exc).__name__}"

    busy = load_json("calendar_busy.json")
    return busy.get(album_id, []), calendar_mode, None


def create_event(candidate: dict, calendar_mode: str) -> dict:
    """CALENDAR_MODE=LIVE일 때 실제 Google Calendar에 이벤트를 생성한다
    (POST /api/bookings/{id}/confirm 전용, Agent Tool 아님).
    실제 이벤트 생성 연동은 이번 작업 범위 밖이다 - 지금은 항상 실패한다.
    호출자는 이 예외를 EXTERNAL_SERVICE_ERROR로 변환하고 candidate를 PREPARED로 유지해야 한다."""
    raise RuntimeError("Google Calendar LIVE 이벤트 생성은 아직 구현되지 않았습니다 (범위 밖).")
