#!/usr/bin/env python3
"""
슬슬 백엔드 데모 E2E 클라이언트 스크립트.

이미 실행 중인 FastAPI 서버(예: uvicorn app.main:app --port 8000)에 실제 HTTP 요청을 보낸다.
app.services.*의 내부 함수를 직접 호출하지 않는다 - 이 스크립트가 프론트엔드와 똑같은 방식으로
공개 API 6개만 호출하는지를 그대로 보여주기 위해서다.

흐름: POST /api/albums/connect -> POST /api/analyses -> POST /api/analyses/{id}/schedule
     -> (선택) POST /api/analyses/{id}/retry -> (선택, --confirm 지정 시만) POST /api/bookings/{id}/confirm

기본 실행은 confirm 직전까지만 진행한다 - candidate를 선택해 보여주기만 하고, --confirm을 명시해야만
실제로 승인을 호출한다(CALENDAR_MODE=LIVE라면 실제 Google Calendar 이벤트가 생성될 수 있다).

사용법 (backend/ 디렉터리에서, 서버가 이미 떠 있는 상태로 실행):
    python scripts/demo_e2e.py
    python scripts/demo_e2e.py --confirm
    python scripts/demo_e2e.py --confirm --retry
    python scripts/demo_e2e.py --base-url http://localhost:8000

환경변수:
    DEMO_BASE_URL   기본값 http://localhost:8000 (--base-url이 있으면 그게 우선한다)

토큰, client secret, Google 전체 응답은 출력하지 않는다. 실제로 생성된 Google Calendar 이벤트를
이 스크립트가 자동으로 삭제하지 않는다 - 필요하면 사용자가 Google Calendar에서 직접 삭제한다.
"""
from __future__ import annotations

import argparse
import os
import sys

import httpx

DEFAULT_BASE_URL = "http://localhost:8000"
REQUEST_TIMEOUT = 30.0


def base_url() -> str:
    return os.environ.get("DEMO_BASE_URL", DEFAULT_BASE_URL)


class DemoApiError(Exception):
    """API가 2xx 이외를 반환했을 때. status/code/message만 담고 전체 응답 본문은 보관하지 않는다."""

    def __init__(self, step: str, status_code: int, code: str, message: str) -> None:
        self.step = step
        self.status_code = status_code
        self.code = code
        self.message = message
        super().__init__(f"{step} 실패: {status_code} {code} - {message}")


def _request(client: httpx.Client, step: str, method: str, path: str, json: dict | None = None) -> dict:
    response = client.request(method, path, json=json, timeout=REQUEST_TIMEOUT)
    if response.status_code >= 400:
        try:
            body = response.json()
        except ValueError:
            body = {}
        raise DemoApiError(
            step=step,
            status_code=response.status_code,
            code=body.get("code", "UNKNOWN_ERROR"),
            message=body.get("message", "(메시지 없음)"),
        )
    return response.json()


def _print_header(title: str) -> None:
    print(f"\n== {title} ==")


def check_health(client: httpx.Client) -> None:
    body = _request(client, "GET /api/health", "GET", "/api/health")
    _print_header("1. health")
    print(f"  status={body['status']} visionMode={body['visionMode']} agentMode={body['agentMode']} "
          f"calendarMode={body['calendarMode']}")


def connect_album(client: httpx.Client) -> str:
    body = _request(client, "POST /api/albums/connect", "POST", "/api/albums/connect", json={"source": "DEMO"})
    _print_header("2. albums/connect")
    print(f"  albumId={body['albumId']}  photos={len(body['photos'])}장")
    return body["albumId"]


def run_analysis(client: httpx.Client, album_id: str) -> dict:
    body = _request(client, "POST /api/analyses", "POST", "/api/analyses", json={"albumId": album_id})
    _print_header("3. analyses")
    print(f"  analysisId={body['analysisId']}")
    print(f"  careStatus={body['careStatus']['status']}  canSchedule={body['canSchedule']}")
    print(f"  calendarMode={body['calendarMode']}  fallbackReason={body.get('fallbackReason')}")
    event = body.get("upcomingEvent")
    if event:
        print(f"  중요 일정: {event['title']} ({event['date']}, source={event['source']})")
    else:
        print("  중요 일정: 없음")
    reverse_plan = body.get("reversePlan")
    if reverse_plan:
        print(f"  reversePlan: {reverse_plan['recommendedStart']} ~ {reverse_plan['recommendedEnd']} "
              f"({reverse_plan['basis']})")
    else:
        print("  reversePlan: 없음")
    return body


def _print_candidates(candidates: list, excluded_slots: list) -> None:
    for candidate in candidates:
        print(f"  - [{candidate['candidateId']}] {candidate['shop']} | {candidate['start']} ~ {candidate['end']} | "
              f"{candidate['price']}원 | {candidate['recommendationReason']}")
    for slot in excluded_slots:
        print(f"  x [{slot['slotId']}] {slot['shop']} 제외됨: {slot['exclusionReason']}")


