"""OCR 텍스트 전량 추출(신규/미스캔 사진만, 증분).

서버가 매 색인마다 자동으로 처리하지만(indexer._run_pipeline), 이미 색인된
기존 라이브러리에 OCR을 소급 적용할 때 이 스크립트로 진행 상황을 보며 실행한다.
"""
import sys
import time
from pathlib import Path

from PIL import Image, ImageOps

sys.path.insert(0, ".")
from backend import db, indexer, ocr  # noqa: E402

db.init()
pending = db.unscanned_ocr_ids()
print(f"OCR 대상 {len(pending)}건")

t0 = time.time()
done = found = 0
for mid in pending:
    m = db.get_media(mid)
    text = None
    if m:
        try:
            p = indexer.PHOTOS_DIR / m["path"]
            img = Image.open(p)
            img = ImageOps.exif_transpose(img)
            text = ocr.extract(img)
        except Exception:
            pass
    db.set_ocr_text(mid, text)
    done += 1
    if text:
        found += 1
    if done % 200 == 0 or done == len(pending):
        rate = done / (time.time() - t0)
        eta = (len(pending) - done) / rate if rate else 0
        print(f"  {done}/{len(pending)}  텍스트발견 {found}건  ({rate:.1f}장/s, ETA {eta:.0f}s)")

print(f"완료: {done}건 처리, {found}건 텍스트 발견, {time.time()-t0:.0f}s")
