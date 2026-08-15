"""USB 증분 백업·보관 통계(storage.py) 테스트 — 실제 USB 없이 tmp_path를 대상으로.

백업은 한 번 잘못되면 원본이 아니라 '백업본'이 조용히 낡는 쪽으로 망가진다.
그래서 증분 판정(크기·mtime), 대상 수집(숨김·_zips_done 제외), 재실행 시
바뀐 것만 복사, 원자적 교체, 공간 부족 사전 차단, 중지, 그리고 WAL이 살아 있는
상태의 DB 스냅샷까지 고정한다.
"""
import os
import sqlite3
import threading
from collections import namedtuple
from pathlib import Path

import pytest

from backend import db, indexer, storage

DiskUsage = namedtuple("DiskUsage", "total used free")


def w(p: Path, content=b"photo-bytes", mtime=None):
    """파일 생성 헬퍼 — 필요하면 mtime까지 고정."""
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(content)
    if mtime is not None:
        os.utime(p, (mtime, mtime))
    return p


@pytest.fixture
def lib(tmp_path, monkeypatch):
    """임시 라이브러리·DATA_DIR·USB. (photos, usb) 반환."""
    photos, data, usb = tmp_path / "photos", tmp_path / "data", tmp_path / "usb"
    for d in (photos, data, usb):
        d.mkdir(parents=True)
    monkeypatch.setattr(indexer, "PHOTOS_DIR", photos)
    monkeypatch.setattr(db, "DATA_DIR", data)
    monkeypatch.setattr(db, "DB_FILE", data / "photonest.db")
    db.init()
    # 백업 상태는 모듈 전역 — 테스트 간에 새지 않도록 초기화
    storage._bk.clear()
    storage._bk.update(state="idle")
    storage._bk_cancel.clear()
    yield photos, usb
    storage._bk_cancel.clear()


def backup_root(usb):
    return usb / storage.BACKUP_DIR_NAME / "photos"


# ---------- 증분 판정 (_same_file) ----------

def test_same_file_missing_destination(tmp_path):
    assert not storage._same_file(tmp_path / "없음.jpg", 10, 1000.0)


def test_same_file_identical(tmp_path):
    p = w(tmp_path / "a.jpg", b"12345", mtime=1000.0)
    assert storage._same_file(p, p.stat().st_size, 1000.0)


def test_same_file_size_differs(tmp_path):
    p = w(tmp_path / "a.jpg", b"12345", mtime=1000.0)
    assert not storage._same_file(p, 99, 1000.0)


def test_same_file_mtime_within_fat_resolution(tmp_path):
    """exFAT는 수정시각 해상도가 2초 — 그 안의 차이는 같은 파일로 본다."""
    p = w(tmp_path / "a.jpg", b"12345", mtime=1000.0)
    assert storage._same_file(p, p.stat().st_size, 1002.0)


def test_same_file_mtime_beyond_tolerance(tmp_path):
    p = w(tmp_path / "a.jpg", b"12345", mtime=1000.0)
    assert not storage._same_file(p, p.stat().st_size, 1005.0)


# ---------- 백업 대상 수집 ----------

def test_collect_preserves_relative_paths(lib):
    photos, _ = lib
    w(photos / "2025" / "여름" / "a.jpg")
    w(photos / "b.jpg")
    rels = {str(rel) for _, rel, _, _ in storage._collect_library_files()}
    assert rels == {os.path.join("2025", "여름", "a.jpg"), "b.jpg"}


def test_collect_skips_hidden_and_done_zips(lib):
    photos, _ = lib
    w(photos / "keep.jpg")
    w(photos / ".DS_Store")
    w(photos / ".hidden" / "x.jpg")
    w(photos / "_zips_done" / "takeout.zip")
    rels = {str(rel) for _, rel, _, _ in storage._collect_library_files()}
    assert rels == {"keep.jpg"}


# ---------- 백업 실행 ----------

def test_first_run_copies_everything(lib):
    photos, usb = lib
    w(photos / "2025" / "a.jpg", b"AAA")
    w(photos / "b.jpg", b"BB")

    storage._run_backup(usb)

    assert storage._bk["state"] == "done"
    assert storage._bk["copied"] == 2
    assert storage._bk["skipped"] == 0
    assert storage._bk["failed"] == 0
    assert (backup_root(usb) / "2025" / "a.jpg").read_bytes() == b"AAA"
    assert (backup_root(usb) / "b.jpg").read_bytes() == b"BB"


