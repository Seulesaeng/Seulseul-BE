# 공통 오류 응답. docs/api-contract.md의 오류 코드 표를 그대로 따른다.
from __future__ import annotations

from typing import Any, Optional


class ApiException(Exception):
    def __init__(self, status_code: int, code: str, message: str, detail: Optional[Any] = None) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        self.detail = detail
        super().__init__(message)


def invalid_request(message: str, detail: Optional[Any] = None) -> ApiException:
    return ApiException(400, "INVALID_REQUEST", message, detail)


def album_not_found(message: str) -> ApiException:
    return ApiException(404, "ALBUM_NOT_FOUND", message)


def analysis_not_found(analysis_id: str) -> ApiException:
    return ApiException(404, "ANALYSIS_NOT_FOUND", f"analysis_id '{analysis_id}'를 찾을 수 없습니다.")


def schedule_not_applicable(message: str) -> ApiException:
    return ApiException(409, "SCHEDULE_NOT_APPLICABLE", message)


def candidate_not_found(candidate_id: str) -> ApiException:
    return ApiException(
        404, "CANDIDATE_NOT_FOUND", f"candidate_id '{candidate_id}'를 찾을 수 없거나 더 이상 승인할 수 없습니다."
    )


def calendar_conflict(message: str, detail: Optional[Any] = None) -> ApiException:
    return ApiException(409, "CALENDAR_CONFLICT", message, detail)


def external_service_error(message: str) -> ApiException:
    return ApiException(502, "EXTERNAL_SERVICE_ERROR", message)
