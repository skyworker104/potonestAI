"""WebDAV(자동백업 앱 수신) 경로 봉쇄 테스트.

WebDAV는 PUT·MKCOL·DELETE를 그대로 열어주므로, 경로 검사가 유일한 방어선이다.
클라이언트가 보낸 어떤 경로도 업로드 폴더(MobileBackup) 밖을 가리켜선 안 된다.
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import db, indexer, upload

# 업로드 폴더 밖을 노리는 경로들. '../MobileBackupEvil'류가 핵심 —
# 문자열 접두사로 검사하면 이름이 겹치는 형제 폴더가 그대로 통과한다.
ESCAPES = [
    "../MobileBackupEvil/x.jpg",
    "../MobileBackup2",
    "%2e%2e/MobileBackupEvil/x.jpg",
    "../../etc/x.jpg",
    "a/../../MobileBackupEvil/x.jpg",
]

# HTTP로 보낼 때는 인코딩된 형태만 쓴다. 날것의 '..'는 클라이언트가 보내기 전에
# 정규화해 없애버려서 핸들러까지 오지도 않는다 — 서버를 시험하지 못한다.
HTTP_ESCAPES = [
    "%2e%2e/MobileBackupEvil/x.jpg",
    "%2e%2e/MobileBackup2",
    "%2e%2e/%2e%2e/etc/x.jpg",
    "a/%2e%2e/%2e%2e/MobileBackupEvil/x.jpg",
]


@pytest.fixture
def photos(tmp_path, monkeypatch):
    root, data = tmp_path / "photos", tmp_path / "data"
    root.mkdir(parents=True)
    data.mkdir(parents=True)
    monkeypatch.setattr(indexer, "PHOTOS_DIR", root)
    monkeypatch.setattr(db, "DATA_DIR", data)
    monkeypatch.setattr(db, "DB_FILE", data / "photonest.db")
    db.init()
    monkeypatch.setattr(upload, "_trigger_index", lambda: None)
    upload._recent_hashes.clear()
    yield root
    upload._recent_hashes.clear()


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(upload.router)
    return TestClient(app)


# ---------- _dav_path 단위 ----------

def test_allows_paths_inside_upload_root(photos):
    root = upload._upload_root().resolve()
    assert upload._dav_path("2025/a.jpg") == root / "2025" / "a.jpg"


def test_allows_root_itself(photos):
    assert upload._dav_path("") == upload._upload_root().resolve()


def test_decodes_percent_encoding(photos):
    """앱이 한글 폴더명을 인코딩해 보내도 정상 경로로 풀린다."""
    root = upload._upload_root().resolve()
    assert upload._dav_path("%EC%97%AC%ED%96%89/a.jpg") == root / "여행" / "a.jpg"


@pytest.mark.parametrize("rest", ESCAPES)
def test_rejects_paths_outside_upload_root(photos, rest):
    assert upload._dav_path(rest) is None


def test_rejects_symlink_pointing_outside(photos, tmp_path):
    """폴더 안에 바깥을 가리키는 심볼릭 링크가 있어도 따라 나가지 않는다."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (upload._upload_root() / "link").symlink_to(outside)

    assert upload._dav_path("link/x.jpg") is None


# ---------- HTTP 동작 ----------

@pytest.mark.parametrize("rest", HTTP_ESCAPES)
def test_put_outside_is_forbidden(photos, client, rest):
    r = client.put(f"/webdav/{rest}", content=b"\xff\xd8\xff-fake-jpeg")

    assert r.status_code == 403
    strays = [p for p in photos.rglob("*.jpg")
              if not p.is_relative_to(photos / upload.UPLOAD_DIR_NAME)]
    assert strays == [], f"{rest!r} → 업로드 폴더 밖에 {strays}"


@pytest.mark.parametrize("rest", HTTP_ESCAPES)
def test_mkcol_outside_is_forbidden(photos, client, rest):
    """MKCOL로 바깥 폴더를 만들어 두고 그 안에 넣는 우회를 막는다."""
    r = client.request("MKCOL", f"/webdav/{rest}")

    assert r.status_code == 403
    siblings = [p.name for p in photos.iterdir()]
    assert siblings == [upload.UPLOAD_DIR_NAME]


def test_delete_outside_is_forbidden(photos, client):
    victim = photos / "MobileBackupEvil" / "keep.jpg"
    victim.parent.mkdir(parents=True)
    victim.write_bytes("소중한파일".encode())

    r = client.request("DELETE", "/webdav/%2e%2e/MobileBackupEvil/keep.jpg")

    assert r.status_code == 403
    assert victim.is_file()


def test_put_inside_still_works(photos, client):
    """회귀: 정상 경로 업로드는 그대로 동작한다."""
    r = client.put("/webdav/2025/a.jpg", content=b"\xff\xd8\xff-fake-jpeg")

    assert r.status_code == 201
    saved = photos / upload.UPLOAD_DIR_NAME / "2025" / "a.jpg"
    assert saved.read_bytes() == b"\xff\xd8\xff-fake-jpeg"


def test_mkcol_inside_still_works(photos, client):
    r = client.request("MKCOL", "/webdav/새폴더")

    assert r.status_code == 201
    assert (photos / upload.UPLOAD_DIR_NAME / "새폴더").is_dir()


def test_get_outside_is_forbidden(photos, client):
    secret = photos / "MobileBackupEvil" / "s.jpg"
    secret.parent.mkdir(parents=True)
    secret.write_bytes("비밀".encode())

    r = client.get("/webdav/%2e%2e/MobileBackupEvil/s.jpg")

    assert r.status_code == 403
    assert "비밀".encode() not in r.content
