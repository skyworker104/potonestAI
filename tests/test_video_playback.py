"""동영상 위치·재생 호환.

아이폰 동영상(HEVC 10비트 HDR)은 크롬에서 소리만 나고 화면이 검다. 서버가
H.264 변환본을 만들어 두고 /media/에서 대신 준다. 또 동영상 위치(GPS)는 EXIF가
아니라 컨테이너(moov) 안 ISO 6709 문자열에 있는데, 예전엔 읽지 않아 영상의
위치가 전부 비어 있었다.
"""
import os
import struct
import time

import pytest
from fastapi.testclient import TestClient

from backend import db, indexer, main, playback


def _atom(kind, payload=b""):
    return struct.pack(">I4s", 8 + len(payload), kind) + payload


def _mov(path, moov_payload, moov_last=True):
    """ftyp + mdat + moov 최소 구조. 아이폰 MOV는 moov가 영상 데이터 뒤에 온다."""
    ftyp = _atom(b"ftyp", b"qt  \0\0\0\0qt  ")
    mdat = _atom(b"mdat", os.urandom(4096))
    moov = _atom(b"moov", moov_payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(ftyp + (mdat + moov if moov_last else moov + mdat))
    return path


APPLE_META = (b"keys....mdta com.apple.quicktime.location.ISO6709"
              b"ilst....data\0\0\0\x01\0\0\0\0+37.6438+126.9198+037.830/")
ANDROID_UDTA = _atom(b"udta", _atom(b"\xa9xyz", b"\x00\x12\x15\xc7+35.1796+129.0756/"))


@pytest.fixture
def lib(tmp_path, monkeypatch):
    root, data = tmp_path / "photos", tmp_path / "data"
    root.mkdir()
    data.mkdir()
    monkeypatch.setattr(indexer, "PHOTOS_DIR", root)
    monkeypatch.setattr(db, "DATA_DIR", data)
    monkeypatch.setattr(db, "DB_FILE", data / "photonest.db")
    db.init()
    playback._failed.clear()
    playback._urgent.clear()
    yield root


def _add_video(rel, codec, lat=None, lon=None, taken="2026-10-16T20:58:39"):
    mid = indexer.media_id_for(rel)
    with db.conn() as c:
        c.execute("INSERT INTO media (id, path, type, taken_at, lat, lon, duration, video_codec) "
                  "VALUES (?, ?, 'video', ?, ?, ?, 53.0, ?)", (mid, rel, taken, lat, lon, codec))
    return mid


# ---------- 위치 ----------

def test_iphone_location_after_media_data(lib):
    p = _mov(lib / "IMG_4539.MOV", APPLE_META, moov_last=True)
    assert indexer.video_location(p) == (37.6438, 126.9198)


def test_android_location_in_udta(lib):
    p = _mov(lib / "20260409_080036.mp4", ANDROID_UDTA, moov_last=False)
    assert indexer.video_location(p) == (35.1796, 129.0756)


def test_no_location(lib):
    assert indexer.video_location(_mov(lib / "a.mp4", _atom(b"mvhd", b"\0" * 100))) == (None, None)
    zero = _atom(b"udta", _atom(b"\xa9xyz", b"\0\x12\x15\xc7+00.0000+000.0000/"))
    assert indexer.video_location(_mov(lib / "b.mp4", zero)) == (None, None)
    (lib / "c.mp4").write_bytes(b"not a movie")
    assert indexer.video_location(lib / "c.mp4") == (None, None)


def test_backfill_fills_missing_location_only(lib):
    _mov(lib / "a.MOV", APPLE_META)
    _mov(lib / "b.MOV", APPLE_META)
    a = _add_video("a.MOV", None)
    b = _add_video("b.MOV", None, lat=1.5, lon=2.5)  # 사용자가 지도에서 지정한 위치
    assert {m for m, _, _ in db.videos_missing_codec()} == {a, b}
    for mid, rel, has_loc in db.videos_missing_codec():
        lat, lon = (None, None) if has_loc else indexer.video_location(lib / rel)
        db.set_video_info(mid, "hevc", lat, lon)
    assert db.videos_missing_codec() == []
    assert (db.get_media(a)["lat"], db.get_media(a)["lon"]) == (37.6438, 126.9198)
    assert (db.get_media(b)["lat"], db.get_media(b)["lon"]) == (1.5, 2.5)


# ---------- 재생 변환 ----------

def test_only_unplayable_codecs_are_converted(lib):
    for name, codec in [("h.mp4", "h264"), ("e.MOV", "hevc"), ("q.avi", "?"),
                        ("n.mp4", None), ("m.avi", "mp4v")]:
        _add_video(name, codec)
    assert sorted(r for _, r, _ in db.videos_to_convert(playback.PLAYABLE)) == ["e.MOV", "m.avi"]


def test_status_and_request(lib, monkeypatch):
    _mov(lib / "e.MOV", APPLE_META)
    hevc, h264 = _add_video("e.MOV", "hevc"), _add_video("h.mp4", "h264")
    started = []
    monkeypatch.setattr(playback, "start", lambda: started.append(1))

    monkeypatch.setattr(playback, "available", lambda: False)
    assert playback.request(hevc) == "unavailable"
    assert playback.request(h264) == "original"

    monkeypatch.setattr(playback, "available", lambda: True)
    assert playback.request(hevc) == "queued"
    assert list(playback._urgent) == [hevc] and started

    out = playback.cached_path(hevc)
    out.parent.mkdir(parents=True)
    out.write_bytes(b"h264")
    assert playback.request(hevc) == "ready"

    # 원본이 바뀌면(같은 이름으로 다시 백업) 옛 변환본은 쓰지 않는다
    later = time.time() + 60
    os.utime(lib / "e.MOV", (later, later))
    assert playback.status(hevc) == "queued"


def test_urgent_item_goes_first(lib):
    queue = [("a", "a.MOV", 1), ("b", "b.MOV", 1), ("c", "c.MOV", 1)]
    playback._urgent.append("c")
    assert playback._next(queue)[0] == "c"
    assert playback._next(queue)[0] == "a"


def test_media_serves_converted_copy_when_ready(lib, monkeypatch):
    src = _mov(lib / "Immich" / "IMG_4539.MOV", APPLE_META)
    mid = _add_video("Immich/IMG_4539.MOV", "hevc")
    monkeypatch.setattr(main, "_warmup_search", lambda: None)
    monkeypatch.setattr(indexer, "build_index", lambda force=False: None)
    with TestClient(main.app) as c:
        r = c.get("/media/Immich/IMG_4539.MOV")
        assert r.content == src.read_bytes()     # 변환 전 — 원본

        out = playback.cached_path(mid)
        out.parent.mkdir(parents=True)
        out.write_bytes(b"converted")
        r = c.get("/media/Immich/IMG_4539.MOV")
        assert r.content == b"converted"
        assert r.headers["content-type"] == "video/mp4"


def test_delete_and_orphan_cleanup(lib):
    mid = _add_video("e.MOV", "hevc")
    cache = playback.cached_path(mid)
    cache.parent.mkdir(parents=True)
    cache.write_bytes(b"x")
    orphan = playback.cached_path("gone")
    orphan.write_bytes(b"x")
    part = cache.with_name(cache.stem + ".part.mp4")
    part.write_bytes(b"x")
    playback._cleanup_orphans()
    assert cache.exists() and not orphan.exists() and not part.exists()
    playback.drop(mid)
    assert not cache.exists()
