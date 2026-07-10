"""SigLIP2 다국어 이미지·텍스트 인코더 (의미 검색용).

기존 CLIP ViT-B/32 + 한국어→영어 사전변환을 대체한다.
SigLIP2는 다국어(한국어 포함) 네이티브라 질의를 번역 없이 직접 이해한다.

- 모델: google/siglip2-base-patch16-256 (임베딩 768차원)
- 손실함수가 sigmoid라 CLIP보다 코사인 유사도 절대값이 낮게 분포한다
  (좋은 매칭 ~0.10-0.16). 임계값은 search.py에서 이 스케일에 맞춰 설정.
- 반환 임베딩은 L2 정규화 → 코사인 유사도 = 내적.
"""
import os

import numpy as np

MODEL = "google/siglip2-base-patch16-256"
DIM = 768
_MAX_LEN = 64  # SigLIP2 텍스트는 고정 길이 패딩 필요

_model = None
_proc = None
_device = None


def available():
    try:
        import torch  # noqa: F401
        import transformers  # noqa: F401
        return True
    except ImportError:
        return False


def _load():
    global _model, _proc, _device
    if _model is None:
        import torch
        from transformers import AutoModel, AutoProcessor

        _proc = AutoProcessor.from_pretrained(MODEL)
        _model = AutoModel.from_pretrained(MODEL)
        _model.eval()

        want = os.environ.get("SIGLIP_DEVICE")
        if want:
            _device = want
        elif torch.backends.mps.is_available():
            _device = "mps"
        else:
            _device = "cpu"
        try:
            _model.to(_device)
        except Exception:  # noqa: BLE001 — 가속기 미지원 시 CPU로 안전 폴백
            _device = "cpu"
            _model.to("cpu")
    return _model, _proc, _device


def encode_images(images, batch_size=16):
    """PIL 이미지 리스트 → (N, 768) 정규화 임베딩."""
    import torch

    model, proc, device = _load()
    out = []
    with torch.no_grad():
        for i in range(0, len(images), batch_size):
            batch = [im.convert("RGB") for im in images[i:i + batch_size]]
            inp = proc(images=batch, return_tensors="pt").to(device)
            emb = model.get_image_features(**inp)
            emb = emb / emb.norm(dim=-1, keepdim=True)
            out.append(emb.cpu().numpy().astype(np.float32))
    return np.vstack(out) if out else np.zeros((0, DIM), np.float32)


def encode_text(texts):
    """질의 문자열 리스트 → (N, 768) 정규화 임베딩. 한국어 직접 처리."""
    import torch

    model, proc, device = _load()
    with torch.no_grad():
        inp = proc(text=list(texts), return_tensors="pt",
                   padding="max_length", max_length=_MAX_LEN).to(device)
        emb = model.get_text_features(**inp)
        emb = emb / emb.norm(dim=-1, keepdim=True)
    return emb.cpu().numpy().astype(np.float32)
