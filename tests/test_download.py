"""선택한 사진 원본 내려받기 — 한 장은 원본, 여러 장은 스트리밍 ZIP."""
import io
import zipfile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import db, indexer, main


@pytest.fixture
def client(tmp_path, monkeypatch):
    photos, data = tmp_path / "photos", tmp_path / "data"
    (photos / "a").mkdir(parents=True)
    (photos / "b").mkdir(parents=True)
    (data / "trash").mkdir(parents=True)
    monkeypatch.setattr(db, "DATA_DIR", data)
    monkeypatch.setattr(db, "DB_FILE", data / "photonest.db")
    monkeypatch.setattr(indexer, "PHOTOS_DIR", photos)
    db.init()
    (photos / "a" / "IMG_1.jpg").write_bytes(b"AAAA")
    (photos / "b" / "IMG_1.jpg").write_bytes(b"BBBB" * 300_000)   # 1.2MB — 여러 조각
    (data / "trash" / "t.jpg").write_bytes(b"TTTT")
    (tmp_path / "secret.txt").write_text("no")
    rows = [("m1", "a/IMG_1.jpg"), ("m2", "b/IMG_1.jpg"), ("m3", "gone.jpg"),
            ("m4", "../secret.txt"), ("m5", "x/t.jpg")]
    for mid, path in rows:
        db.upsert_media(dict(id=mid, path=path, type="image", taken_at=None, lat=None,
                             lon=None, width=1, height=1, duration=None, sig=mid, hash=mid))
    with db.conn() as c:  # m5는 휴지통에 있는 사진
        c.execute("UPDATE media SET trashed_at='2026-01-01', trash_path='trash/t.jpg' WHERE id='m5'")
    app = FastAPI()
    app.post("/api/download")(main.download)
    return TestClient(app)


def test_single_photo_comes_back_as_the_original(client):
    r = client.post("/api/download", data={"ids": "m1"})
    assert r.status_code == 200 and r.content == b"AAAA"
    assert "IMG_1.jpg" in r.headers["content-disposition"]


def test_many_photos_come_back_as_a_zip_with_unique_names(client):
    r = client.post("/api/download", data={"ids": "m1,m2,m5"})
    assert r.headers["content-type"] == "application/zip"
    assert "attachment" in r.headers["content-disposition"]
    z = zipfile.ZipFile(io.BytesIO(r.content))
    assert sorted(z.namelist()) == ["IMG_1.jpg", "IMG_1_1.jpg", "t.jpg"]
    assert z.read("IMG_1_1.jpg") == b"BBBB" * 300_000   # 같은 이름은 번호를 붙인다
    assert z.read("t.jpg") == b"TTTT"                    # 휴지통 사진도 받을 수 있다


def test_missing_files_and_paths_outside_the_library_are_skipped(client):
    r = client.post("/api/download", data={"ids": "m3,m4,없는id"})
    assert r.status_code == 404
