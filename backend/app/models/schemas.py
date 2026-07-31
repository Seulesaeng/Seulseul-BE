# Pydantic 스키마. docs/api-contract.md의 JSDoc 타입을 그대로 옮긴 것이다.
# 필드명/구조를 바꿀 때는 계약 문서를 먼저 수정한다.
from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

VisionMode = Literal["LIVE", "CACHED"]
AgentMode = Literal["LIVE", "FALLBACK", "CACHED"]
CalendarMode = Literal["LIVE", "CACHED"]
SearchScope = Literal["THIS_WEEK", "NEXT_WEEK"]
ExclusionReason = Literal["CALENDAR_BUSY", "OUTSIDE_SEARCH_SCOPE", "SHOP_UNAVAILABLE"]
RecommendationReason = Literal["권장 관리 구간 내", "Calendar 충돌 없음", "선호 네일샵", "빠른 예약 가능 시간"]
ToolName = Literal["get_calendar_busy_times", "search_nail_shop_slots", "prepare_booking_candidates"]


class ApiErrorBody(BaseModel):
    code: str
    message: str
    detail: Optional[dict] = None


class HealthResponse(BaseModel):
    status: str
    visionMode: VisionMode
    agentMode: AgentMode
    calendarMode: CalendarMode
    serverTime: str


class Photo(BaseModel):
    photoId: str
    imageUrl: str
    takenAt: str
    label: str


class SelectedPhoto(Photo):
    role: Literal["BASELINE", "RECENT"]


class CoveragePeriod(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    from_: str = Field(alias="from")
    to: str


class ScanSummary(BaseModel):
    totalPhotos: int
    coveragePeriod: CoveragePeriod


class ConnectAlbumRequest(BaseModel):
    source: Literal["DEMO"]


class ConnectAlbumResponse(BaseModel):
    albumId: str
    sourceMode: Literal["DEMO"]
    connectedAt: str
    scanSummary: ScanSummary
    photos: List[Photo]


class ChangeSignal(BaseModel):
    level: Literal["LOW", "MEDIUM", "HIGH"]
    trend: Literal["STABLE", "INCREASING"]
    confidence: Literal["LOW", "MEDIUM", "HIGH"]
    reason: str
    visionFailed: bool


class Timing(BaseModel):
    cycleDays: int
    lastServiceDate: str
    daysSinceLastService: int
    nextDueDate: str


class CareStatus(BaseModel):
    status: Literal["NOW", "SOON", "FRESH"]
    needsCareNow: bool
    reasonCodes: List[str]
    message: str
    decidedBy: Literal["POLICY"]
    decidedAt: str


class UpcomingEvent(BaseModel):
    eventId: str
    title: str
    date: str
    source: Literal["cached", "live"]


class ReversePlan(BaseModel):
    recommendedStart: str
    recommendedEnd: str
    basis: Literal["UPCOMING_EVENT", "CARE_CYCLE"]
    reason: str


class CalendarMarker(BaseModel):
    date: str
    type: Literal["CARE_NOW", "RECOMMENDED", "USER_EVENT"]
    label: str


class EvidenceLogEntry(BaseModel):
    step: int
    type: Literal["SYSTEM", "VISION", "POLICY", "CALENDAR"]
    message: str
    result: Optional[dict] = None


class AnalyzeRequest(BaseModel):
    albumId: str


class AnalyzeResponse(BaseModel):
    analysisId: str
    albumId: str
    selectedPhotos: List[SelectedPhoto]
    changeSignal: ChangeSignal
    timing: Timing
    careStatus: CareStatus
    upcomingEvent: Optional[UpcomingEvent] = None
    reversePlan: Optional[ReversePlan] = None
    calendarMarkers: List[CalendarMarker]
    evidenceLogs: List[EvidenceLogEntry]
    canSchedule: bool
    visionMode: VisionMode
    calendarMode: CalendarMode
    fallbackReason: Optional[str] = None


class RecommendedWindow(BaseModel):
    start: str
    end: str
    basis: Literal["UPCOMING_EVENT", "CARE_CYCLE"]


class ExcludedSlot(BaseModel):
    slotId: str
    shop: str
    start: str
    end: str
    exclusionReason: ExclusionReason


class Candidate(BaseModel):
    candidateId: str
    slotId: str
    shop: str
    artist: str
    service: str
    price: int
    start: str
    end: str
    recommendationReason: RecommendationReason
    status: Literal["PREPARED", "CONFIRMED"]


class ExecutionLogEntry(BaseModel):
    step: int
    type: Literal["TOOL", "POLICY", "AGENT", "SYSTEM"]
    tool: Optional[ToolName] = None
    searchScope: Optional[SearchScope] = None
    result: Optional[dict] = None
    message: str
    exclusionReason: Optional[ExclusionReason] = None


class ScheduleResponse(BaseModel):
    analysisId: str
    scheduleRunId: str
    recommendedWindow: RecommendedWindow
    excludedSlots: List[ExcludedSlot]
    candidates: List[Candidate]
    executionLogs: List[ExecutionLogEntry]
    agentMode: AgentMode
    calendarMode: CalendarMode
    fallbackReason: Optional[str] = None


class RetryRequest(BaseModel):
    searchScope: SearchScope


class RecheckResult(BaseModel):
    conflict: bool
    checkedAt: str


class ShopBooking(BaseModel):
    simulated: bool
    confirmationCode: str


class CalendarEventResult(BaseModel):
    created: bool
    simulated: bool
    eventId: Optional[str] = None
    htmlLink: Optional[str] = None


class ConfirmResponse(BaseModel):
    candidateId: str
    slotId: str
    shop: str
    artist: str
    service: str
    price: int
    start: str
    end: str
    recommendationReason: str
    status: Literal["CONFIRMED"]
    confirmedAt: str
    recheck: RecheckResult
    shopBooking: ShopBooking
    calendarEvent: CalendarEventResult
    calendarMode: CalendarMode
