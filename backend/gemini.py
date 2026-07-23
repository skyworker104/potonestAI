"""Google Gemini 어댑터 — OpenAI 호환 엔드포인트라 local_llm 로직을 재사용한다.

Google AI Studio(https://aistudio.google.com/apikey) 키로 직접 호출.
무료 티어(일일 한도)가 있어 OpenRouter 중계 없이 비용 0원 운용이 가능하다.
키/모델은 settings에서 읽는다. 의도 파싱(텍스트)에만 사용.
"""
import urllib.error

from . import local_llm, settings

# 검색 의도 파싱용 추천 모델 — 무료 티어 한도가 넉넉한 Flash 계열 위주 (2026-07 기준)
PRESET_MODELS = [
    {"id": "gemini-2.5-flash-lite", "label": "Gemini 2.5 Flash Lite (무료 한도 큼·빠름)"},
    {"id": "gemini-2.5-flash", "label": "Gemini 2.5 Flash (해석 더 정확)"},
    {"id": "gemini-2.0-flash", "label": "Gemini 2.0 Flash (구세대·안정)"},
]


def config():
    """(api_key, model) — 키가 없으면 (None, model)."""
    cfg = settings.load()
    return cfg.get("gemini_api_key") or None, cfg.get("gemini_model")


def available():
    key, model = config()
    return bool(key and model)


def parse(message, history=None, today=None):
    """발화→의도 dict. 키 미설정/실패 시 None(호출부가 폴백)."""
    key, model = config()
    if not key or not model:
        return None
    return local_llm.parse(
        message, history=history, today=today,
        base=settings.GEMINI_BASE, model=model, key=key,
    )


def translate(text, timeout=15):
    """한국어 사진검색 주제어 → 간결한 영어 키워드. 실패/키없음 시 None.

    CLIP 텍스트 인코더(영어 전용) 백엔드에서 검색 질의 변환에 쓴다.
    """
    key, model = config()
    if not key or not model:
        return None
    payload = {
        "model": model,
        "messages": [
            {"role": "system",
             "content": "Translate the Korean photo-search keywords into concise "
                        "English keywords for an image search engine. "
                        "Output ONLY the English keywords, nothing else."},
            {"role": "user", "content": text},
        ],
        "temperature": 0, "max_tokens": 60,
    }
    try:
        d = local_llm._http_json(f"{settings.GEMINI_BASE}/chat/completions",
                                 payload, timeout=timeout, key=key)
        out = (d["choices"][0]["message"].get("content") or "").strip()
        return out or None
    except Exception:
        return None


def test_key(key: str, model: str = None):
    """키 유효성 확인. (ok, message) 반환.

    /models 는 Gemini OpenAI 호환 표면에서 인증 필수라 키 검증에 적합하고,
    모델 존재 확인까지 한 번에 된다 (목록 id는 'models/' 접두사가 붙음).
    """
    key = (key or "").strip()
    if not key:
        return False, "API 키를 입력하세요."
    try:
        d = local_llm._http_json(f"{settings.GEMINI_BASE}/models", timeout=10, key=key)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return False, "키가 유효하지 않습니다 (인증 실패). AI Studio에서 키를 확인하세요."
        return False, f"Gemini API 오류 (HTTP {e.code})."
    except Exception as e:  # noqa: BLE001 — 네트워크/타임아웃 등 사용자에게 메시지로 전달
        return False, f"연결 실패: {e}"

    if model:
        ids = {m.get("id", "") for m in d.get("data", [])}
        # 목록은 'models/gemini-…' 형태 — 접두사 유무 모두 허용
        if model not in ids and f"models/{model}" not in ids:
            return True, f"키 유효 · 단 '{model}' 모델을 목록에서 찾지 못했습니다 (모델 id를 확인하세요)."
    return True, f"연결 성공 · 모델 '{model or '기본'}' 사용 가능."
