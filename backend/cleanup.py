"""정리 추천 — 지워도 될 만한 사진을 골라 '중복' 화면에 보여 준다.

네 묶음:
- 똑같은 사진: 내용 해시가 같은 파일 (db.duplicate_groups)
- 비슷한 연속 사진: 30초 안에 연달아 찍었고 임베딩이 거의 같은 사진 — 한 장만 남긴다
- 스크린샷: 파일 이름이 스크린샷이거나, 카메라 정보(EXIF)가 없고 화면 캡처처럼 보이며 글자가 있는 이미지
- 문서·화면을 찍은 사진: 문서·영수증·화이트보드·모니터처럼 보이고 글자가 많은 사진

판정 근거는 태블릿 라이브러리(16,474장)를 직접 보며 정했다:
- 제로샷 분류("a screenshot of …" 대 일반 사진 문구)만으로는 셀카·풍경도 많이 걸린다.
  카메라 EXIF가 있으면 화면 캡처가 아니고(모니터를 찍은 사진), 글자(OCR)가 없으면
  필터 셀카·그림이었다. 그래서 분류 + EXIF + OCR 글자 수를 함께 본다.
- PNG 여부는 근거가 안 됐다(일반 사진을 PNG로 저장한 경우가 대부분).

즐겨찾기·앨범에 넣은 사진은 추천하지 않는다(일부러 남긴 것). 비슷한 사진 묶음에서는
그런 사진을 남길 사진으로 먼저 고른다. 아무것도 지우지 않는다 — 고르는 건 사용자다.
"""
import re
from datetime import datetime

import numpy as np

from . import db, embedder, indexer

SIMILAR_SECONDS = 30
SCREENSHOT_MIN_OCR = 15     # 스크린샷 판정에 필요한 최소 글자 수 (상태바·UI 글자)
DOCUMENT_MIN_OCR = 40       # 문서 판정에 필요한 최소 글자 수

_SCREENSHOT_NAME = re.compile(r"screenshot|screen ?shot|스크린샷|캡처|capture", re.I)

_JUNK_PROMPTS = {
    "screenshot": ["a screenshot of a phone screen", "a screenshot of a chat conversation",
                   "a screenshot of a website", "a screenshot of an app"],
    "document": ["a photo of a document", "a photo of a receipt",
                 "a photo of a printed page of text", "a photo of a whiteboard",
                 "a photo of a paper form", "a photo of a handwritten note"],
}
# 비교 대상(일반 사진) — 이쪽이 더 가까우면 정리 대상이 아니다
_NORMAL_PROMPTS = [
    "a photo of people", "a photo of a person", "a photo of a child", "a group photo",
    "a selfie", "a landscape photo", "a photo of the sea", "a photo of mountains",
    "a photo of food", "a photo of an animal", "a photo of a building",
    "a photo of a city street", "a photo of a room", "a photo of a car",
    "a photo of flowers", "a photo of a sign", "a photo of a menu",
    "a photo of a product", "a photo of a cake", "a photo of a museum exhibit",
]

# 파일을 다시 읽어야 하는 값 — 파일이 그대로면 바뀌지 않으므로 서버가 도는 동안 기억한다
_camera_cache = {}      # id → 카메라 EXIF 있음
_sharp_cache = {}       # id → 선명도
_text_cache = {}        # 모델 id → (라벨 배열, 문구 임베딩)


def _prompt_matrix():
    model = embedder.model_id()
    if model not in _text_cache:
        labels, prompts = [], []
        for label, ps in _JUNK_PROMPTS.items():
            labels += [label] * len(ps)
            prompts += ps
        labels += ["normal"] * len(_NORMAL_PROMPTS)
        prompts += _NORMAL_PROMPTS
        t = np.asarray(embedder.encode_text(prompts), dtype=np.float32)
        t /= np.linalg.norm(t, axis=1, keepdims=True)
        _text_cache[model] = (np.array(labels), t)
    return _text_cache[model]


