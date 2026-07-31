# API 라우트. docs/api-contract.md 기준. 6개 API 전부 실제 로직으로 동작한다.
# Google Calendar LIVE(실제 OAuth 연동)만 아직 없다 (CLAUDE.md 구현 순서 9단계).
from __future__ import annotations

import random
import string

from fastapi import APIRouter, Request

from app import demo_clock, mock_data
from app.errors import album_not_found, analysis_not_found, invalid_request, schedule_not_applicable
from app.models.schemas import (
    AnalyzeRequest,
    AnalyzeResponse,
    ConfirmResponse,
    ConnectAlbumRequest,
    ConnectAlbumResponse,
    HealthResponse,
    RetryRequest,
    RetryResponse,
    ScheduleResponse,
)
from app.services import analysis_service, calendar_service, confirm_service, schedule_service
from app.settings import get_modes
from app.state import store

router = APIRouter(prefix="/api")


def _now_iso() -> str:
    return demo_clock.now_iso()


def _new_id(prefix: str) -> str:
    stamp = demo_clock.now().strftime("%Y%m%d%H%M%S")
    suffix = "".join(random.choices(string.digits, k=4))
    return f"{prefix}_{stamp}_{suffix}"


@router.get("/health", response_model=HealthResponse)
def get_health() -> HealthResponse:
    modes = get_modes()
    return HealthResponse(
        status="ok",
        visionMode=modes.vision_mode,
        agentMode=modes.agent_mode,
        calendarMode=modes.calendar_mode,
        serverTime=_now_iso(),
    )


@router.post("/albums/connect", response_model=ConnectAlbumResponse)
def connect_album(payload: ConnectAlbumRequest, request: Request) -> ConnectAlbumResponse:
    photos = mock_data.build_photos(str(request.base_url))
    return ConnectAlbumResponse(
        albumId=mock_data.ALBUM_ID,
        sourceMode=payload.source,
        connectedAt=_now_iso(),
        scanSummary=mock_data.build_scan_summary(),
        photos=photos,
    )


@router.post("/analyses", response_model=AnalyzeResponse)
def create_analysis(payload: AnalyzeRequest, request: Request) -> AnalyzeResponse:
    if payload.albumId != mock_data.ALBUM_ID:
        raise album_not_found(f"albumId '{payload.albumId}'를 찾을 수 없습니다.")

    photos = mock_data.build_photos(str(request.base_url))
    analysis_id = _new_id("analysis")
    modes = get_modes()
    today = demo_clock.today()
    decided_at = _now_iso()

    result = analysis_service.run_analysis(payload.albumId, photos, today, decided_at, modes.calendar_mode)

    response = AnalyzeResponse(
        analysisId=analysis_id,
        albumId=mock_data.ALBUM_ID,
        selectedPhotos=result.selectedPhotos,
        changeSignal=result.changeSignal,
        timing=result.timing,
        careStatus=result.careStatus,
        upcomingEvent=result.upcomingEvent,
        reversePlan=result.reversePlan,
        calendarMarkers=result.calendarMarkers,
        evidenceLogs=result.evidenceLogs,
        canSchedule=result.canSchedule,
        visionMode=modes.vision_mode,
        calendarMode=result.calendarMode,
        fallbackReason=calendar_service.combine_fallback_reasons(result.fallbackReason, result.calendarFallbackReason),
    )
    store.analyses[analysis_id] = response.model_dump()
    return response


def _get_analysis_or_404(analysis_id: str) -> dict:
    analysis = store.analyses.get(analysis_id)
    if analysis is None:
        raise analysis_not_found(analysis_id)
    return analysis


@router.post("/analyses/{analysis_id}/schedule", response_model=ScheduleResponse)
def schedule_analysis(analysis_id: str) -> ScheduleResponse:
    analysis = _get_analysis_or_404(analysis_id)
    if not analysis["canSchedule"]:
        raise schedule_not_applicable("관리 시점이 아니고 임박한 일정도 없어 예약 후보를 탐색할 필요가 없습니다.")
    if not analysis.get("reversePlan"):
        raise schedule_not_applicable("역방향 권장 구간이 없어 예약 후보를 탐색할 수 없습니다.")

    schedule_run_id = _new_id("schedule")
    result = schedule_service.run_schedule(
        analysis_id=analysis_id,
        analysis=analysis,
        album_id=mock_data.ALBUM_ID,
        schedule_run_id=schedule_run_id,
    )

    response = ScheduleResponse(
        analysisId=analysis_id,
        scheduleRunId=result.scheduleRunId,
        recommendedWindow=result.recommendedWindow,
        excludedSlots=result.excludedSlots,
        candidates=result.candidates,
        executionLogs=result.executionLogs,
        agentMode=result.agentMode,
        calendarMode=result.calendarMode,
        fallbackReason=result.fallbackReason,
    )

    store.schedules[analysis_id] = response.model_dump()
    for candidate in response.candidates:
        store.candidates[candidate.candidateId] = candidate.model_dump()
    return response


@router.post("/analyses/{analysis_id}/retry", response_model=RetryResponse)
def retry_analysis(analysis_id: str, payload: RetryRequest) -> RetryResponse:
    if payload.searchScope != "NEXT_WEEK":
        raise invalid_request("MVP에서는 searchScope로 'NEXT_WEEK'만 지원합니다.")

    analysis = _get_analysis_or_404(analysis_id)
    if not analysis["canSchedule"]:
        raise schedule_not_applicable("관리 시점이 아니고 임박한 일정도 없어 예약 후보를 탐색할 필요가 없습니다.")
    if not analysis.get("reversePlan"):
        raise schedule_not_applicable("역방향 권장 구간이 없어 예약 후보를 탐색할 수 없습니다.")

    retry_run_id = _new_id("retry")
    result = schedule_service.run_retry(
        analysis_id=analysis_id,
        analysis=analysis,
        album_id=mock_data.ALBUM_ID,
        retry_run_id=retry_run_id,
    )

    response = RetryResponse(
        analysisId=analysis_id,
        retryRunId=result.retryRunId,
        searchScope="NEXT_WEEK",
        searchWindow=result.searchWindow,
        candidates=result.candidates,
        excludedSlots=result.excludedSlots,
        executionLogs=result.executionLogs,
        agentMode=result.agentMode,
        calendarMode=result.calendarMode,
        fallbackReason=result.fallbackReason,
    )

    # 13. 기존 schedule 후보는 삭제/덮어쓰지 않는다 - candidates만 store.candidates에 추가한다.
    for candidate in response.candidates:
        store.candidates[candidate.candidateId] = candidate.model_dump()
    return response


@router.post("/bookings/{candidate_id}/confirm", response_model=ConfirmResponse)
def confirm_booking(candidate_id: str) -> ConfirmResponse:
    return confirm_service.confirm_candidate(candidate_id, store.candidates, mock_data.ALBUM_ID)
