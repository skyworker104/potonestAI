"""CLIP 임베딩 → SigLIP2 임베딩 전량 재생성.

기존 512차원 CLIP 임베딩을 지우고, SigLIP2(768차원)로 전 이미지를 다시 인코딩한다.
차원이 바뀌므로 반드시 전량 교체(부분 혼합 불가).
"""
import sys
import time
from pathlib import Path

from PIL import Image

sys.path.insert(0, ".")
from backend import db, siglip  # noqa: E402

THUMBS = Path("data/thumbs")

db.init()

# 1) 기존 CLIP 임베딩 제거
with db.conn() as c:
    n = c.execute(
        "SELECT count(*) FROM media WHERE embedding IS NOT NULL"
    ).fetchone()[0]
    c.execute("UPDATE media SET embedding=NULL")
print(f"기존 임베딩 {n}건 제거")

# 2) SigLIP2로 전량 재인코딩
ids = db.missing_embedding_ids()
print(f"재인코딩 대상 {len(ids)}건  (device={siglip._load()[2]})")

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
    vecs = siglip.encode_images(images, batch_size=BATCH)
    for mid, v in zip(valid, vecs):
        db.set_embedding(mid, v)
    done += len(valid)
    if done % 320 == 0 or i + BATCH >= len(ids):
        rate = done / (time.time() - t0)
        eta = (len(ids) - done) / rate if rate else 0
        print(f"  {done}/{len(ids)}  ({rate:.1f}장/s, ETA {eta:.0f}s)")

print(f"완료: {done}건, {time.time()-t0:.0f}s")

# 3) 검증
with db.conn() as c:
    cnt = c.execute("SELECT count(embedding) FROM media WHERE embedding IS NOT NULL").fetchone()[0]
_, mat = db.load_embeddings()
print(f"임베딩 보유 {cnt}건, 행렬 shape={mat.shape}")
