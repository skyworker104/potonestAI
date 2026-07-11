"""의미 검색(임베딩 백엔드) + 코멘트 문장 검색 + 메타데이터 폴백 + 날짜/타입 필터.

이미지 의미 검색 백엔드는 embedder가 플랫폼에 맞게 선택한다:
  - SigLIP2(torch): 다국어 — 한국어 질의를 번역 없이 직접 이해
  - CLIP-ONNX(저사양): 영어 전용 — 질의를 영어로 변환해 검색(_to_english)
점수 임계값·사상 파라미터는 백엔드별 분포가 달라 embedder.params()가 제공한다.
AI가 꺼져 있으면(저사양 기기) 파일명·날짜 기반 검색으로 동작한다.
"""
import re
from datetime import datetime

import numpy as np

from . import db, indexer

_comment_model = None

COMMENT_THRESHOLD = 0.42  # 코멘트(문장 의미) 매칭 하한 — 백엔드와 무관한 문장 모델
MAX_RESULTS = 60

# 질의 영어 변환 캐시 (CLIP 백엔드용 — 스킬 재사용 시 같은 주제어가 반복됨)
_en_cache = {}
_HANGUL = re.compile(r"[가-힣]")


def _to_english(text):
    """한국어 검색 주제어 → 영어 키워드 (CLIP 텍스트 인코더가 영어 전용).

    우선순위: OpenRouter 번역(품질) → 내장 KO_EN 사전 → 원문.
    결과는 프로세스 수명 동안 캐시한다.
    """
    if not _HANGUL.search(text):
        return text  # 이미 영어/숫자
    if text in _en_cache:
        return _en_cache[text]
    en = None
    try:
        from . import openrouter
        en = openrouter.translate(text)
    except Exception:
        pass
    if not en:
        from . import llm
        en = llm._ko_to_en(text)
    _en_cache[text] = en or text
    return _en_cache[text]


def get_comment_model():
    """코멘트 문장 의미 검색용 인코더.

    CLIP 텍스트 인코더는 한국어 문장 변별력이 약해, 문장 유사도 전용
    다국어 모델을 별도로 쓴다 (코멘트↔질의 의미 매칭 전용).
    """
    global _comment_model
    if _comment_model is None:
        from sentence_transformers import SentenceTransformer
        _comment_model = SentenceTransformer("paraphrase-multilingual-MiniLM-L12-v2")
    return _comment_model


def embed_comment(text):
    """코멘트를 문장 인코더로 임베딩 (저장 시 호출). 실패 시 None."""
    text = (text or "").strip()
    if not text or not indexer.text_ai_available():
        return None
    try:
        return get_comment_model().encode(
            [text], convert_to_numpy=True, normalize_embeddings=True
        )[0]
    except Exception:
        return None


def _in_date_range(item, date_from, date_to):
    if not (date_from or date_to):
        return True
    try:
        t = datetime.fromisoformat(item["taken_at"])
    except (ValueError, TypeError):
        return False
    if date_from and t < datetime.fromisoformat(date_from):
        return False
    if date_to and t > datetime.fromisoformat(date_to):
        return False
    return True


def _metadata_find(search_text, candidates, top_k):
    """AI 비활성 시 폴백: 파일명/경로 키워드 매칭."""
    if not search_text:
        return candidates[:top_k]
    words = [w.lower() for w in re.split(r"\s+", search_text) if w]
    hits = [
        it for it in candidates
        if any(w in it["path"].lower() for w in words)
    ]
    return hits[:top_k]


def _comment_matches(raw_query, allowed_ids, by_id, top_k):
    """질의와 코멘트의 의미 유사도로 매칭된 (item, score) 목록."""
    if not raw_query or not indexer.text_ai_available():
        return []
    ids, emb = db.comment_embeddings()
    if not ids:
        return []
    try:
        qv = get_comment_model().encode(
            [raw_query], convert_to_numpy=True, normalize_embeddings=True
        )[0]
    except Exception:
        return []
    sims = emb @ qv
    out = []
    for i, mid in enumerate(ids):
        if mid in allowed_ids and sims[i] >= COMMENT_THRESHOLD:
            out.append((by_id[mid], float(sims[i])))
    out.sort(key=lambda x: -x[1])
    return out[:top_k]


def _caption_matches(raw_query, allowed_ids, by_id, top_k):
    """질의와 자동 캡션(비전 LLM 생성)의 의미 유사도 매칭. 신규 사진만 캡션이 있다."""
    if not raw_query or not indexer.text_ai_available():
        return []
    ids, emb = db.caption_embeddings()
    if not ids:
        return []
    try:
        qv = get_comment_model().encode(
            [raw_query], convert_to_numpy=True, normalize_embeddings=True
        )[0]
    except Exception:
        return []
    sims = emb @ qv
    out = []
    for i, mid in enumerate(ids):
        if mid in allowed_ids and sims[i] >= COMMENT_THRESHOLD:
            out.append((by_id[mid], float(sims[i])))
    out.sort(key=lambda x: -x[1])
    return out[:top_k]


