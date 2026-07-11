"""임베딩 전량 재생성 — 활성 백엔드(embedder)로 전 이미지를 다시 인코딩한다.

백엔드/모델이 바뀌면 차원·점수 스케일이 달라 벡터가 비호환이므로
전량 교체한다(부분 혼합 불가). 평소에는 서버의 AI 색인이 모델 태그
불일치분을 자동 재색인하므로 이 스크립트는 강제 초기화용이다.
"""
import sys
import time
from pathlib import Path

from PIL import Image

sys.path.insert(0, ".")
from backend import db, embedder  # noqa: E402

THUMBS = Path("data/thumbs")

db.init()
MODEL = embedder.model_id()
print(f"백엔드: {embedder.name()}  모델: {MODEL}  차원: {embedder.dim()}")

# 1) 기존 임베딩 제거
with db.conn() as c:
    n = c.execute(
        "SELECT count(*) FROM media WHERE embedding IS NOT NULL"
    ).fetchone()[0]
    c.execute("UPDATE media SET embedding=NULL, embed_model=NULL")
print(f"기존 임베딩 {n}건 제거")

# 2) 활성 백엔드로 전량 재인코딩
ids = db.missing_embedding_ids(MODEL)
print(f"재인코딩 대상 {len(ids)}건")

t0 = time.time()
done = 0
BATCH = 16
for i in range(0, len(ids), BATCH):
    batch = ids[i:i + BATCH]
    images, valid = [], []
    for mid in batch:
        tp = THUMBS / f"{mid}.jpg"
        if tp.exists():
            images.append(Image.open(tp))
            valid.append(mid)
    if not images:
        continue
    vecs = embedder.encode_images(images, batch_size=BATCH)
    for mid, v in zip(valid, vecs):
        db.set_embedding(mid, v, MODEL)
    done += len(valid)
    if done % 320 == 0 or i + BATCH >= len(ids):
        rate = done / (time.time() - t0)
        eta = (len(ids) - done) / rate if rate else 0
        print(f"  {done}/{len(ids)}  ({rate:.1f}장/s, ETA {eta:.0f}s)")

print(f"완료: {done}건, {time.time()-t0:.0f}s")

# 3) 검증
with db.conn() as c:
    cnt = c.execute("SELECT count(embedding) FROM media WHERE embedding IS NOT NULL").fetchone()[0]
_, mat = db.load_embeddings(MODEL, embedder.dim())
print(f"임베딩 보유 {cnt}건, 행렬 shape={mat.shape}")
