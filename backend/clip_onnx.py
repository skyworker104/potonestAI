"""CLIP ViT-B/32 ONNX 인코더 — torch 없이 onnxruntime로 이미지·텍스트 임베딩.

저사양 기기(안드로이드 태블릿 Termux 등)용 경량 백엔드.
모델은 Xenova/clip-vit-base-patch32 (transformers.js와 동일 가중치·양자화)라서
폰 브라우저(transformers.js)가 만든 벡터와 그대로 호환된다.

- 임베딩 512차원, L2 정규화 → 코사인 유사도 = 내적
- 텍스트 인코더는 영어 전용 — 한국어 질의는 search.py가 영어로 변환해 넘긴다
- 기본 양자화(int8) 모델(~150MB). CLIP_ONNX_FP32=1이면 fp32(~600MB)

환경변수:
  CLIP_ONNX_DIR    모델 저장 위치 (기본 data/models/clip-onnx)
  CLIP_ONNX_FP32   1이면 비양자화 모델 사용
"""
import os
import urllib.request
from pathlib import Path

import numpy as np

from . import db

MODEL_TAG = "clip-vit-base-patch32"   # DB embed_model 태그 (폰 오프로드와 호환 키)
DIM = 512
_HF = "https://huggingface.co/Xenova/clip-vit-base-patch32/resolve/main"
_MAX_TOKENS = 77
_IMG_SIZE = 224
# CLIP 표준 정규화 상수
_MEAN = np.array([0.48145466, 0.4578275, 0.40821073], dtype=np.float32)
_STD = np.array([0.26862954, 0.26130258, 0.27577711], dtype=np.float32)

_dir = Path(os.environ.get("CLIP_ONNX_DIR", db.DATA_DIR / "models" / "clip-onnx"))
_sessions = {"text": None, "vision": None, "tokenizer": None}


def _quant_suffix():
    return "" if os.environ.get("CLIP_ONNX_FP32") == "1" else "_quantized"


def _files():
    q = _quant_suffix()
    return {
        "tokenizer.json": f"{_HF}/tokenizer.json",
        f"text_model{q}.onnx": f"{_HF}/onnx/text_model{q}.onnx",
        f"vision_model{q}.onnx": f"{_HF}/onnx/vision_model{q}.onnx",
    }


def available():
    try:
        import onnxruntime  # noqa: F401
        import tokenizers  # noqa: F401
        return True
    except ImportError:
        return False


def _download():
    """모델 파일이 없으면 내려받는다 (양자화 기준 총 ~150MB, 1회)."""
    _dir.mkdir(parents=True, exist_ok=True)
    for name, url in _files().items():
        dst = _dir / name
        if dst.exists() and dst.stat().st_size > 0:
            continue
        tmp = dst.with_suffix(dst.suffix + ".part")
        urllib.request.urlretrieve(url, tmp)  # noqa: S310 — 고정 HF 저장소
        tmp.rename(dst)


def _load():
    if _sessions["vision"] is not None:
        return _sessions
    import onnxruntime as ort
    from tokenizers import Tokenizer

    _download()
    q = _quant_suffix()
    opts = ort.SessionOptions()
    # 저RAM 기기: 스레드 과점 방지 (기본은 코어 수만큼 잡아 메모리 압박)
    opts.intra_op_num_threads = int(os.environ.get("CLIP_ONNX_THREADS", "2"))
    _sessions["text"] = ort.InferenceSession(
        str(_dir / f"text_model{q}.onnx"), opts, providers=["CPUExecutionProvider"])
    _sessions["vision"] = ort.InferenceSession(
        str(_dir / f"vision_model{q}.onnx"), opts, providers=["CPUExecutionProvider"])
    tok = Tokenizer.from_file(str(_dir / "tokenizer.json"))
    tok.enable_truncation(max_length=_MAX_TOKENS)
    tok.enable_padding(length=_MAX_TOKENS, pad_id=49407, pad_token="<|endoftext|>")
    _sessions["tokenizer"] = tok
    return _sessions


def _pick_output(session, *names):
    """ONNX 출력 중 프로젝션 임베딩 이름을 찾는다 (내보내기 방식에 따라 다름)."""
    outs = [o.name for o in session.get_outputs()]
    for n in names:
        if n in outs:
            return n
    return outs[0]  # 폴백: 첫 출력


def _preprocess(image):
    """PIL 이미지 → (3,224,224) float32 CLIP 정규화 텐서."""
    from PIL import Image
    img = image.convert("RGB")
    # 짧은 변을 224로 리사이즈 후 중앙 크롭 (CLIP 표준)
    w, h = img.size
    scale = _IMG_SIZE / min(w, h)
    img = img.resize((round(w * scale), round(h * scale)), Image.BICUBIC)
    w, h = img.size
    left, top = (w - _IMG_SIZE) // 2, (h - _IMG_SIZE) // 2
    img = img.crop((left, top, left + _IMG_SIZE, top + _IMG_SIZE))
    arr = np.asarray(img, dtype=np.float32) / 255.0
    arr = (arr - _MEAN) / _STD
    return arr.transpose(2, 0, 1)  # HWC → CHW


def _normalize(mat):
    norm = np.linalg.norm(mat, axis=-1, keepdims=True)
    norm[norm == 0] = 1.0
    return (mat / norm).astype(np.float32)


def encode_images(images, batch_size=8):
    """PIL 이미지 리스트 → (N, 512) 정규화 임베딩."""
    s = _load()
    out_name = _pick_output(s["vision"], "image_embeds")
    out = []
    for i in range(0, len(images), batch_size):
        batch = np.stack([_preprocess(im) for im in images[i:i + batch_size]])
        emb = s["vision"].run([out_name], {"pixel_values": batch})[0]
        out.append(_normalize(emb))
    return np.vstack(out) if out else np.zeros((0, DIM), np.float32)


def encode_text(texts):
    """영어 질의 리스트 → (N, 512) 정규화 임베딩."""
    s = _load()
    out_name = _pick_output(s["text"], "text_embeds")
    encs = [s["tokenizer"].encode(t) for t in texts]
    feeds = {"input_ids": np.array([e.ids for e in encs], dtype=np.int64)}
    # 내보내기에 따라 attention_mask 입력이 없을 수 있다 (EOS 풀링 내장)
    declared = {i.name for i in s["text"].get_inputs()}
    if "attention_mask" in declared:
        feeds["attention_mask"] = np.array([e.attention_mask for e in encs], dtype=np.int64)
    emb = s["text"].run([out_name], feeds)[0]
    return _normalize(emb)
