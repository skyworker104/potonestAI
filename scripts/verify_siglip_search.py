"""재색인 후 실검증: 실제 DB 임베딩(SigLIP2)에 한국어 질의로 top-5 + 점수 분포 확인.

임계값(SCORE_THRESHOLD/MARGIN) 튜닝 판단용. search.find()를 직접 호출한다.
"""
import sys
sys.path.insert(0, ".")
from backend import db, search, siglip  # noqa: E402

db.init()
ids, emb = db.load_embeddings()
print(f"임베딩 {len(ids)}건, shape={emb.shape}\n")

QUERIES = ["바닷가", "강아지", "아기", "밤에 찍은 도시 야경", "맛있는 음식",
           "단풍", "벚꽃", "눈 내린 겨울 풍경", "커피와 카페", "산",
           "웃고 있는 사람", "가족 사진"]

import numpy as np  # noqa: E402
pos = {mid: i for i, mid in enumerate(ids)}

for q in QUERIES:
    qv = siglip.encode_text([q])[0]
    scores = emb @ qv
    order = np.argsort(-scores)[:5]
    top = float(scores[order[0]])
    line = "  ".join(f"{ids[i].split('__')[-1][:20]}({scores[i]:.3f})" for i in order)
    verdict = "✓검색됨" if top >= search.SCORE_THRESHOLD else "✗매칭없음"
    print(f"■ '{q}' [{verdict} top={top:.3f}]\n  {line}")
