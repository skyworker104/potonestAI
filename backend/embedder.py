"""이미지 의미검색 임베딩 백엔드 선택 계층.

플랫폼에 맞는 백엔드를 자동/수동 선택한다:
  siglip     torch + SigLIP2 (다국어, 768차원) — PC/고성능 기기
  clip-onnx  onnxruntime + CLIP ViT-B/32 (영어, 512차원) — 태블릿/저사양 기기

백엔드마다 점수 분포가 달라(SigLIP2 sigmoid ~0.10-0.16, CLIP cosine ~0.2-0.33)
검색 임계값·텍스트 매칭 점수 사상도 백엔드가 함께 제공한다(params()).

DB에는 embed_model 태그가 함께 저장되어, 백엔드를 바꾸면 기존 벡터는
자동으로 재색인 대상이 된다 (다른 모델의 벡터와는 비교하지 않는다).

환경변수:
  EMBED_BACKEND  auto(기본) | siglip | clip-onnx | off
"""
import os

_cache = {"checked": False, "name": None}

# 백엔드별 검색 파라미터.
#   score_threshold  이미지 점수 절대 하한 (미만이면 매칭 없음)
#   score_margin     최고점 대비 상대 컷오프
#   text_base/span   코멘트·캡션 문장 유사도(0.42~1.0)를 이미지 점수대로 사상
#   ocr_base/span    OCR 부분일치 비율(0~1)을 이미지 점수대로 사상
_PARAMS = {
    "siglip": dict(score_threshold=0.10, score_margin=0.025,
                   text_base=0.06, text_span=0.12,
                   ocr_base=0.10, ocr_span=0.06),
    # 실측(양자화 ONNX, 실제 사진 300장): 있는 주제 top1 ~0.27-0.29,
    # 없는 주제 ~0.23. 프롬프트 템플릿("a photo of ...")은 효과 없어 미사용.
    # text/ocr 사상은 siglip 대비 이미지 점수대 비례(약 2.2배)로 환산.
    "clip-onnx": dict(score_threshold=0.24, score_margin=0.05,
                      text_base=0.15, text_span=0.22,
                      ocr_base=0.24, ocr_span=0.08),
}


def _siglip_ok():
    from . import siglip
    return siglip.available()


def _onnx_ok():
    from . import clip_onnx
    return clip_onnx.available()


def _pick():
    if _cache["checked"]:
        return _cache["name"]
    want = os.environ.get("EMBED_BACKEND", "auto").lower()
    name = None
    if want == "siglip":
        name = "siglip" if _siglip_ok() else None
    elif want in ("clip-onnx", "clip_onnx", "onnx"):
        name = "clip-onnx" if _onnx_ok() else None
    elif want != "off":  # auto
        if _siglip_ok():
            name = "siglip"
        elif _onnx_ok():
            name = "clip-onnx"
    _cache.update(checked=True, name=name)
    return name


def _impl():
    name = _pick()
    if name == "siglip":
        from . import siglip
        return siglip
    if name == "clip-onnx":
        from . import clip_onnx
        return clip_onnx
    return None


def available():
    return _pick() is not None


def name():
    return _pick()


def model_id():
    """DB embed_model 태그. siglip은 기존 DB와 호환되는 HF id를 유지."""
    n = _pick()
    if n == "siglip":
        from . import siglip
        return siglip.MODEL          # "google/siglip2-base-patch16-256"
    if n == "clip-onnx":
        from . import clip_onnx
        return clip_onnx.MODEL_TAG   # "clip-vit-base-patch32"
    return None


def dim():
    m = _impl()
    return m.DIM if m else 0


def needs_english():
    """텍스트 인코더가 영어 전용인가 (질의 번역 필요 여부)."""
    return _pick() == "clip-onnx"


def params():
    return _PARAMS.get(_pick(), _PARAMS["siglip"])


def encode_images(images, batch_size=8):
    return _impl().encode_images(images, batch_size=batch_size)


def encode_text(texts):
    return _impl().encode_text(texts)
