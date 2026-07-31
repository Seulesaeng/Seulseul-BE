# Google OAuth 토큰 로드/갱신 공용 로직.
# backend/scripts/issue_google_token.py(최초 발급, 브라우저 인증)와
# backend/scripts/calendar_live_smoke_test.py(읽기 전용 smoke test), 그리고
# app 런타임(app/services/calendar_service.py, CALENDAR_MODE=LIVE)이 이 모듈을 공유한다.
#
# FastAPI 요청 처리 중에는 절대 브라우저를 열지 않는다 - run_installed_app_flow()는
# backend/scripts/issue_google_token.py가 사용자가 직접 스크립트를 실행할 때만 호출한다.
# app 런타임과 calendar_live_smoke_test.py는 load_runtime_credentials()만 사용하며,
# 토큰이 없거나 갱신할 수 없으면 예외를 던질 뿐 절대 새로 발급을 시도하지 않는다.
#
# 토큰, client_secret, access_token, refresh_token은 이 모듈 어디에서도 출력하지 않는다.
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from typing import Optional

BACKEND_DIR = Path(__file__).resolve().parent.parent

SCOPES = [
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/calendar.freebusy",
]


def credentials_path() -> Path:
    """backend/scripts/issue_google_token.py 전용 경로 - app 런타임과 smoke test는 쓰지 않는다."""
    override = os.environ.get("GOOGLE_CREDENTIALS_PATH")
    return Path(override) if override else BACKEND_DIR / "credentials.json"


def token_path() -> Path:
    override = os.environ.get("GOOGLE_TOKEN_PATH")
    return Path(override) if override else BACKEND_DIR / "token.json"


def load_existing_credentials(path: Path):
    """token.json이 있으면 로드한다. 읽기/파싱에 실패하면 None을 반환한다."""
    from google.oauth2.credentials import Credentials

    if not path.is_file():
        return None
    try:
        return Credentials.from_authorized_user_file(str(path), SCOPES)
    except Exception as exc:
        print(f"[WARN] 기존 {path.name}을 읽는 데 실패했습니다: {type(exc).__name__}", file=sys.stderr)
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


def run_installed_app_flow(creds_path: Path):
    """브라우저를 열어 사용자 동의를 받는다. backend/scripts/issue_google_token.py가
    사용자 직접 실행 시에만 호출한다 - app 런타임과 smoke test는 이 함수를 호출하지 않는다."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), SCOPES)
    return flow.run_local_server(port=0)


def load_runtime_credentials(tok_path: Optional[Path] = None):
    """token.json을 로드하고 필요하면 refresh_token으로 갱신해 원자적으로 저장한다.
    브라우저 인증은 절대 실행하지 않는다 - token.json이 없거나 갱신할 수 없으면 예외를 던진다.
    app 런타임(CALENDAR_MODE=LIVE)과 calendar_live_smoke_test.py가 이 함수를 공유한다."""
    path = tok_path or token_path()
    creds = load_existing_credentials(path)
    if creds is None:
        raise FileNotFoundError(
            f"{path}가 없거나 읽을 수 없습니다. scripts/issue_google_token.py를 실행해 token.json을 "
            f"발급하세요 (브라우저 인증은 이 경로에서 수행되지 않습니다)."
        )

    if creds.valid:
        return creds

    if creds.expired and creds.refresh_token:
        if refresh_credentials(creds):
            save_token(creds, path)
            return creds
        raise RuntimeError(f"{path.name}의 refresh_token으로 토큰을 갱신하지 못했습니다.")

    raise RuntimeError(f"{path.name}이 유효하지 않고 refresh_token도 없습니다. 토큰을 다시 발급하세요.")
