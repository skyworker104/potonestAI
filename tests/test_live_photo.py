"""아이폰 라이브 포토 — 사진과 2~3초 동영상을 한 장으로.

라이브 포토는 사진(JPG/HEIC)과 짧은 MOV 두 파일로 올라온다. 예전엔 MOV가 따로
동영상으로 1,500여 개 보였다. 또 태블릿 서버(proot)의 시간대가 UTC라 아이폰 영상의
촬영 시각이 모두 9시간 이르게 기록돼 있었다 — MOV 안의 현지 시각 문자열을 쓴다.
"""
import struct

import pytest
from fastapi.testclient import TestClient

from backend import db, indexer, main


def _atom(kind, payload=b""):
    return struct.pack(">I4s", 8 + len(payload), kind) + payload


def _mov(path, local_date=None, live=True):
    meta = b"keys"
    if live:
        meta += b"mdta com.apple.quicktime.content.identifier"
    if local_date:
        meta += b"mdta com.apple.quicktime.creationdate"
    meta += b"ilst"
    if live:
        meta += b"data\0\0\0\x01\0\0\0\0" + b"A1B2C3D4-0000-4000-8000-1234567890AB"
    if local_date:
        meta += b"data\0\0\0\x01\0\0\0\0" + local_date.encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_atom(b"ftyp", b"qt  \0\0\0\0qt  ") + _atom(b"mdat", b"\0" * 512)
                     + _atom(b"moov", _atom(b"meta", meta)))


@pytest.fixture
def lib(tmp_path, monkeypatch):
    root, data = tmp_path / "photos", tmp_path / "data"
    root.mkdir()
    monkeypatch.setattr(indexer, "PHOTOS_DIR", root)
    monkeypatch.setattr(indexer, "TRASH_DIR", data / "trash")
    monkeypatch.setattr(indexer, "THUMBS_DIR", data / "thumbs")
    monkeypatch.setattr(indexer, "DATA_DIR", data)
    monkeypatch.setattr(db, "DATA_DIR", data)
    monkeypatch.setattr(db, "DB_FILE", data / "photonest.db")
    db.init()
    return root


def add(root, rel, kind, taken, duration=0.0, write=True):
    mid = indexer.media_id_for(rel)
    if write and kind == "image":
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(b"jpg")
    with db.conn() as c:
        c.execute("INSERT INTO media (id, path, type, taken_at, duration) VALUES (?, ?, ?, ?, ?)",
                  (mid, rel, kind, taken, duration))
    return mid


def run_check():
    for mid, rel, taken, dur in db.videos_to_live_check():
        db.set_live_checked(mid, *indexer.check_video(mid, rel, taken, dur))


def test_iphone_video_time_is_local(lib):
    _mov(lib / "a.MOV", "2026-10-09T18:03:18+0900")
    assert indexer.video_creation_time(lib / "a.MOV") == "2026-10-09T18:03:18"


def test_pairs_with_the_photo_taken_at_the_same_moment(lib):
    d = "MobileBackup/Immich"
    _mov(lib / d / "IMG_0010.MOV", "2026-10-09T18:03:18+0900")
    # UTC로 잘못 기록 — mvhd는 현지 시각 문자열보다 2초 늦기도 하다(실제 IMG_1580)
    vid = add(lib, f"{d}/IMG_0010.MOV", "video", "2026-10-09T09:03:20", 2.9)
    photo = add(lib, f"{d}/IMG_0010_1.HEIC", "image", "2026-10-09T18:03:22")   # 4초 뒤
    old = add(lib, f"{d}/IMG_0010.JPG", "image", "2024-03-23T17:34:53")       # 이름만 같은 옛 사진
    run_check()
    v, p = db.get_media(vid), db.get_media(photo)
    assert v["taken_at"] == "2026-10-09T18:03:18"           # 9시간 정정
    assert p["live_video"] == vid and db.get_media(old)["live_video"] is None
    shown = {it["id"]: it for it in db.list_photos()}
    assert vid not in shown and shown[photo]["live_video"] == vid
    assert db.timeline_months()["total"] == 2
    assert db.videos_to_live_check() == []


def test_ordinary_short_video_is_not_paired(lib):
    _mov(lib / "IMG_1.MOV", "2026-10-09T18:03:18+0900", live=False)
    vid = add(lib, "IMG_1.MOV", "video", "2026-10-09T18:03:18", 2.0)
    add(lib, "IMG_1.JPG", "image", "2026-10-09T18:03:18")
    run_check()
    assert vid in {it["id"] for it in db.list_photos()}
    assert db.videos_to_live_check() == []


def test_live_video_waits_for_its_photo(lib):
    _mov(lib / "IMG_2.MOV", "2026-10-09T18:03:18+0900")
    vid = add(lib, "IMG_2.MOV", "video", "2026-10-09T18:03:18", 2.5)
    run_check()
    assert [r[0] for r in db.videos_to_live_check()] == [vid]   # 사진이 아직 안 왔다
    photo = add(lib, "IMG_2.JPG", "image", "2026-10-09T18:03:19")
    run_check()
    assert db.get_media(photo)["live_video"] == vid


def test_time_is_fixed_only_for_timezone_shifts(lib):
    _mov(lib / "b.MOV", "2026-10-09T18:03:18+0900", live=False)
    vid = add(lib, "b.MOV", "video", "2026-10-01T12:00:00", 30.0)   # 다른 근거로 정한 시각
    run_check()
    assert db.get_media(vid)["taken_at"] == "2026-10-01T12:00:00"


def test_trash_restore_delete_move_together(lib):
    _mov(lib / "IMG_3.MOV", "2026-10-09T18:03:18+0900")
    vid = add(lib, "IMG_3.MOV", "video", "2026-10-09T18:03:18", 2.5)
    photo = add(lib, "IMG_3.JPG", "image", "2026-10-09T18:03:18")
    run_check()

    assert indexer.move_to_trash(photo)
    assert db.get_media(vid)["trashed_at"] and not (lib / "IMG_3.MOV").exists()
    assert [it["id"] for it in db.list_trash()] == [photo]     # 휴지통에도 한 장
    assert indexer.restore_from_trash(photo)
    assert not db.get_media(vid)["trashed_at"] and (lib / "IMG_3.MOV").exists()

    indexer.move_to_trash(photo)
    assert indexer.delete_permanently(photo)
    assert db.get_media(vid) is None


def test_live_endpoint_serves_the_motion(lib, monkeypatch):
    _mov(lib / "IMG_4.MOV", "2026-10-09T18:03:18+0900")
    vid = add(lib, "IMG_4.MOV", "video", "2026-10-09T18:03:18", 2.5)
    photo = add(lib, "IMG_4.JPG", "image", "2026-10-09T18:03:18")
    run_check()
    monkeypatch.setattr(main, "_warmup_search", lambda: None)
    monkeypatch.setattr(indexer, "build_index", lambda force=False: None)
    with TestClient(main.app) as c:
        assert c.get(f"/api/media/{photo}/live").content == (lib / "IMG_4.MOV").read_bytes()
        assert c.get(f"/api/media/{vid}/live").status_code == 404


def test_search_live_photos_only(lib):
    from backend import search
    _mov(lib / "IMG_5.MOV", "2026-10-09T18:03:18+0900")
    add(lib, "IMG_5.MOV", "video", "2026-10-09T18:03:18", 2.5)
    photo = add(lib, "IMG_5.JPG", "image", "2026-10-09T18:03:18")
    add(lib, "IMG_6.JPG", "image", "2026-10-09T18:10:00")
    run_check()
    assert [it["id"] for it in search.find(None, kind="live")] == [photo]
