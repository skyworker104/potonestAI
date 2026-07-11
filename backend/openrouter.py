"""OpenRouter 어댑터 — OpenAI 호환 API라 local_llm 로직을 그대로 재사용한다.

키/모델은 settings에서 읽는다. 의도 파싱(텍스트)에만 사용.
"""
import urllib.error
import urllib.request

from . import local_llm, settings

# OpenRouter 순위 노출용(선택) 헤더
_HEADERS = {
    "HTTP-Referer": "http://localhost:8765",
    "X-Title": "PhotoNest AI",
}

# 검색 의도 파싱용 추천 모델 — 가볍고 빠르고 저렴한 것 위주.
# 실제 카탈로그는 수시로 바뀌므로 직접 입력도 UI에서 허용한다 (2026-07 기준 검증).
PRESET_MODELS = [
    {"id": "google/gemini-2.5-flash-lite", "label": "Gemini 2.5 Flash Lite (저렴·빠름)"},
    {"id": "anthropic/claude-haiku-4.5", "label": "Claude Haiku 4.5 (안정)"},
    {"id": "openai/gpt-4o-mini", "label": "GPT-4o mini (저렴)"},
    {"id": "meta-llama/llama-3.3-70b-instruct", "label": "Llama 3.3 70B (오픈)"},
]


def config():
    """(api_key, model) — 키가 없으면 (None, model)."""
    cfg = settings.load()
    return cfg.get("openrouter_api_key") or None, cfg.get("openrouter_model")


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
        base=settings.OPENROUTER_BASE, model=model, key=key, extra_headers=_HEADERS,
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
        d = local_llm._http_json(f"{settings.OPENROUTER_BASE}/chat/completions",
                                 payload, timeout=timeout, key=key,
                                 extra_headers=_HEADERS)
        out = (d["choices"][0]["message"].get("content") or "").strip()
        return out or None
    except Exception:
        return None


def test_key(key: str, model: str = None):
    """키 유효성 확인. (ok, message) 반환.

    /key 는 인증이 필요한 엔드포인트라 키 검증에 적합(/models는 공개라 부적합).
    모델 존재 여부는 공개 /models 목록으로 별도 확인한다.
    """
    key = (key or "").strip()
    if not key:
        return False, "API 키를 입력하세요."
    # 1) 키 인증 확인
    try:
        info = local_llm._http_json(f"{settings.OPENROUTER_BASE}/key", timeout=10,
                                    key=key, extra_headers=_HEADERS)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return False, "키가 유효하지 않습니다 (인증 실패)."
        return False, f"OpenRouter 오류 (HTTP {e.code})."
    except Exception as e:  # noqa: BLE001 — 네트워크/타임아웃 등 사용자에게 메시지로 전달
        return False, f"연결 실패: {e}"

    # 2) 모델 존재 확인 (공개 목록)
    if model:
        try:
            d = local_llm._http_json(f"{settings.OPENROUTER_BASE}/models", timeout=10,
                                     key=key, extra_headers=_HEADERS)
            ids = {m.get("id") for m in d.get("data", [])}
            if model not in ids:
                return True, f"키 유효 · 단 '{model}' 모델을 목록에서 찾지 못했습니다 (모델 id를 확인하세요)."
        except Exception:  # noqa: BLE001 — 모델 확인 실패는 치명적이지 않음
            pass

    label = (info.get("data") or {}).get("label")
    return True, f"연결 성공{' · ' + label if label else ''} · 모델 '{model or '기본'}' 사용 가능."
