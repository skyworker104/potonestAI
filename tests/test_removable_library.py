"""사진 원본을 SD 카드에 둘 때의 안전장치.

카드가 빠진 채 서버가 켜지면 사진 폴더가 비어 보인다. 그때 색인이 돌면
remove_missing이 라이브러리 전체(앨범·즐겨찾기·인물 연결)를 DB에서 지운다.
또 원본은 카드를 빼서 PC 탐색기로 열 수 있어야 하므로, exFAT·Windows가
거부하는 이름으로 저장하면 안 된다.
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import db, indexer, upload


@pytest.fixture
def lib(tmp_path, monkeypatch):
    root, data = tmp_path / "sdcard" / "PhotoNest", tmp_path / "data"
    data.mkdir(parents=True)
    monkeypatch.setattr(indexer, "PHOTOS_DIR", root)
    monkeypatch.setattr(db, "DATA_DIR", data)
    monkeypatch.setattr(db, "DB_FILE", data / "photonest.db")
    db.init()
    monkeypatch.setattr(upload, "_trigger_index", lambda: None)
    upload._recent_hashes.clear()
    yield root
    upload._recent_hashes.clear()


def _add_media_row(path="MobileBackup/phone/a.jpg"):
    with db.conn() as c:
        c.execute("INSERT INTO media (id, path, type) VALUES (?, ?, 'image')", ("m1", path))


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(upload.router)
    return TestClient(app)


# ---------- 연결 판정 ----------

def test_new_empty_library_is_online_and_gets_marker(lib):
    assert indexer.library_online()
    assert (lib / indexer.LIBRARY_MARKER).is_file()


def test_existing_folder_without_marker_is_adopted(lib):
    # 표식 도입 전부터 쓰던 폴더 — 파일이 있으면 그대로 인정하고 표식을 단다
    _add_media_row()
    (lib / "MobileBackup").mkdir(parents=True)
    assert indexer.library_online()
    assert (lib / indexer.LIBRARY_MARKER).is_file()


def test_missing_card_is_offline(lib):
    _add_media_row()           # DB에는 사진이 있는데
    assert not lib.exists()    # 카드(폴더)가 없다
    assert not indexer.library_online()
    assert not lib.exists()    # 판정하느라 빈 폴더를 만들면 안 된다


def test_empty_mountpoint_is_offline(lib):
    # proot 바인드가 빠지면 빈 폴더만 남는다
    _add_media_row()
    lib.mkdir(parents=True)
    assert not indexer.library_online()


def test_pipeline_keeps_db_when_card_missing(lib):
    _add_media_row()
    indexer._run_pipeline(False)
    with db.conn() as c:
        assert c.execute("SELECT COUNT(*) FROM media").fetchone()[0] == 1
    assert "SD 카드" in indexer._state["error"]
    indexer._state["error"] = None


# ---------- 업로드 거절 ----------

def test_upload_refused_when_card_missing(lib, client):
    _add_media_row()
    r = client.post("/api/upload", data={"device": "phone"},
                    files={"files": ("a.jpg", b"\xff\xd8x", "image/jpeg")})
    assert r.status_code == 503
    assert not lib.exists()


def test_webdav_refused_when_card_missing(lib, client):
    _add_media_row()
    r = client.put("/webdav/2025/a.jpg", content=b"\xff\xd8x")
    assert r.status_code == 503
    assert not lib.exists()


# ---------- PC에서 열 수 있는 이름 ----------

@pytest.mark.parametrize("raw, want", [
    ("IMG_0001.jpg", "IMG_0001.jpg"),
    ("사진 2025.jpg", "사진 2025.jpg"),
    ('a:b*c?"d<e>f|g.jpg', "a_b_c__d_e_f_g.jpg"),
    ("tab\there.jpg", "tab_here.jpg"),
    ("../../evil.jpg", "evil.jpg"),
    ("dir\\win.jpg", "win.jpg"),
    ("name.jpg. ", "name.jpg"),
    ("...", "file"),
])
def test_portable_name(raw, want):
    assert upload.portable_name(raw) == want


def test_upload_saves_portable_name(lib, client):
    r = client.post("/api/upload", data={"device": "phone"},
                    files={"files": ("12:30:01?.jpg", b"\xff\xd8abc", "image/jpeg")})
    assert r.json()["results"][0]["status"] == "saved"
    assert (lib / "MobileBackup" / "phone" / "12_30_01_.jpg").is_file()


def test_webdav_put_and_head_use_same_portable_name(lib, client):
    assert client.put("/webdav/2025/a%3Ab.jpg", content=b"\xff\xd8abc").status_code == 201
    assert (lib / "MobileBackup" / "2025" / "a_b.jpg").is_file()
    # 자동백업 앱은 같은 이름으로 존재 여부를 다시 묻는다
    assert client.head("/webdav/2025/a%3Ab.jpg").status_code == 200
