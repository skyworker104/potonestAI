"""PC 폴더 업로드 — 하위 경로 정리(_safe_subdir)와 원본 바이트·GPS 보존.

폴더 업로드는 두 가지가 깨지면 안 된다.
1) 저장된 파일이 원본과 1바이트도 달라선 안 된다 — EXIF·GPS 무손실의 근거.
2) subdir은 클라이언트가 보내는 값이므로, 어떤 문자열이 와도 업로드 폴더
   밖에 파일이 생겨선 안 된다.
"""
import io
import sqlite3
from pathlib import Path

import piexif
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from backend import db, indexer, upload

# 특수문자를 걷어내면 '..'만 남는 입력들 — 정리 로직이 놓치면 상위로 탈출한다
TRAVERSAL_INPUTS = [
    "..", "../..", "../../etc", "/etc", "~",
    "<..>", "<..>/<..>", "a/<..>/<..>/<..>",
    "|..|", "?..?", "*..*", '"..*"', "<.>/<..>",
]


@pytest.fixture
def photos(tmp_path, monkeypatch):
    """임시 라이브러리 + 임시 DB. 색인 스레드는 띄우지 않는다."""
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


def gps_jpeg():
    """GPS EXIF가 든 작은 JPEG 바이트."""
    exif = piexif.dump({
        "0th": {}, "Exif": {}, "1st": {}, "thumbnail": None,
        "GPS": {
            piexif.GPSIFD.GPSLatitudeRef: b"N",
            piexif.GPSIFD.GPSLatitude: ((37, 1), (30, 1), (0, 1)),
            piexif.GPSIFD.GPSLongitudeRef: b"E",
            piexif.GPSIFD.GPSLongitude: ((127, 1), (0, 1), (0, 1)),
        },
    })
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (120, 30, 60)).save(buf, "JPEG", exif=exif)
    return buf.getvalue()


def post(client, name, data, subdir=None, device="pc"):
    form = {"device": device}
    if subdir is not None:
        form["subdir"] = subdir
    return client.post("/api/upload",
                       files=[("files", (name, data, "image/jpeg"))],
                       data=form)


# ---------- _safe_subdir 단위 ----------

def test_keeps_normal_nested_path():
    assert upload._safe_subdir("2025/여름휴가") == Path("2025/여름휴가")


def test_strips_hidden_components():
    assert upload._safe_subdir(".git/objects") == Path("objects")


def test_converts_windows_separators():
    assert upload._safe_subdir(r"2025\여름") == Path("2025/여름")


def test_strips_special_characters():
    assert upload._safe_subdir('사진<>:"|?*집') == Path("사진집")


def test_returns_none_when_everything_filtered():
    assert upload._safe_subdir("../..") is None
    assert upload._safe_subdir("") is None


@pytest.mark.parametrize("evil", TRAVERSAL_INPUTS)
def test_never_escapes_upload_dir(evil):
    """어떤 입력이 와도 결과 경로는 기준 폴더 안에 머물러야 한다."""
    base = Path("/photos/MobileBackup/pc")
    sd = upload._safe_subdir(evil)
    if sd is None:
        return
    assert not sd.is_absolute(), f"{evil!r} → 절대경로 {sd}"
    final = Path(str((base / sd))).resolve()
    assert final.is_relative_to(base), f"{evil!r} → {final} (탈출)"


# ---------- 업로드 통합 ----------

def test_upload_preserves_original_bytes_and_gps(photos, client):
    """저장된 파일이 원본과 완전히 동일 — 그래야 GPS가 살아남는다."""
    data = gps_jpeg()

    r = post(client, "trip.jpg", data, subdir="2025/여름")

    assert r.status_code == 200 and r.json()["saved"] == 1
    saved = photos / upload.UPLOAD_DIR_NAME / "pc" / "2025" / "여름" / "trip.jpg"
    assert saved.read_bytes() == data
    gps = piexif.load(str(saved))["GPS"]
    assert gps[piexif.GPSIFD.GPSLatitude] == ((37, 1), (30, 1), (0, 1))
    assert gps[piexif.GPSIFD.GPSLongitudeRef] == b"E"


def test_upload_without_subdir_lands_in_device_dir(photos, client):
    assert post(client, "a.jpg", gps_jpeg()).json()["saved"] == 1
    assert (photos / upload.UPLOAD_DIR_NAME / "pc" / "a.jpg").is_file()


@pytest.mark.parametrize("evil", TRAVERSAL_INPUTS)
def test_upload_cannot_write_outside_library(photos, client, evil, tmp_path):
    """악성 subdir로 업로드해도 파일은 업로드 폴더 안에만 생긴다."""
    r = post(client, "evil.jpg", gps_jpeg(), subdir=evil)

    assert r.status_code == 200
    upload_root = (photos / upload.UPLOAD_DIR_NAME).resolve()
    found = list(tmp_path.rglob("evil*.jpg"))
    assert found, "파일이 저장되지 않았다"
    for p in found:
        assert p.resolve().is_relative_to(upload_root), f"{evil!r} → {p} (탈출)"


def test_duplicate_upload_skipped(photos, client):
    """같은 사진을 다시 올리면 중복으로 걸러진다(폴더가 달라도 내용 기준)."""
    data = gps_jpeg()
    assert post(client, "a.jpg", data, subdir="1").json()["saved"] == 1
    assert post(client, "a.jpg", data, subdir="2").json()["duplicate"] == 1


def test_unsupported_extension_rejected(photos, client):
    r = client.post("/api/upload",
                    files=[("files", ("x.exe", b"MZ", "application/octet-stream"))],
                    data={"device": "pc"})
    assert r.json()["results"][0]["status"] == "unsupported"
    assert not list((photos / upload.UPLOAD_DIR_NAME).rglob("*.exe"))