def test_second_run_copies_nothing(lib):
    """증분의 핵심 — 바뀐 게 없으면 재실행해도 한 장도 다시 쓰지 않는다."""
    photos, usb = lib
    w(photos / "a.jpg", b"AAA")
    storage._run_backup(usb)
    storage._run_backup(usb)

    assert storage._bk["state"] == "done"
    assert storage._bk["copied"] == 0
    assert storage._bk["skipped"] == 1


def test_changed_file_recopied(lib):
    """내용이 바뀐 것만 다시 복사되고, 백업본이 최신으로 갱신된다."""
    photos, usb = lib
    a = w(photos / "a.jpg", b"OLD", mtime=1000.0)
    w(photos / "b.jpg", b"BB", mtime=1000.0)
    storage._run_backup(usb)

    w(a, b"NEW-CONTENT", mtime=2000.0)
    storage._run_backup(usb)

    assert storage._bk["copied"] == 1
    assert storage._bk["skipped"] == 1
    assert (backup_root(usb) / "a.jpg").read_bytes() == b"NEW-CONTENT"


def test_no_temp_files_left_behind(lib):
    """복사는 임시파일 → os.replace. 끝난 뒤 .pncopy- 잔여물이 없어야 한다."""
    photos, usb = lib
    w(photos / "2025" / "a.jpg", b"AAA")
    storage._run_backup(usb)

    leftovers = [p.name for p in usb.rglob(".pncopy-*")]
    assert leftovers == []


def test_aborts_when_usb_too_small(lib, monkeypatch):
    """공간이 모자라면 한 장도 쓰지 않고 미리 멈춘다(반쯤 찬 백업 방지)."""
    photos, usb = lib
    w(photos / "a.jpg", b"A" * 500)
    monkeypatch.setattr(storage.shutil, "disk_usage",
                        lambda p: DiskUsage(1000, 990, 10))

    storage._run_backup(usb)

    assert storage._bk["state"] == "error"
    assert "공간 부족" in storage._bk["error"]
    assert not backup_root(usb).exists()


def test_cancel_stops_before_copying(lib):
    photos, usb = lib
    w(photos / "a.jpg", b"AAA")
    storage._bk_cancel.set()

    storage._run_backup(usb)

    assert storage._bk["state"] == "cancelled"
    assert storage._bk["copied"] == 0


def test_cancelled_backup_resumes_incrementally(lib):
    """중지 후 다시 돌리면 이미 받은 것은 건너뛰고 남은 것만 이어서 받는다."""
    photos, usb = lib
    w(photos / "a.jpg", b"AAA")
    w(photos / "b.jpg", b"BBB")
    storage._run_backup(usb)          # 둘 다 복사
    (backup_root(usb) / "b.jpg").unlink()  # b만 못 받은 상태를 재현

    storage._run_backup(usb)

    assert storage._bk["copied"] == 1
    assert storage._bk["skipped"] == 1
    assert (backup_root(usb) / "b.jpg").read_bytes() == b"BBB"


# ---------- DB 스냅샷 ----------

def test_backup_includes_db_snapshot(lib):
    photos, usb = lib
    w(photos / "a.jpg", b"AAA")
    with db.conn() as c:
        c.execute("INSERT INTO media (id, path, type) VALUES ('m1','a.jpg','photo')")

    storage._run_backup(usb)

    snap = usb / storage.BACKUP_DIR_NAME / "data" / "photonest.db"
    assert snap.is_file()
    c = sqlite3.connect(snap)
    try:
        assert c.execute("SELECT COUNT(*) FROM media").fetchone()[0] == 1
    finally:
        c.close()


def test_snapshot_captures_wal_contents(lib):
    """WAL이 살아 있는(체크포인트 전) 상태에서도 스냅샷에 데이터가 들어가야 한다.

    photonest.db 파일만 단순 복사하면 WAL에만 있는 행이 통째로 빠진다 —
    sqlite backup API를 쓰는 이유이자, 그 선택을 고정하는 회귀 테스트.
    """
    _, usb = lib
    live = sqlite3.connect(db.DB_FILE)
    dest = usb / "data"
    try:
        live.execute("PRAGMA journal_mode=WAL")
        live.execute("INSERT INTO media (id, path, type) VALUES ('w1','w.jpg','photo')")
        live.commit()
        storage._snapshot_db(dest)  # 연결을 연 채로 = WAL 미체크포인트
    finally:
        live.close()

    c = sqlite3.connect(dest / "photonest.db")
    try:
        assert c.execute("SELECT COUNT(*) FROM media WHERE id='w1'").fetchone()[0] == 1
    finally:
        c.close()