def run_schedule(client: httpx.Client, analysis_id: str) -> dict:
    body = _request(
        client, "POST /api/analyses/{id}/schedule", "POST", f"/api/analyses/{analysis_id}/schedule"
    )
    _print_header("4. schedule")
    print(f"  scheduleRunId={body['scheduleRunId']}  agentMode={body['agentMode']}  "
          f"calendarMode={body['calendarMode']}")
    _print_candidates(body["candidates"], body["excludedSlots"])
    return body


def run_retry(client: httpx.Client, analysis_id: str) -> dict:
    body = _request(
        client,
        "POST /api/analyses/{id}/retry",
        "POST",
        f"/api/analyses/{analysis_id}/retry",
        json={"searchScope": "NEXT_WEEK"},
    )
    _print_header("5. retry (NEXT_WEEK)")
    print(f"  retryRunId={body['retryRunId']}  agentMode={body['agentMode']}  calendarMode={body['calendarMode']}")
    _print_candidates(body["candidates"], body["excludedSlots"])
    return body


def select_first_candidate(schedule_body: dict) -> dict | None:
    """busy와 겹치지 않는 첫 PREPARED candidate를 고른다. schedule 응답의 candidates는 이미
    충돌(excludedSlots로 걸러진 슬롯)이 없는 것만 준비되므로, 목록의 첫 번째를 그대로 쓰면 된다."""
    candidates = schedule_body.get("candidates", [])
    return candidates[0] if candidates else None


def confirm_candidate(client: httpx.Client, candidate: dict) -> dict:
    body = _request(
        client, "POST /api/bookings/{id}/confirm", "POST", f"/api/bookings/{candidate['candidateId']}/confirm"
    )
    _print_header("6. confirm")
    event = body["calendarEvent"]
    print(f"  status={body['status']}  calendarMode={body['calendarMode']}")
    print(f"  calendarEvent: created={event['created']} simulated={event['simulated']} eventId={event['eventId']}")
    if event.get("htmlLink"):
        print(f"  htmlLink={event['htmlLink']}")
    return body


def run_demo(client: httpx.Client, confirm: bool, retry: bool) -> int:
    """전체 흐름 실행. httpx.Client를 인자로 받아 테스트에서 MockTransport로 주입할 수 있게 한다."""
    try:
        check_health(client)
        album_id = connect_album(client)
        analysis = run_analysis(client, album_id)

        if not analysis["canSchedule"]:
            print("\ncanSchedule=false — 지금은 예약 시점이 아니므로 여기서 종료합니다.")
            return 0

        schedule = run_schedule(client, analysis["analysisId"])

        if retry:
            run_retry(client, analysis["analysisId"])

        candidate = select_first_candidate(schedule)
        if candidate is None:
            print("\n선택 가능한 candidate가 없습니다.")
            return 0

        print(f"\n선택된 candidate: {candidate['candidateId']} ({candidate['shop']}, {candidate['start']})")

        if not confirm:
            print("(--confirm이 없어 여기서 멈춥니다. 실제 승인을 검증하려면 --confirm을 붙여 실행하세요.)")
            return 0

        confirm_candidate(client, candidate)
    except DemoApiError as exc:
        print(f"\n[FAIL] {exc.step}: HTTP {exc.status_code} {exc.code} - {exc.message}", file=sys.stderr)
        return 1
    except httpx.HTTPError as exc:
        print(f"\n[FAIL] 서버에 연결할 수 없습니다 ({type(exc).__name__}). "
              f"먼저 서버를 실행하세요: uvicorn app.main:app --port 8000", file=sys.stderr)
        return 1

    print("\n완료. 실제 Google Calendar 이벤트를 생성했다면 이 스크립트는 자동으로 삭제하지 않습니다 - "
          "필요하면 Google Calendar에서 직접 삭제하세요.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="슬슬 백엔드 데모 E2E 클라이언트 (실행 중인 서버에 HTTP 요청).")
    parser.add_argument("--base-url", default=None, help=f"기본값: {DEFAULT_BASE_URL} (또는 DEMO_BASE_URL 환경변수)")
    parser.add_argument("--confirm", action="store_true", help="candidate를 선택한 뒤 실제로 /confirm까지 호출한다.")
    parser.add_argument("--retry", action="store_true", help="/retry(NEXT_WEEK) 흐름도 함께 검증한다.")
    args = parser.parse_args()

    url = args.base_url or base_url()
    print(f"base URL: {url}")

    with httpx.Client(base_url=url) as client:
        return run_demo(client, confirm=args.confirm, retry=args.retry)


if __name__ == "__main__":
    raise SystemExit(main())
