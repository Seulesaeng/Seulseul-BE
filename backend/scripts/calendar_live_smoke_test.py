#!/usr/bin/env python3
"""
Google Calendar LIVE 연동 smoke test.

backend/token.json(scripts/issue_google_token.py가 발급한, calendar.events/calendar.freebusy
스코프)을 읽기 전용으로 재사용해 실제 Google Calendar API를 두 가지 읽기 호출
(events.list, freebusy.query)로 검증한다.

이 스크립트는 브라우저 OAuth를 절대 실행하지 않는다 - 그건 scripts/issue_google_token.py의
역할이다. token.json이 없거나, 만료됐는데 refresh_token으로도 갱신할 수 없으면 명확하게
실패한다. app/services/calendar_service.py를 포함한 기존 API 코드는 이 단계에서 수정하지
않는다. 기본 실행에서는 Calendar 이벤트를 생성·수정·삭제하지 않는다(읽기 전용 smoke test).

사용법 (backend/ 디렉터리에서 실행):
    python scripts/calendar_live_smoke_test.py

환경변수:
    GOOGLE_TOKEN_PATH   기본값 backend/token.json (scripts/issue_google_token.py와 공유)
    GOOGLE_CALENDAR_ID  기본값 primary
    APP_TIMEZONE        기본값 Asia/Seoul

토큰, client secret, 전체 API 응답은 어떤 로그에도 출력하지 않는다.

토큰 로드/refresh/원자적 저장의 공용 구현은 app/google_auth.py에 있다 - 이 스크립트는
load_runtime_credentials()를 그대로 재사용하며(브라우저 인증 없음), 중복 구현하지 않는다.
"""
from __future__ import annotations

import os
import sys
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

# app.google_auth를 임포트하기 위해 backend/ 를 sys.path에 넣는다.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.google_auth import load_runtime_credentials, token_path  # noqa: E402

EVENTS_WINDOW_DAYS = 30
FREEBUSY_WINDOW_DAYS = 7
EVENTS_MAX_RESULTS = 10


def calendar_id() -> str:
    return os.environ.get("GOOGLE_CALENDAR_ID", "primary")


def app_timezone() -> str:
    return os.environ.get("APP_TIMEZONE", "Asia/Seoul")


def _rfc3339(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def check_events(service, cal_id: str) -> list:
    """5. events.list를 실제로 호출해 읽기 권한을 검증한다.
    반환값은 summary/start/end만 추출한 것이다 - 전체 API 응답(attendee 등)은 보관/출력하지 않는다."""
    now = datetime.now(timezone.utc)
    result = (
        service.events()
        .list(
            calendarId=cal_id,
            timeMin=_rfc3339(now),
            timeMax=_rfc3339(now + timedelta(days=EVENTS_WINDOW_DAYS)),
            singleEvents=True,
            orderBy="startTime",
            maxResults=EVENTS_MAX_RESULTS,
        )
        .execute()
    )

    summaries = []
    for item in result.get("items", []):
        start = item.get("start", {}).get("dateTime") or item.get("start", {}).get("date")
        end = item.get("end", {}).get("dateTime") or item.get("end", {}).get("date")
        summaries.append({"summary": item.get("summary", "(제목 없음)"), "start": start, "end": end})
    return summaries


def check_freebusy(service, cal_id: str, tz: str) -> list:
    """5. freebusy.query를 실제로 호출해 읽기 권한을 검증한다.
    반환값은 busy 구간의 시작/종료 시각만 추출한 것이다."""
    now = datetime.now(timezone.utc)
    result = (
        service.freebusy()
        .query(
            body={
                "timeMin": _rfc3339(now),
                "timeMax": _rfc3339(now + timedelta(days=FREEBUSY_WINDOW_DAYS)),
                "timeZone": tz,
                "items": [{"id": cal_id}],
            }
        )
        .execute()
    )

    busy_blocks = result.get("calendars", {}).get(cal_id, {}).get("busy", [])
    return [{"start": block.get("start"), "end": block.get("end")} for block in busy_blocks]


def run_smoke_test(tok_path: Path, cal_id: str, tz: str) -> dict:
    from googleapiclient.discovery import build

    # 3~4. token.json 로드/refresh는 app.google_auth.load_runtime_credentials()가 전담한다.
    # 브라우저 인증은 여기서 절대 일어나지 않는다 - 없거나 갱신 불가능하면 예외가 그대로 전파된다.
    creds = load_runtime_credentials(tok_path)
    service = build("calendar", "v3", credentials=creds, cache_discovery=False)

    events = check_events(service, cal_id)
    busy = check_freebusy(service, cal_id, tz)
    return {"events": events, "busy": busy}


def main() -> int:
    tok_path = token_path()
    cal_id = calendar_id()
    tz = app_timezone()

    print(f"token:      {tok_path}")
    print(f"calendarId: {cal_id}")
    print(f"timeZone:   {tz}")
    print()

    try:
        result = run_smoke_test(tok_path, cal_id, tz)
    except Exception as exc:
        print(f"\n[FAIL] {type(exc).__name__}: {exc}", file=sys.stderr)
        traceback.print_exc()
        return 1

    # 7~8. 토큰/시크릿/전체 API 응답은 출력하지 않는다 - summary/시작/종료 시각만 출력한다.
    print(f"[OK] events.list 검증 성공 ({EVENTS_WINDOW_DAYS}일 내 최대 {EVENTS_MAX_RESULTS}건)")
    if result["events"]:
        for event in result["events"]:
            print(f"  - {event['summary']} | {event['start']} ~ {event['end']}")
    else:
        print(f"  (다가오는 {EVENTS_WINDOW_DAYS}일 내 이벤트가 없습니다)")

    print(f"\n[OK] freebusy.query 검증 성공 ({FREEBUSY_WINDOW_DAYS}일 내)")
    if result["busy"]:
        for block in result["busy"]:
            print(f"  - {block['start']} ~ {block['end']}")
    else:
        print(f"  (다가오는 {FREEBUSY_WINDOW_DAYS}일 내 busy 구간이 없습니다)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
