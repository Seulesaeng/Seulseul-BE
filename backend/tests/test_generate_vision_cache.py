# backend/scripts/generate_vision_cache.py 단위 테스트.
# 실제 OpenAI API는 호출하지 않는다 (call_vision_model을 monkeypatch로 대체).
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import generate_vision_cache as gvc


@pytest.fixture(autouse=True)
def _isolate_from_real_dotenv(monkeypatch):
    # backend/.env may contain real credentials on a developer machine. main()'s own
    # load_dotenv() call must not resurrect env vars a test intentionally deleted/unset,
    # and tests must never trigger a real OpenAI call as a side effect.
    monkeypatch.setattr(gvc, "load_dotenv", lambda *args, **kwargs: False)


def test_select_images_matches_runtime_baseline_and_recent_selection():
    images = gvc.select_images("album-001")
    assert len(images) == 4
    assert images[0].role == "BASELINE"
    assert images[0].photoId == "photo_01"
    assert [img.photoId for img in images[1:]] == ["photo_04", "photo_05", "photo_06"]
    assert all(img.role == "RECENT" for img in images[1:])
    for img in images:
        assert img.path.is_file()


def test_select_images_unknown_album_raises():
    with pytest.raises(RuntimeError):
        gvc.select_images("album-unknown")


def test_encode_image_data_uri_produces_valid_data_uri():
    images = gvc.select_images("album-001")
    data_uri = gvc._encode_image_data_uri(images[0].path)
    assert data_uri.startswith("data:image/jpeg;base64,")
    assert len(data_uri) > 100


def test_encode_image_data_uri_missing_file_raises():
    with pytest.raises(FileNotFoundError):
        gvc._encode_image_data_uri(Path("does_not_exist.jpg"))


def test_build_input_has_one_text_part_and_four_image_parts():
    images = gvc.select_images("album-001")
    payload = gvc._build_input(images)
    assert len(payload) == 1
    assert payload[0]["role"] == "user"
    content = payload[0]["content"]
    assert content[0]["type"] == "input_text"
    image_parts = [c for c in content if c["type"] == "input_image"]
    assert len(image_parts) == 4
    for part in image_parts:
        assert part["image_url"].startswith("data:image/")
        assert part["detail"] == "auto"


def test_write_cache_creates_new_file_with_expected_fields(tmp_path, monkeypatch):
    cache_path = tmp_path / "vision_cache.json"
    monkeypatch.setattr(gvc, "CACHE_PATH", cache_path)

    assessment = gvc.VisionAssessment(
        changeLevel="HIGH", trend="INCREASING", confidence="MEDIUM", reason="테스트 사유", visionFailed=False
    )
    gvc.write_cache("album-001", assessment, "2026-08-10T09:41:00+09:00")

    data = json.loads(cache_path.read_text(encoding="utf-8"))
    entry = data["album-001"]
    assert entry == {
        "changeLevel": "HIGH",
        "trend": "INCREASING",
        "confidence": "MEDIUM",
        "reason": "테스트 사유",
        "visionFailed": False,
        "generatedAt": "2026-08-10T09:41:00+09:00",
    }


def test_write_cache_preserves_other_albums(tmp_path, monkeypatch):
    cache_path = tmp_path / "vision_cache.json"
    cache_path.write_text(json.dumps({"album-999": {"changeLevel": "LOW"}}), encoding="utf-8")
    monkeypatch.setattr(gvc, "CACHE_PATH", cache_path)

    assessment = gvc.VisionAssessment(
        changeLevel="LOW", trend="STABLE", confidence="LOW", reason="사유", visionFailed=False
    )
    gvc.write_cache("album-001", assessment, "2026-08-10T09:41:00+09:00")

    data = json.loads(cache_path.read_text(encoding="utf-8"))
    assert "album-999" in data
    assert "album-001" in data


def test_main_does_not_overwrite_cache_on_vision_call_failure(tmp_path, monkeypatch, capsys):
    cache_path = tmp_path / "vision_cache.json"
    original_content = json.dumps({"album-001": {"changeLevel": "MEDIUM", "generatedAt": "old"}})
    cache_path.write_text(original_content, encoding="utf-8")
    monkeypatch.setattr(gvc, "CACHE_PATH", cache_path)
    monkeypatch.setenv("VISION_MODEL", "gpt-4o-mini")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-dummy-should-not-be-logged")

    def _boom(images, model):
        raise RuntimeError("simulated API failure")

    monkeypatch.setattr(gvc, "call_vision_model", _boom)
    monkeypatch.setattr("sys.argv", ["generate_vision_cache.py", "--album-id", "album-001"])

    exit_code = gvc.main()

    assert exit_code == 1
    assert cache_path.read_text(encoding="utf-8") == original_content  # 기존 캐시 그대로 보존

    captured = capsys.readouterr()
    assert "sk-test-dummy-should-not-be-logged" not in captured.out
    assert "sk-test-dummy-should-not-be-logged" not in captured.err


def test_main_dry_run_does_not_call_api_or_touch_cache(tmp_path, monkeypatch):
    cache_path = tmp_path / "vision_cache.json"
    monkeypatch.setattr(gvc, "CACHE_PATH", cache_path)
    monkeypatch.setattr("sys.argv", ["generate_vision_cache.py", "--dry-run"])

    exit_code = gvc.main()

    assert exit_code == 0
    assert not cache_path.exists()


def test_main_fails_when_vision_model_env_missing(tmp_path, monkeypatch):
    cache_path = tmp_path / "vision_cache.json"
    monkeypatch.setattr(gvc, "CACHE_PATH", cache_path)
    monkeypatch.delenv("VISION_MODEL", raising=False)
    monkeypatch.setattr("sys.argv", ["generate_vision_cache.py"])

    exit_code = gvc.main()

    assert exit_code == 1
    assert not cache_path.exists()


def test_call_vision_model_fails_fast_without_api_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    images = gvc.select_images("album-001")
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        gvc.call_vision_model(images, "gpt-4o-mini")