def _has_camera(mid, rel):
    if mid not in _camera_cache:
        _camera_cache[mid] = indexer.has_camera_exif(indexer.PHOTOS_DIR / rel)
    return _camera_cache[mid]


def _sharpness(mid):
    """썸네일의 라플라시안 분산 — 클수록 선명 (흔들린 사진이 작다)."""
    if mid not in _sharp_cache:
        val = 0.0
        if indexer.cv2 is not None:
            img = indexer.cv2.imread(str(indexer.THUMBS_DIR / f"{mid}.jpg"),
                                     indexer.cv2.IMREAD_GRAYSCALE)
            if img is not None:
                val = float(indexer.cv2.Laplacian(img, indexer.cv2.CV_64F).var())
        _sharp_cache[mid] = val
    return _sharp_cache[mid]


def _keeper_order(group):
    """남길 사진이 맨 앞 — 즐겨찾기·앨범 사진, 그다음 선명한 사진."""
    return sorted(group, key=lambda r: (not r["favorite"], not r["in_album"],
                                        -_sharpness(r["id"])))


def _similar_groups(rows, pos, mat, threshold):
    """촬영 시각순으로 이웃한 사진끼리 비교해 연속 촬영 묶음을 만든다."""
    shots = sorted((r for r in rows if r["id"] in pos and r["taken_at"]),
                   key=lambda r: r["taken_at"])
    groups, cur = [], shots[:1]
    for prev, r in zip(shots, shots[1:]):
        try:
            gap = (datetime.fromisoformat(r["taken_at"])
                   - datetime.fromisoformat(prev["taken_at"])).total_seconds()
        except ValueError:
            gap = None
        same = (gap is not None and 0 <= gap <= SIMILAR_SECONDS
                and r["hash"] != prev["hash"]   # 똑같은 파일은 '똑같은 사진'에서 다룬다
                and float(mat[pos[r["id"]]] @ mat[pos[prev["id"]]]) >= threshold)
        if same:
            cur.append(r)
        else:
            if len(cur) > 1:
                groups.append(cur)
            cur = [r]
    if len(cur) > 1:
        groups.append(cur)
    return [_keeper_order(g) for g in groups]


def suggestions():
    rows = db.cleanup_rows()
    item = lambda r: db.row_to_item(r)
    images = [r for r in rows if r["type"] == "image"]
    protected = lambda r: r["favorite"] or r["in_album"]

    result = {
        "duplicates": db.duplicate_groups(),
        "similar": [], "screenshots": [], "documents": [],
        "ai": embedder.available() and indexer.ai_available(),
    }

    screenshots = {r["id"] for r in images
                   if not protected(r) and _SCREENSHOT_NAME.search(r["path"])}

    if result["ai"]:
        ids, mat = db.load_embeddings(embedder.model_id(), embedder.dim())
        if len(ids):
            mat = mat / np.maximum(np.linalg.norm(mat, axis=1, keepdims=True), 1e-9)
            pos = {mid: n for n, mid in enumerate(ids)}
            threshold = embedder.params().get("similar_threshold", 0.95)
            result["similar"] = [[item(r) for r in g]
                                 for g in _similar_groups(images, pos, mat, threshold)]

            labels, prompts = _prompt_matrix()
            label = dict(zip(ids, labels[(mat @ prompts.T).argmax(1)]))
            documents = set()
            for r in images:
                lab = label.get(r["id"])
                if protected(r) or r["id"] in screenshots or lab in (None, "normal"):
                    continue
                ocr = r["ocr_len"] or 0
                if (lab == "screenshot" and ocr >= SCREENSHOT_MIN_OCR
                        and not _has_camera(r["id"], r["path"])):
                    screenshots.add(r["id"])
                elif ocr >= DOCUMENT_MIN_OCR:   # 문서처럼 보이거나, 화면을 카메라로 찍은 사진
                    documents.add(r["id"])
            result["documents"] = [item(r) for r in images if r["id"] in documents]

    result["screenshots"] = [item(r) for r in images if r["id"] in screenshots]
    return result