def test_snapshot_copies_skills(lib):
    _, usb = lib
    (db.DATA_DIR / "skills.json").write_text('[{"id":"sk_1"}]', encoding="utf-8")

    storage._snapshot_db(usb / "data")

    assert (usb / "data" / "skills.json").read_text(encoding="utf-8") == '[{"id":"sk_1"}]'


# ---------- 시작 가드 ----------

def test_start_rejects_path_outside_removable_volumes(lib, monkeypatch):
    """감지된 이동식 볼륨이 아니면 거부 — 임의 경로로 쓰기를 유도할 수 없다."""
    _, usb = lib
    monkeypatch.setattr(storage, "_removable_volumes", list)

    r = storage.backup_start(storage.BackupStart(path=str(usb)))

    assert r["ok"] is False
    assert not (usb / storage.BACKUP_DIR_NAME).exists()


def test_start_rejects_when_already_running(lib, monkeypatch):
    _, usb = lib
    monkeypatch.setattr(storage, "_removable_volumes",
                        lambda: [{"path": str(usb)}])
    storage._bk_set(state="running")

    assert storage.backup_start(storage.BackupStart(path=str(usb)))["ok"] is False


def test_start_accepts_detected_volume(lib, monkeypatch):
    _, usb = lib
    monkeypatch.setattr(storage, "_removable_volumes",
                        lambda: [{"path": str(usb)}])
    started, ran = [], threading.Event()

    def stub(target):
        started.append(target)
        ran.set()

    monkeypatch.setattr(storage, "_run_backup", stub)

    assert storage.backup_start(storage.BackupStart(path=str(usb)))["ok"] is True
    assert ran.wait(5), "백업 스레드가 시작되지 않았다"
    assert str(started[0]) == str(usb)


# ---------- 보관 통계 ----------

def test_stats_splits_photos_videos_trash(lib):
    photos, _ = lib
    w(photos / "a.jpg", b"A" * 10)
    w(photos / "v.mp4", b"V" * 20)
    w(db.DATA_DIR / "trash" / "t.jpg", b"T" * 30)
    with db.conn() as c:
        c.execute("INSERT INTO media (id, path, type) VALUES ('1','a.jpg','photo')")
        c.execute("INSERT INTO media (id, path, type) VALUES ('2','v.mp4','video')")
        c.execute(
            "INSERT INTO media (id, path, type, trashed_at, trash_path) "
            "VALUES ('3','gone.jpg','photo','2026-01-01','trash/t.jpg')")

    s = storage.storage_stats()

    assert s["photos"] == {"count": 1, "bytes": 10}
    assert s["videos"] == {"count": 1, "bytes": 20}
    assert s["trash"] == {"count": 1, "bytes": 30}


def test_stats_excludes_trash_from_app_data(lib):
    """휴지통 원본은 trash에 이미 잡히므로 앱 데이터에 이중 계상되면 안 된다.

    색인 DB 자체도 앱 데이터라 절대값 대신 증가분으로 본다.
    """
    base = storage.storage_stats()["data_bytes"]

    w(db.DATA_DIR / "trash" / "big.jpg", b"Y" * 5000)
    assert storage.storage_stats()["data_bytes"] == base      # 휴지통은 빠지고

    w(db.DATA_DIR / "thumbs" / "t1.jpg", b"X" * 40)
    assert storage.storage_stats()["data_bytes"] == base + 40  # 썸네일은 잡힌다


def test_stats_survives_missing_files(lib):
    """DB에 있는데 파일이 사라진 경우에도 0바이트로 세고 죽지 않는다."""
    photos, _ = lib
    with db.conn() as c:
        c.execute("INSERT INTO media (id, path, type) VALUES ('x','없는파일.jpg','photo')")

    s = storage.storage_stats()

    assert s["photos"] == {"count": 1, "bytes": 0}
