#!/usr/bin/env python3
"""
Google Calendar LIVE 연동 1단계: OAuth 토큰 발급/갱신 독립 스크립트.

브라우저 인증(run_local_server)은 이 스크립트를 사용자가 직접 실행할 때만 일어난다.
FastAPI 서버(uvicorn app.main:app)는 이 스크립트를 임포트하지 않으며, 요청 처리 중에는
절대 브라우저를 열지 않는다. app/services/calendar_service.py를 포함한 기존 API 코드는
이 단계에서 수정하지 않는다 - 이 스크립트는 향후 LIVE 연동이 실제로 붙을 때 사용할
backend/token.json을 미리 준비해두는 역할만 한다.

사용법 (backend/ 디렉터리에서 실행):
    python scripts/issue_google_token.py

경로 기본값은 backend/credentials.json, backend/token.json이며 각각
GOOGLE_CREDENTIALS_PATH / GOOGLE_TOKEN_PATH 환경변수로 덮어쓸 수 있다.

이 스크립트는 client secret, access token, refresh token, token.json의 내용을
어떤 로그에도 출력하지 않는다.
"""
from __future__ import annotations

import os
import sys
import tempfile
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent

SCOPES = [
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/calendar.freebusy",
]


def credentials_path() -> Path:
    override = os.environ.get("GOOGLE_CREDENTIALS_PATH")
    return Path(override) if override else BACKEND_DIR / "credentials.json"


def token_path() -> Path:
    override = os.environ.get("GOOGLE_TOKEN_PATH")
    return Path(override) if override else BACKEND_DIR / "token.json"


def load_existing_credentials(path: Path):
    """token.json이 있으면 로드한다. 읽기/파싱에 실패하면 None을 반환해 재발급으로 넘어간다."""
    from google.oauth2.credentials import Credentials

    if not path.is_file():
        return None
    try:
        return Credentials.from_authorized_user_file(str(path), SCOPES)
    except Exception as exc:
        print(f"[WARN] 기존 {path.name}을 읽는 데 실패해 새로 발급합니다: {type(exc).__name__}", file=sys.stderr)
        return None


def refresh_credentials(creds) -> bool:
    """만료된 credentials를 refresh_token으로 갱신한다. 성공하면 True."""
    from google.auth.transport.requests import Request

    try:
        creds.refresh(Request())
        return True
    except Exception as exc:
        print(f"[WARN] 토큰 갱신 실패: {type(exc).__name__}", file=sys.stderr)
        return False


def run_installed_app_flow(creds_path: Path):
    """브라우저를 열어 사용자 동의를 받는다. 이 스크립트를 직접 실행할 때만 호출된다."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), SCOPES)
    return flow.run_local_server(port=0)


def save_token(creds, path: Path) -> None:
    """token.json을 원자적으로 저장한다(중간 실패 시 기존 파일이 손상되지 않는다)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(creds.to_json())
        os.replace(tmp_path, path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def get_credentials(creds_path: Path, tok_path: Path):
    """1~7단계: 기존 token 로드 -> 유효하면 재사용 -> 만료+refresh_token이면 refresh
    -> 그래도 안 되면 credentials.json으로 브라우저 인증 후 token.json 저장."""
    if not creds_path.is_file():
        raise FileNotFoundError(
            f"{creds_path}를 찾을 수 없습니다. Google Cloud Console에서 다운로드한 OAuth 클라이언트 JSON을 "
            f"이 경로에 저장하거나 GOOGLE_CREDENTIALS_PATH 환경변수로 위치를 지정하세요."
        )

    creds = load_existing_credentials(tok_path)

    if creds and creds.valid:
        print(f"[OK] 기존 {tok_path.name}이 유효합니다. 재사용합니다.")
        return creds

    if creds and creds.expired and creds.refresh_token:
        print("[INFO] 토큰이 만료되어 갱신을 시도합니다...")
        if refresh_credentials(creds):
            save_token(creds, tok_path)
            print(f"[OK] 토큰을 갱신하고 {tok_path.name}에 저장했습니다.")
            return creds
        print("[INFO] 갱신에 실패해 새로 인증합니다...")

    print("[INFO] 유효한 토큰이 없어 브라우저 인증을 시작합니다...")
    creds = run_installed_app_flow(creds_path)
    save_token(creds, tok_path)
    print(f"[OK] 인증에 성공해 {tok_path.name}을 저장했습니다.")
    return creds


def verify_connection(creds) -> dict:
    """8~9단계: Calendar v3 service를 만들어, 이 스크립트의 SCOPES(calendar.events, calendar.freebusy)로
    실제 호출 가능한 두 요청으로만 연동을 검증한다.

    service.calendars().get(calendarId="primary")는 호출하지 않는다 - calendars.get은
    calendar/calendar.readonly류 스코프가 있어야 하며 이 두 SCOPES만으로는 403
    insufficient authentication scopes가 발생한다. 이벤트를 생성/수정하지 않는다."""
    from googleapiclient.discovery import build

    service = build("calendar", "v3", credentials=creds, cache_discovery=False)

    # calendar.events 스코프 검증: 이벤트 읽기 권한만 확인하고 결과 내용은 사용하지 않는다.
    service.events().list(calendarId="primary", maxResults=1, singleEvents=True).execute()

    # calendar.freebusy 스코프 검증.
    now = datetime.now(timezone.utc)
    service.freebusy().query(
        body={
            "timeMin": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "timeMax": (now + timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "timeZone": "Asia/Seoul",
            "items": [{"id": "primary"}],
        }
    ).execute()

    return {"eventsAccessVerified": True, "freeBusyAccessVerified": True}


def main() -> int:
    creds_path = credentials_path()
    tok_path = token_path()

    print(f"credentials: {creds_path}")
    print(f"token:       {tok_path}")
    print(f"scopes:      {', '.join(SCOPES)}")
    print()

    try:
        creds = get_credentials(creds_path, tok_path)
        verify_connection(creds)
    except Exception as exc:
        print(f"\n[FAIL] {type(exc).__name__}: {exc}", file=sys.stderr)
        traceback.print_exc()
        return 1

    # 10. 검증 결과만 출력한다 - 토큰/시크릿 값은 어디에도 출력하지 않는다.
    print("\n[OK] Google Calendar 연결 검증 성공")
    print("  이벤트(events) 접근 검증됨")
    print("  Free/Busy 접근 검증됨")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
