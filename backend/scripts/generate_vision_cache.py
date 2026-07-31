#!/usr/bin/env python3
"""
고정 데모 앨범(기준 사진 1장 + 최근 사진 3장)을 실제 Vision 모델로 한 번 분석해
backend/data/vision_cache.json을 생성/갱신하는 독립 스크립트.

런타임 서버(uvicorn app.main:app)는 기본적으로 VISION_MODE=CACHED이며 이 스크립트가
만든 vision_cache.json만 읽는다. OPENAI_API_KEY를 실제로 호출하는 곳은 이 스크립트뿐이다.

사용법 (backend/ 디렉터리에서 실행):
    # backend/.env 에 OPENAI_API_KEY, VISION_MODEL을 채워두면 자동으로 읽는다.
    python scripts/generate_vision_cache.py
    python scripts/generate_vision_cache.py --album-id album-001
    python scripts/generate_vision_cache.py --dry-run   # 이미지 선택만 확인, API 호출 없음

실패(자격증명 누락 / API 오류 / 응답 검증 실패)하면 기존 vision_cache.json을 건드리지 않는다.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import List, Literal

# backend/ 를 sys.path에 넣어 app 패키지를 임포트할 수 있게 한다.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
from pydantic import BaseModel

from app.fixtures import DATA_DIR, load_json
from app.models.schemas import Photo
from app.services import photo_service

KST = timezone(timedelta(hours=9))
BACKEND_DIR = Path(__file__).resolve().parent.parent
DEMO_ALBUM_DIR = BACKEND_DIR / "demo-album"
CACHE_PATH = DATA_DIR / "vision_cache.json"

SYSTEM_INSTRUCTIONS = """당신은 네일 사진 여러 장을 비교하는 비전 분석기입니다.
기준 사진 1장과 최근 사진 3장을 비교해, "큐티클 라인과 젤네일이 시작되는 지점 사이 간격"이
시간이 지나며 어떻게 변했는지만 관찰하십시오.

반드시 지켜야 할 규칙:
- 손이나 외모를 평가하지 않는다.
- 큐티클과 젤네일 시작점 사이 간격의 시간적 변화만 본다. 다른 특징(색상, 손 모양, 청결도 등)은 언급하지 않는다.
- 정확한 mm 수치를 주장하지 않는다.
- 성장 속도를 퍼센트(%)로 주장하지 않는다.
- 관리가 필요한지 여부를 판단하지 않는다. 그것은 이 분석의 역할이 아니다.
- 예약 시점을 판단하지 않는다. 그것도 이 분석의 역할이 아니다.

