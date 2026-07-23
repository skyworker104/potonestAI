"""Gemini 엔진(B안) 유닛 테스트 — 설정 저장/마스킹/모드, 어댑터 가용성."""
import json

import pytest

from backend import gemini, settings


@pytest.fixture
def settings_file(tmp_path, monkeypatch):
    f = tmp_path / "settings.json"
    monkeypatch.setattr(settings, "_FILE", f)
    # 환경변수 폴백이 테스트를 오염시키지 않게 제거
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    return f


def test_gemini_mode_valid(settings_file):
    cfg = settings.save({"engine_mode": "gemini"})
    assert cfg["engine_mode"] == "gemini"


def test_gemini_key_and_model_saved(settings_file):
    settings.save({"gemini_api_key": "AIzaTest123456789", "gemini_model": "gemini-2.5-flash"})
    cfg = settings.load()
    assert cfg["gemini_api_key"] == "AIzaTest123456789"
    assert cfg["gemini_model"] == "gemini-2.5-flash"
    # 파일에 실제로 반영됐는지
    on_disk = json.loads(settings_file.read_text())
    assert on_disk["gemini_api_key"] == "AIzaTest123456789"


def test_gemini_key_masked_in_public(settings_file):
    settings.save({"gemini_api_key": "AIzaTest123456789"})
    pub = settings.public()
    assert pub["gemini_key_set"] is True
    assert "AIzaTest123456789" not in json.dumps(pub)  # 원문 키 노출 금지
    assert pub["gemini_key_masked"].startswith("AIzaTest")


def test_masked_value_does_not_overwrite_key(settings_file):
    settings.save({"gemini_api_key": "AIzaOriginal12345"})
    settings.save({"gemini_api_key": "AIzaOrig…2345"})  # 마스킹 값 재전송 → 유지
    assert settings.load()["gemini_api_key"] == "AIzaOriginal12345"
    settings.save({"gemini_api_key": ""})  # 명시적 비우기
    assert settings.load()["gemini_api_key"] == ""


def test_empty_model_falls_back_to_default(settings_file):
    settings.save({"gemini_model": "  "})
    assert settings.load()["gemini_model"] == settings.DEFAULT_GEMINI_MODEL


def test_env_fallback(settings_file, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "AIzaFromEnv123")
    assert settings.load()["gemini_api_key"] == "AIzaFromEnv123"


def test_available_requires_key(settings_file):
    assert not gemini.available()
    settings.save({"gemini_api_key": "AIzaTest123456789"})
    assert gemini.available()


def test_parse_returns_none_without_key(settings_file):
    """키 없으면 네트워크 호출 없이 None → 호출부가 다음 엔진으로 폴백."""
    assert gemini.parse("강아지 사진 찾아줘") is None
    assert gemini.translate("강아지") is None


def test_test_key_rejects_empty():
    ok, msg = gemini.test_key("")
    assert not ok
