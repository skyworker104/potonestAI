"""신규 사진 자동 캡션(비전 LLM) — 상황/관계 질의("웃고 있는 사람", "생일 파티",
"여럿이 식사") 등 SigLIP2 제로샷만으로는 약한 영역을 보강한다.

비용이 커(실측 vl 7B 기준 장당 ~30초) 기존 라이브러리 전체 소급은 비현실적이라
**신규로 추가되는 사진에만** 적용한다(indexer._run_pipeline 참고). 명시적으로
VISION_LLM_MODEL을 설정한 경우에만 동작 — 무거운 비전 모델을 의도치 않게
매 업로드마다 자동 호출해 지연을 유발하지 않도록 기본은 꺼짐(opt-in).

환경변수:
  VISION_LLM_MODEL   LM Studio 등에 로드된 비전 모델 id (예: qwen/qwen2.5-vl-7b)
                      미설정 시 캡션 기능 자체가 비활성화된다.
"""
import base64
import io
import json
import os

from . import local_llm

PROMPT = "이 사진에 무엇이 있는지, 어떤 상황인지 한국어 한 문장으로 간결히 설명해줘."
MAX_SIDE = 640   # 캡션은 세부 텍스트가 아니라 장면 이해가 목적 — 썸네일 해상도로 충분
_TIMEOUT = 150   # 첫 호출은 비전 모델 콜드로딩 가능성


def available():
    return bool(os.environ.get("VISION_LLM_MODEL"))


def _encode(image):
    img = image.convert("RGB")
    img.thumbnail((MAX_SIDE, MAX_SIDE))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode()


def generate(image) -> "str | None":
    """PIL 이미지 → 한국어 한 문장 캡션. 비활성·실패 시 None."""
    if not available():
        return None
    base, _, _ = local_llm.discover()
    if not base:
        return None
    model = os.environ["VISION_LLM_MODEL"]
    payload = {
        "model": model,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": PROMPT},
                {"type": "image_url",
                 "image_url": {"url": f"data:image/jpeg;base64,{_encode(image)}"}},
            ],
        }],
        "max_tokens": 150, "temperature": 0.2,
    }
    try:
        d = local_llm._http_json(f"{base}/chat/completions", payload, timeout=_TIMEOUT)
        text = (d["choices"][0]["message"].get("content") or "").strip()
        return text or None
    except Exception:
        return None
