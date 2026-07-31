# 앨범 사진 정렬/선택 (순수 함수).
from __future__ import annotations

from datetime import date, datetime
from typing import List

from app.models.schemas import Photo, SelectedPhoto

RECENT_PHOTO_COUNT = 3


def sort_photos_by_taken_at(photos: List[Photo]) -> List[Photo]:
    return sorted(photos, key=lambda photo: datetime.fromisoformat(photo.takenAt))


def _taken_date(photo: Photo) -> date:
    return datetime.fromisoformat(photo.takenAt).date()


def select_baseline_photo(sorted_photos: List[Photo], reference_date: date) -> Photo:
    """마지막 시술일(reference_date)과 촬영일이 가장 가까운 사진을 기준 사진으로 선택한다."""
    if not sorted_photos:
        raise ValueError("앨범에 사진이 없습니다.")
    return min(sorted_photos, key=lambda photo: abs((_taken_date(photo) - reference_date).days))


def select_recent_photos(sorted_photos: List[Photo], baseline: Photo, count: int = RECENT_PHOTO_COUNT) -> List[Photo]:
    """기준 사진과 중복되지 않는 최근 사진 count장을 선택한다."""
    remaining = [photo for photo in sorted_photos if photo.photoId != baseline.photoId]
    return remaining[-count:] if count > 0 else []


def build_selected_photos(photos: List[Photo], reference_date: date) -> List[SelectedPhoto]:
    sorted_photos = sort_photos_by_taken_at(photos)
    baseline = select_baseline_photo(sorted_photos, reference_date)
    recent = select_recent_photos(sorted_photos, baseline)
    selected = [SelectedPhoto(**baseline.model_dump(), role="BASELINE")]
    selected += [SelectedPhoto(**photo.model_dump(), role="RECENT") for photo in recent]
    return selected
