"""앱 설정 영구 저장 — 대화형 LLM 엔진 선택 및 OpenRouter 연동.

설정은 data/app/settings.json에 저장한다(단일 로컬 사용자 앱).
API 키는 평문 저장되므로 파일 권한을 0600으로 제한하고,
외부로 반환할 때는 반드시 mask()로 마스킹한다.

우선순위: settings.json 값 → 환경변수 폴백 → 기본값.
  engine_mode        "auto" | "openrouter" | "local" | "claude"
  openrouter_api_key  (폴백: env OPENROUTER_API_KEY)
  openrouter_model    OpenRouter 모델 id (예: anthropic/claude-3.5-haiku)
"""
import json
import os
import threading
from pathlib import Path

from . import db

_FILE = db.DATA_DIR / "app" / "settings.json"
_LOCK = threading.Lock()

# OpenRouter OpenAI 호환 엔드포인트
OPENROUTER_BASE = "https://openrouter.ai/api/v1"

# 검색 의도 파싱은 가볍고 빠른 모델로 충분 — UI 프리셋과 동일하게 유지
DEFAULT_OPENROUTER_MODEL = "google/gemini-2.5-flash-lite"

_DEFAULTS = {
    "engine_mode": "auto",
    "openrouter_api_key": "",
    "openrouter_model": DEFAULT_OPENROUTER_MODEL,
}

VALID_MODES = ("auto", "openrouter", "local", "claude")


def _read_file():
    try:
        with open(_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def load():
    """현재 설정(dict). 파일 없으면 기본값+env 폴백."""
    cfg = dict(_DEFAULTS)
    cfg.update({k: v for k, v in _read_file().items() if k in _DEFAULTS})
    # 파일에 키가 없으면 환경변수로 폴백
    if not cfg["openrouter_api_key"]:
        cfg["openrouter_api_key"] = os.environ.get("OPENROUTER_API_KEY", "")
    if cfg["engine_mode"] not in VALID_MODES:
        cfg["engine_mode"] = "auto"
    return cfg


def save(patch: dict):
    """부분 업데이트 후 저장. 유효한 키만 반영. 저장된 전체 설정 반환."""
    with _LOCK:
        current = dict(_DEFAULTS)
        current.update({k: v for k, v in _read_file().items() if k in _DEFAULTS})

        if "engine_mode" in patch:
            mode = patch["engine_mode"]
            if mode not in VALID_MODES:
                raise ValueError(f"engine_mode must be one of {VALID_MODES}")
            current["engine_mode"] = mode
        if "openrouter_model" in patch:
            current["openrouter_model"] = (patch["openrouter_model"] or "").strip() \
                or DEFAULT_OPENROUTER_MODEL
        if "openrouter_api_key" in patch:
            key = patch["openrouter_api_key"]
            # None/빈 문자열이 아니고 마스킹 값이 아닐 때만 갱신
            if key and "…" not in key and "*" not in key:
                current["openrouter_api_key"] = key.strip()
            elif key == "":  # 명시적 비우기
                current["openrouter_api_key"] = ""

        _FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(_FILE, "w", encoding="utf-8") as f:
            json.dump(current, f, ensure_ascii=False, indent=2)
        try:
            os.chmod(_FILE, 0o600)  # API 키 평문 저장 → 소유자만 읽기
        except OSError:
            pass
    return current


def mask_key(key: str):
    """API 키 마스킹: 'sk-or-v1-abcd…wxyz'. 없으면 빈 문자열."""
    if not key:
        return ""
    if len(key) <= 12:
        return "…" + key[-4:]
    return key[:8] + "…" + key[-4:]


def public(cfg=None):
    """외부 반환용 — API 키는 마스킹, 설정 여부 플래그 포함."""
    cfg = cfg or load()
    key = cfg.get("openrouter_api_key", "")
    return {
        "engine_mode": cfg["engine_mode"],
        "openrouter_model": cfg["openrouter_model"],
        "openrouter_key_masked": mask_key(key),
        "openrouter_key_set": bool(key),
    }