관찰한 간격 변화의 크기를 changeLevel(LOW/MEDIUM/HIGH)로, 방향을 trend(STABLE/INCREASING)로,
판단 확신도를 confidence(LOW/MEDIUM/HIGH)로만 표현하십시오. reason은 위 규칙을 지키는
한 문장 설명이어야 합니다. 이미지에서 간격을 신뢰성 있게 관찰할 수 없다면
visionFailed를 true로 표시하십시오."""


class VisionAssessment(BaseModel):
    changeLevel: Literal["LOW", "MEDIUM", "HIGH"]
    trend: Literal["STABLE", "INCREASING"]
    confidence: Literal["LOW", "MEDIUM", "HIGH"]
    reason: str
    visionFailed: bool


@dataclass(frozen=True)
class SelectedImage:
    photoId: str
    role: str
    path: Path


def _load_album_photos() -> List[Photo]:
    album = load_json("album.json")
    return [
        Photo(
            photoId=photo["photoId"],
            # 이 스크립트는 HTTP를 거치지 않으므로 imageUrl 자리에 로컬 파일 경로를 직접 담는다.
            imageUrl=str(DEMO_ALBUM_DIR / photo["filename"]),
            takenAt=photo["takenAt"],
            label=photo["label"],
        )
        for photo in album["photos"]
    ]


def select_images(album_id: str) -> List[SelectedImage]:
    """런타임(app/services/analysis_service.py)과 동일한 규칙으로 기준/최근 사진을 고른다:
    마지막 시술일과 가장 가까운 사진을 기준으로, 그와 중복되지 않는 최근 3장을 선택한다."""
    photos = _load_album_photos()
    history = load_json("treatment_history.json")
    service_dates = history.get(album_id, {}).get("serviceDates", [])
    if not service_dates:
        raise RuntimeError(f"treatment_history.json에 '{album_id}' 시술 기록이 없습니다.")
    last_service_date = max(date.fromisoformat(d) for d in service_dates)

    sorted_photos = photo_service.sort_photos_by_taken_at(photos)
    baseline = photo_service.select_baseline_photo(sorted_photos, last_service_date)
    recent = photo_service.select_recent_photos(sorted_photos, baseline)

    images = [SelectedImage(photoId=baseline.photoId, role="BASELINE", path=Path(baseline.imageUrl))]
    images += [SelectedImage(photoId=p.photoId, role="RECENT", path=Path(p.imageUrl)) for p in recent]
    return images


def _encode_image_data_uri(path: Path) -> str:
    if not path.is_file():
        raise FileNotFoundError(f"이미지 파일을 찾을 수 없습니다: {path.name}")
    suffix = path.suffix.lower().lstrip(".") or "jpeg"
    mime = "image/jpeg" if suffix in ("jpg", "jpeg") else f"image/{suffix}"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _build_input(images: List[SelectedImage]) -> list:
    content: list = [
        {
            "type": "input_text",
            "text": (
                "아래 이미지는 순서대로 기준 사진 1장, 최근 사진 1~3입니다.\n"
                "1) 기준 사진\n2) 최근 사진 1\n3) 최근 사진 2\n4) 최근 사진 3"
            ),
        }
    ]
    for image in images:
        content.append({"type": "input_image", "image_url": _encode_image_data_uri(image.path), "detail": "auto"})
    return [{"role": "user", "content": content}]


def call_vision_model(images: List[SelectedImage], model: str) -> VisionAssessment:
    from openai import OpenAI  # 지연 임포트: --dry-run에서는 필요 없다.

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY 환경변수가 설정되어 있지 않습니다.")

    client = OpenAI(api_key=api_key)
    response = client.responses.parse(
        model=model,
        instructions=SYSTEM_INSTRUCTIONS,
        input=_build_input(images),
        text_format=VisionAssessment,
        timeout=60,
    )

    if response.output_parsed is None:
        raise RuntimeError("모델이 구조화된 응답을 반환하지 않았습니다(거부되었거나 파싱할 수 없음).")

    return response.output_parsed


def write_cache(album_id: str, assessment: VisionAssessment, generated_at: str) -> None:
    existing: dict = {}
    if CACHE_PATH.is_file():
        with CACHE_PATH.open("r", encoding="utf-8") as f:
            existing = json.load(f)

    existing[album_id] = {
        "changeLevel": assessment.changeLevel,
        "trend": assessment.trend,
        "confidence": assessment.confidence,
        "reason": assessment.reason,
        "visionFailed": assessment.visionFailed,
        "generatedAt": generated_at,
    }

    # 임시 파일에 먼저 쓰고 원자적으로 교체한다 (중간에 실패해도 기존 파일이 손상되지 않는다).
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(CACHE_PATH.parent), suffix=".tmp")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp_path, CACHE_PATH)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def main() -> int:
    load_dotenv(BACKEND_DIR / ".env")

    parser = argparse.ArgumentParser(description="고정 데모 앨범을 Vision 모델로 분석해 vision_cache.json을 생성한다.")
    parser.add_argument("--album-id", default="album-001")
    parser.add_argument("--dry-run", action="store_true", help="이미지 선택만 확인하고 API를 호출하지 않는다.")
    args = parser.parse_args()

    try:
        images = select_images(args.album_id)
    except Exception as exc:
        print(f"[FAIL] 사진 선택 실패: {exc}", file=sys.stderr)
        return 1

    print(f"선택된 이미지 ({len(images)}장):")
    for image in images:
        print(f"  - [{image.role}] {image.photoId} ({image.path.name})")

    if args.dry_run:
        print("--dry-run 지정됨: API를 호출하지 않고 종료합니다. vision_cache.json은 변경되지 않았습니다.")
        return 0

    model = os.environ.get("VISION_MODEL")
    if not model:
        print("[FAIL] VISION_MODEL 환경변수가 설정되어 있지 않습니다.", file=sys.stderr)
        return 1

    try:
        assessment = call_vision_model(images, model)
    except Exception as exc:
        # 예외 메시지에는 API 키나 이미지 데이터를 담지 않는다 (call_vision_model이 그런 값을 절대 포함하지 않게 구성됨).
        print(f"[FAIL] Vision 모델 호출 실패: {exc}", file=sys.stderr)
        print("기존 vision_cache.json은 변경하지 않았습니다.", file=sys.stderr)
        return 1

    generated_at = datetime.now(KST).isoformat(timespec="seconds")

    try:
        write_cache(args.album_id, assessment, generated_at)
    except Exception as exc:
        print(f"[FAIL] vision_cache.json 저장 실패: {exc}", file=sys.stderr)
        return 1

    print("\n[OK] vision_cache.json을 갱신했습니다.")
    print(f"  changeLevel  = {assessment.changeLevel}")
    print(f"  trend        = {assessment.trend}")
    print(f"  confidence   = {assessment.confidence}")
    print(f"  visionFailed = {assessment.visionFailed}")
    print(f"  reason       = {assessment.reason}")
    print(f"  generatedAt  = {generated_at}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
