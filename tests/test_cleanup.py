"""정리 추천 (cleanup.py) — 판정 규칙 고정.

규칙은 태블릿 라이브러리를 직접 보며 정했다: 제로샷 분류만 믿으면 셀카·풍경이 걸려서,
카메라 EXIF(있으면 화면 캡처가 아니다)와 OCR 글자 수(없으면 필터 셀카·그림)를 함께 본다.
임베딩은 기저 벡터로 흉내 낸다: 스크린샷 문구 e0, 문서 문구 e1, 일반 사진 문구 e2.
"""
import numpy as np
import piexif
import pytest
from PIL import Image

from backend import cleanup, db, embedder, indexer

E = np.eye(4, dtype=np.float32)
SHOT, DOC, NORMAL = E[0], E[1], E[2]


@pytest.fixture
def lib(tmp_path, monkeypatch):
    monkeypatch.setattr(indexer, "PHOTOS_DIR", tmp_path / "photos")
    monkeypatch.setattr(indexer, "THUMBS_DIR", tmp_path / "thumbs")
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_FILE", tmp_path / "photonest.db")
    db.init()

    def encode_text(prompts):
        return np.stack([SHOT if "screenshot" in p else
                         DOC if p in cleanup._JUNK_PROMPTS["document"] else NORMAL
                         for p in prompts])
    monkeypatch.setattr(embedder, "available", lambda: True)
    monkeypatch.setattr(embedder, "model_id", lambda: "fake")
    monkeypatch.setattr(embedder, "dim", lambda: 4)
    monkeypatch.setattr(embedder, "params", lambda: {"similar_threshold": 0.95})
    monkeypatch.setattr(embedder, "encode_text", encode_text)
    monkeypatch.setattr(indexer, "ai_available", lambda: True)
    cameras = set()
    monkeypatch.setattr(indexer, "has_camera_exif", lambda p: p.name in cameras)
    for cache in (cleanup._camera_cache, cleanup._sharp_cache, cleanup._text_cache):
        cache.clear()
    yield cameras


def add(mid, vec=None, ocr="", taken="2026-10-01T10:00:00", path=None, fav=0, hash_=None,
        pix="", lat=None, added=None):
    with db.conn() as c:
        c.execute(
            "INSERT INTO media (id, path, type, taken_at, favorite, hash, ocr_text, "
            "pixel_hash, lat, added_at) VALUES (?, ?, 'image', ?, ?, ?, ?, ?, ?, ?)",
            (mid, path or f"{mid}.jpg", taken, fav, hash_ or mid, ocr, pix, lat, added))
    if vec is not None:
        db.set_embedding(mid, np.asarray(vec, dtype=np.float32), "fake")


def ids(items):
    return sorted(it["id"] for it in items)


def test_screenshot_rules(lib):
    cameras = lib
    add("named", NORMAL, path="Screenshot_20231101_KakaoTalk.jpg")   # 이름만으로
    add("capture", SHOT, ocr="오후 3:12 카카오톡 대화 내용")           # 카메라 없음 + 글자
    add("filter_selfie", SHOT, ocr="")                                # 글자 없음 → 셀카·그림
    add("monitor", SHOT, ocr="x" * 60)                                # 카메라로 찍은 모니터
    cameras.add("monitor.jpg")
    add("phone_photo", SHOT, ocr="짧은글")                             # 카메라 + 글자 적음
    cameras.add("phone_photo.jpg")
    r = cleanup.suggestions()
    assert ids(r["screenshots"]) == ["capture", "named"]
    assert ids(r["documents"]) == ["monitor"]   # 화면을 찍은 사진은 문서·화면 묶음으로


def test_document_needs_enough_text(lib):
    add("receipt", DOC, ocr="영수증 " * 20)
    add("cat", DOC, ocr="")          # 분류가 문서여도 글자가 없으면 일반 사진
    add("landscape", NORMAL, ocr="간판 " * 30)   # 글자가 많아도 일반 사진처럼 보이면 제외
    assert ids(cleanup.suggestions()["documents"]) == ["receipt"]


def test_favorites_and_album_photos_are_not_suggested(lib):
    add("fav_shot", NORMAL, path="Screenshot_1.jpg", fav=1)
    add("album_doc", DOC, ocr="문서 " * 30)
    album = db.create_album("서류")
    db.add_to_album(album, ["album_doc"])
    r = cleanup.suggestions()
    assert r["screenshots"] == [] and r["documents"] == []


