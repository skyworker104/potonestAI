"""재색인 후 실검증: 실제 DB 임베딩에 한국어 질의로 top-5 + 점수 분포 확인.

임계값(embedder.params) 튜닝 판단용. 활성 백엔드(embedder)로 검사한다.
"""
import sys
sys.path.insert(0, ".")
from backend import db, embedder  # noqa: E402

db.init()
print(f"백엔드: {embedder.name()}  모델: {embedder.model_id()}")
ids, emb = db.load_embeddings(embedder.model_id(), embedder.dim())
print(f"임베딩 {len(ids)}건, shape={emb.shape}\n")

QUERIES = ["바닷가", "강아지", "아기", "밤에 찍은 도시 야경", "맛있는 음식",
           "단풍", "벚꽃", "눈 내린 겨울 풍경", "커피와 카페", "산",
           "웃고 있는 사람", "가족 사진"]

import numpy as np  # noqa: E402
from backend.search import _to_english  # noqa: E402

threshold = embedder.params()["score_threshold"]

for q in QUERIES:
    qtext = _to_english(q) if embedder.needs_english() else q
    qv = embedder.encode_text([qtext])[0]
    scores = emb @ qv
    order = np.argsort(-scores)[:5]
    top = float(scores[order[0]])
    line = "  ".join(f"{ids[i].split('__')[-1][:20]}({scores[i]:.3f})" for i in order)
    verdict = "✓검색됨" if top >= threshold else "✗매칭없음"
    en = f" → '{qtext}'" if qtext != q else ""
    print(f"■ '{q}'{en} [{verdict} top={top:.3f}]\n  {line}")