def _ocr_matches(search_text, allowed_ids, by_id, top_k):
    """질의 단어가 사진 속 글자(OCR)에 그대로 포함되는 사진 매칭.

    OCR 텍스트는 문장이 아니라 스캔된 단편이라 임베딩 유사도보다
    부분일치(포함 여부)가 더 정확하고 예측 가능하다.
    (item, 일치비율 0~1) 반환 — 점수 사상은 호출부가 백엔드 스케일로 한다.
    """
    if not search_text:
        return []
    words = [w for w in re.split(r"\s+", search_text.strip()) if len(w) >= 2]
    if not words:
        return []
    rows = db.ocr_texts()
    if not rows:
        return []
    out = []
    for mid, text in rows:
        if mid not in allowed_ids:
            continue
        low = text.lower()
        hit_n = sum(1 for w in words if w.lower() in low)
        if hit_n:
            out.append((by_id[mid], hit_n / len(words)))
    out.sort(key=lambda x: -x[1])
    return out[:top_k]


def find(search_text, date_from=None, date_to=None, media_type=None,
         raw_query=None, only_ids=None, bbox=None, exclude_ids=None, top_k=MAX_RESULTS):
    from . import places
    pool = db.list_photos(ids=only_ids, limit=100000) if only_ids is not None \
        else db.list_photos(limit=100000)
    exclude = set(exclude_ids or [])
    candidates = [
        it for it in pool
        if _in_date_range(it, date_from, date_to)
        and (media_type in (None, "", "all") or it["type"] == media_type)
        and it["id"] not in exclude
        # 지명(위치) 검색이면 GPS가 해당 지역 안인 사진만 (정확한 장소 판정)
        and (bbox is None or places.in_bbox(it["lat"], it["lon"], bbox))
    ]
    if not candidates:
        return []

    if not search_text:
        # 검색어 없이 위치/인물/날짜만 → 최신순
        return [dict(it, score=None) for it in candidates[:top_k]]

    if not indexer.ai_available():
        return [dict(it, score=None) for it in _metadata_find(search_text, candidates, top_k)]

    by_id = {it["id"]: it for it in candidates}
    allowed_ids = set(by_id)

    from . import embedder, skills
    P = embedder.params()

    # 1) 이미지 의미 검색 (같은 모델로 만든 벡터만 — 백엔드 간 비호환)
    image_hits = {}  # id → score
    ids, emb = db.load_embeddings(embedder.model_id(), embedder.dim())
    pos = {mid: i for i, mid in enumerate(ids)}
    idxs = [pos[it["id"]] for it in candidates if it["id"] in pos]
    cand = [it for it in candidates if it["id"] in pos]
    # 지시어("사진 찾아줘" 등)를 제거한 핵심 주제어로 인코딩한다.
    # 문장 전체를 넣으면 임베딩이 희석돼 점수가 임계 근처로 떨어짐
    # ("강아지 사진 찾아줘" 0.10 vs "강아지" 0.13).
    subject = skills._core(search_text)
    if idxs:
        qtext = _to_english(subject) if embedder.needs_english() else subject
        qv = embedder.encode_text([qtext])[0]
        scores = emb[idxs] @ qv
        order = np.argsort(-scores)
        top = float(scores[order[0]])
        if top >= P["score_threshold"]:
            cutoff = max(P["score_threshold"], top - P["score_margin"])
            for oi in order[:top_k]:
                s = float(scores[oi])
                if s < cutoff:
                    break
                image_hits[cand[oi]["id"]] = s

    # 2) 코멘트 의미 검색 (텍스트↔텍스트). 원문 질의 우선 사용
    comment_hits = _comment_matches(
        raw_query or search_text, allowed_ids, by_id, top_k
    )

    # 3) OCR(사진 속 글자) 검색 — 핵심 주제어 기준 부분일치
    ocr_hits = _ocr_matches(subject, allowed_ids, by_id, top_k)

    # 4) 자동 캡션 의미 검색 (비전 LLM이 생성한 문장 — 신규 사진만 존재)
    caption_hits = _caption_matches(
        raw_query or search_text, allowed_ids, by_id, top_k
    )

    # 5) 병합 — 코멘트/캡션은 문장 설명이므로 강한 매칭은 상위에 오도록.
    #    문장 유사도(0.42~1.0)·OCR 일치비율(0~1)을 백엔드 이미지 점수대로 사상
    merged = dict(image_hits)
    for it, csim in comment_hits:
        cscore = P["text_base"] + (csim - COMMENT_THRESHOLD) * P["text_span"]
        merged[it["id"]] = max(merged.get(it["id"], 0.0), cscore)
    for it, frac in ocr_hits:
        oscore = P["ocr_base"] + P["ocr_span"] * frac
        merged[it["id"]] = max(merged.get(it["id"], 0.0), oscore)
    for it, csim in caption_hits:
        cscore = P["text_base"] + (csim - COMMENT_THRESHOLD) * P["text_span"]
        merged[it["id"]] = max(merged.get(it["id"], 0.0), cscore)

    if not merged:
        return []
    ranked = sorted(merged.items(), key=lambda kv: -kv[1])[:top_k]
    return [dict(by_id[mid], score=round(s, 3)) for mid, s in ranked]