def test_similar_burst_keeps_favorite_first(lib):
    near = NORMAL + 0.05 * E[3]
    add("a", NORMAL, taken="2026-10-01T10:00:00")
    add("b", near, taken="2026-10-01T10:00:10", fav=1)
    add("c", NORMAL, taken="2026-10-01T10:00:25")
    add("later", NORMAL, taken="2026-10-01T10:05:00")        # 30초를 넘김
    add("other", DOC, taken="2026-10-01T10:05:05")           # 다른 장면
    add("copy", NORMAL, taken="2026-10-01T10:05:06", hash_="later")  # 같은 파일 — '똑같은 사진'
    r = cleanup.suggestions()
    assert [[it["id"] for it in g] for g in r["similar"]] == [["b", "a", "c"]]
    assert [ids(g) for g in r["duplicates"]] == [["copy", "later"]]


def test_without_ai_only_filenames(lib, monkeypatch):
    monkeypatch.setattr(indexer, "ai_available", lambda: False)
    add("named", None, path="스크린샷 2026-10-01.png")
    add("receipt", DOC, ocr="영수증 " * 20)
    r = cleanup.suggestions()
    assert ids(r["screenshots"]) == ["named"]
    assert r["documents"] == [] and r["similar"] == [] and r["ai"] is False


def test_has_camera_exif(tmp_path):
    shot = tmp_path / "shot.jpg"
    Image.new("RGB", (8, 8)).save(shot)
    photo = tmp_path / "photo.jpg"
    exif = piexif.dump({"0th": {piexif.ImageIFD.Make: b"Apple",
                                piexif.ImageIFD.Model: b"iPhone 16"}})
    Image.new("RGB", (8, 8)).save(photo, exif=exif)
    assert not indexer.has_camera_exif(shot)
    assert indexer.has_camera_exif(photo)
    assert not indexer.has_camera_exif(tmp_path / "missing.jpg")


def test_identical_pixels_grouped_apart_from_file_copies(lib):
    # 원본(위치정보 있음) · EXIF를 지운 사본 · 다른 이름으로 다시 저장한 PNG — 픽셀은 같다
    add("orig", pix="P1", lat=33.4, added="2026-10-02T00:00:00")
    add("stripped", pix="P1", added="2026-10-01T00:00:00")
    add("png", pix="P1", path="png.png", added="2026-10-03T00:00:00")
    add("orig_copy", pix="P1", hash_="orig")            # 같은 파일 사본 — '똑같은 사진'에서
    add("fav_a", pix="P2", added="2026-10-05T00:00:00", fav=1)
    add("fav_b", pix="P2", added="2026-10-01T00:00:00")
    add("alone", pix="P3")
    add("same_file", pix="P4")
    add("same_file2", pix="P4", hash_="same_file")      # 파일까지 같으면 이 묶음엔 없다
    add("unread", pix="")                               # 못 읽은 사진은 묶지 않는다
    add("unread2", pix="")
    r = cleanup.suggestions()
    groups = sorted([[it["id"] for it in g] for g in r["identical"]])
    assert groups == [["fav_a", "fav_b"], ["orig", "stripped", "png"]]
    assert sorted(ids(g) for g in r["duplicates"]) == [["orig", "orig_copy"],
                                                       ["same_file", "same_file2"]]
    assert r["identical_pending"] == 0


def test_identical_pixels_not_repeated_as_similar(lib):
    add("a", NORMAL, taken="2026-10-01T10:00:00", pix="P")
    add("b", NORMAL, taken="2026-10-01T10:00:05", pix="P")
    r = cleanup.suggestions()
    assert r["similar"] == [] and len(r["identical"]) == 1


def test_pixel_hash_ignores_format_and_exif(tmp_path):
    img = Image.new("RGB", (16, 16), (10, 120, 200))
    img.putpixel((3, 3), (255, 0, 0))
    plain = tmp_path / "plain.png"
    img.save(plain)
    tagged = tmp_path / "tagged.png"
    img.save(tagged, exif=piexif.dump({"0th": {piexif.ImageIFD.Make: b"Apple"}}))
    other = tmp_path / "other.png"
    img2 = img.copy()
    img2.putpixel((0, 0), (0, 0, 0))
    img2.save(other)
    h = indexer._pixel_hash_file
    assert h(plain) == h(tagged) != h(other)
    assert h(tmp_path / "missing.jpg") == ""


def test_backfill_counts_missing(lib):
    with db.conn() as c:
        c.execute("INSERT INTO media (id, path, type) VALUES ('old', 'old.jpg', 'image')")
    assert db.count_missing_pixel_hash() == 1
    assert db.images_missing_pixel_hash() == [("old", "old.jpg")]
    db.set_pixel_hash("old", None)
    assert db.count_missing_pixel_hash() == 0
