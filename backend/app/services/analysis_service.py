# POST /api/analyses 결정론 파이프라인 조립.
# Agent를 실행하지 않는다. Calendar busy 조회/네일샵 슬롯 검색/candidate 생성을 하지 않는다 (CLAUDE.md 참고).
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import List, Optional

from app.fixtures import load_json
from app.models.schemas import (
    CalendarMarker,
    CareStatus,
    ChangeSignal,
    EvidenceLogEntry,
    Photo,
    ReversePlan,
    SelectedPhoto,
    Timing,
    UpcomingEvent,
)
from app.services import calendar_service, photo_service, policy, vision_service


@dataclass(frozen=True)
class AnalysisResult:
    selectedPhotos: List[SelectedPhoto]
    changeSignal: ChangeSignal
    timing: Timing
    careStatus: CareStatus
    upcomingEvent: Optional[UpcomingEvent]
    reversePlan: Optional[ReversePlan]
    calendarMarkers: List[CalendarMarker]
    evidenceLogs: List[EvidenceLogEntry]
    canSchedule: bool
    fallbackReason: Optional[str]


def load_service_dates(album_id: str) -> List[str]:
    history = load_json("treatment_history.json")
    return history.get(album_id, {}).get("serviceDates", [])


def run_analysis(album_id: str, photos: List[Photo], today: date, decided_at: str) -> AnalysisResult:
    logs: List[EvidenceLogEntry] = []
    step_counter = {"value": 0}

    def log(log_type: str, message: str, result: Optional[dict] = None) -> None:
        step_counter["value"] += 1
        logs.append(EvidenceLogEntry(step=step_counter["value"], type=log_type, message=message, result=result))

    # 시술 기록은 기준 사진 선택(마지막 시술일과 가장 가까운 사진)에도 필요하므로 먼저 로드한다.
    service_dates = load_service_dates(album_id)
    last_service_date = max(date.fromisoformat(d) for d in service_dates)

    # 1. 앨범 사진 정렬
    sorted_photos = photo_service.sort_photos_by_taken_at(photos)
    log(
        "SYSTEM",
        "앨범 사진을 촬영일 오름차순으로 정렬했습니다.",
        {"photoIds": [p.photoId for p in sorted_photos]},
    )

    # 2. 기준/최근 사진 선택 (기준 사진은 최근 사진과 중복되지 않는다)
    baseline = photo_service.select_baseline_photo(sorted_photos, last_service_date)
    recent = photo_service.select_recent_photos(sorted_photos, baseline)
    selected_photos = (
        [SelectedPhoto(**baseline.model_dump(), role="BASELINE")]
        + [SelectedPhoto(**photo.model_dump(), role="RECENT") for photo in recent]
    )
    log(
        "SYSTEM",
        "마지막 시술일과 가장 가까운 사진을 기준으로, 최근 사진 3장을 선택했습니다.",
        {"baselinePhotoId": baseline.photoId, "recentPhotoIds": [p.photoId for p in recent]},
    )

    # 3. Vision 변화 신호
    change_signal, vision_fallback_reason = vision_service.load_change_signal(album_id)
    log(
        "VISION",
        "Vision 변화 신호를 확인했습니다.",
        {"changeLevel": change_signal.level, "trend": change_signal.trend, "visionFailed": change_signal.visionFailed},
    )

    # 4. 개인 주기
    cycle_stats = policy.calculate_cycle_stats(service_dates)
    timing = policy.build_timing(service_dates, cycle_stats, today)
    log(
        "POLICY",
        "개인 시술 주기를 계산했습니다.",
        {
            "cycleMinDays": cycle_stats.minDays,
            "cycleAvgDays": round(cycle_stats.avgDays, 2),
            "cycleMaxDays": cycle_stats.maxDays,
            "daysSinceLastTreatment": timing.daysSinceLastService,
        },
    )

    # 5. 관리 시점 정책
    care_status = policy.decide_care_status(
        days_since_last=timing.daysSinceLastService,
        cycle_stats=cycle_stats,
        change_level=change_signal.level,
        vision_failed=change_signal.visionFailed,
        decided_at=decided_at,
    )
    log(
        "POLICY",
        "관리 시점 정책을 실행했습니다.",
        {"status": care_status.status, "needsCareNow": care_status.needsCareNow, "reasonCodes": care_status.reasonCodes},
    )

    # 6. 중요 일정 발견 (CALENDAR_MODE=CACHED -> calendar_events.json)
    upcoming_event = calendar_service.select_nearest_care_relevant_event(album_id, today)
    log(
        "CALENDAR",
        "중요 일정을 조회했습니다.",
        {"found": upcoming_event is not None, "eventId": upcoming_event.eventId if upcoming_event else None},
    )

    # 7. 역방향 스케줄 (네일 정책: 중요 일정 2~3일 전)
    reverse_plan_calc = policy.calculate_reverse_plan(upcoming_event)
    reverse_plan = policy.to_api_reverse_plan(reverse_plan_calc)
    log(
        "POLICY",
        "역방향 스케줄을 계산했습니다.",
        {
            "targetEvent": reverse_plan_calc.targetEvent.title if reverse_plan_calc else None,
            "recommendedStart": reverse_plan_calc.recommendedStart if reverse_plan_calc else None,
            "recommendedEnd": reverse_plan_calc.recommendedEnd if reverse_plan_calc else None,
            "reasonCode": reverse_plan_calc.reasonCode if reverse_plan_calc else None,
            "userMessage": reverse_plan_calc.userMessage if reverse_plan_calc else None,
        },
    )

    calendar_markers = policy.build_calendar_markers(today, care_status.needsCareNow, reverse_plan, upcoming_event)
    can_schedule = care_status.needsCareNow or reverse_plan is not None

    return AnalysisResult(
        selectedPhotos=selected_photos,
        changeSignal=change_signal,
        timing=timing,
        careStatus=care_status,
        upcomingEvent=upcoming_event,
        reversePlan=reverse_plan,
        calendarMarkers=calendar_markers,
        evidenceLogs=logs,
        canSchedule=can_schedule,
        fallbackReason=vision_fallback_reason,
    )
