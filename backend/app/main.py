# FastAPI 진입점. 이 단계는 6개 API 전부 목(mock) 데이터로 동작한다.
# 실제 Vision/smolagents/Google Calendar는 아직 연결하지 않는다.
from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.errors import ApiException
from app.settings import get_frontend_origins

BACKEND_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BACKEND_DIR / ".env")

app = FastAPI(title="슬슬 API")

# allow_origins=["*"] + allow_credentials=True 조합은 쓰지 않는다(브라우저가 거부하는 조합이기도
# 하고, 자격증명 포함 요청을 아무 origin에나 허용하는 것은 안전하지 않다). 이 API는 쿠키/세션을
# 쓰지 않으므로 allow_credentials는 기본값(False)을 그대로 둔다.
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_frontend_origins(),
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)

DEMO_ALBUM_DIR = BACKEND_DIR / "demo-album"
if DEMO_ALBUM_DIR.is_dir():
    app.mount("/demo-album", StaticFiles(directory=str(DEMO_ALBUM_DIR)), name="demo-album")

app.include_router(router)


@app.exception_handler(ApiException)
async def handle_api_exception(request: Request, exc: ApiException) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"code": exc.code, "message": exc.message, "detail": exc.detail},
    )


@app.exception_handler(RequestValidationError)
async def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content={"code": "INVALID_REQUEST", "message": "요청 형식이 올바르지 않습니다.", "detail": None},
    )
