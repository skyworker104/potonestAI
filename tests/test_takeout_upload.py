"""Takeout zip 업로드(조각·이어받기)와 처리 후 zip 보관/삭제 옵션.

깨지면 안 되는 것:
1) 조각으로 나눠 받은 파일이 원본과 1바이트도 다르지 않아야 한다.
2) 끊긴 뒤 같은 파일로 다시 시작하면 받은 지점(offset)부터 이어진다.
3) offset이 어긋난 조각은 붙지 않는다 (409 + 실제 offset).
4) takeout_keep_zip=False면 압축 해제 성공 후 zip이 지워지고, True면 _zips_done에 남는다.
"""
import io
import zipfile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import db, indexer, settings, takeout


def _make_zip(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


@pytest.fixture
def env(tmp_path, monkeypatch):
    root, data = tmp_path / "photos", tmp_path / "data"
    root.mkdir()
    data.mkdir()
    monkeypatch.setattr(indexer, "PHOTOS_DIR", root)
    monkeypatch.setattr(db, "DATA_DIR", data)
    monkeypatch.setattr(db, "DB_FILE", data / "photonest.db")
    monkeypatch.setattr(settings, "_FILE", data / "app" / "settings.json")
    monkeypatch.setattr(takeout, "STATE_FILE", data / "takeout_state.json")
    monkeypatch.setattr(indexer, "build_index", lambda *a, **k: None)
    db.init()
    app = FastAPI()
    app.include_router(takeout.router)
    return root, TestClient(app)


def _upload(client, name, blob, chunk, last_modified=1):
    r = client.post("/api/takeout/upload/init",
                    json={"name": name, "size": len(blob), "last_modified": last_modified})
    assert r.status_code == 200, r.text
    uid, off = r.json()["upload_id"], r.json()["offset"]
    while off < len(blob):
        r = client.put(f"/api/takeout/upload/{uid}?offset={off}",
                       content=blob[off:off + chunk])
        assert r.status_code == 200, r.text
        off = r.json()["offset"]
    return uid


def test_chunked_upload_is_byte_identical(env):
    root, client = env
    blob = _make_zip({"Takeout/Google 포토/a.jpg": b"x" * 5000})
    uid = _upload(client, "takeout-20261115.zip", blob, chunk=777)
    r = client.post(f"/api/takeout/upload/{uid}/complete")
    assert r.status_code == 200, r.text
    dest = root / r.json()["name"]
    assert dest.read_bytes() == blob
    assert not list((root / takeout.INCOMING_DIRNAME).glob("*.part"))
    assert dest in takeout.find_zips()


def test_resume_from_received_offset(env):
    root, client = env
    blob = _make_zip({"Takeout/Google 포토/a.jpg": b"y" * 8000})
    r = client.post("/api/takeout/upload/init",
                    json={"name": "takeout-1.zip", "size": len(blob), "last_modified": 7})
    uid = r.json()["upload_id"]
    client.put(f"/api/takeout/upload/{uid}?offset=0", content=blob[:1000])
    # 연결이 끊긴 뒤 같은 파일을 다시 골랐다
    r = client.post("/api/takeout/upload/init",
                    json={"name": "takeout-1.zip", "size": len(blob), "last_modified": 7})
    assert r.json() == {"upload_id": uid, "offset": 1000, "size": len(blob)}
    client.put(f"/api/takeout/upload/{uid}?offset=1000", content=blob[1000:])
    r = client.post(f"/api/takeout/upload/{uid}/complete")
    assert (root / r.json()["name"]).read_bytes() == blob


def test_wrong_offset_rejected(env):
    _, client = env
    blob = _make_zip({"a.jpg": b"z" * 3000})
    r = client.post("/api/takeout/upload/init", json={"name": "t.zip", "size": len(blob)})
    uid = r.json()["upload_id"]
    client.put(f"/api/takeout/upload/{uid}?offset=0", content=blob[:500])
    r = client.put(f"/api/takeout/upload/{uid}?offset=0", content=blob[:500])
    assert r.status_code == 409
    assert r.json()["detail"]["offset"] == 500


def test_oversize_chunk_does_not_corrupt(env):
    _, client = env
    blob = _make_zip({"a.jpg": b"z" * 3000})
    r = client.post("/api/takeout/upload/init", json={"name": "t.zip", "size": len(blob)})
    uid = r.json()["upload_id"]
    r = client.put(f"/api/takeout/upload/{uid}?offset=0", content=blob + b"extra")
    assert r.status_code == 413
    r = client.post("/api/takeout/upload/init", json={"name": "t.zip", "size": len(blob)})
    assert r.json()["offset"] == 0


def test_non_zip_rejected(env):
    root, client = env
    r = client.post("/api/takeout/upload/init", json={"name": "a.rar", "size": 10})
    assert r.status_code == 400
    uid = _upload(client, "fake.zip", b"not a zip at all", chunk=100)
    r = client.post(f"/api/takeout/upload/{uid}/complete")
    assert r.status_code == 400
    assert not list(root.glob("*.zip"))


def test_name_gets_takeout_prefix(env):
    root, client = env
    uid = _upload(client, "photos-export.zip", _make_zip({"a.jpg": b"1"}), chunk=50)
    name = client.post(f"/api/takeout/upload/{uid}/complete").json()["name"]
    assert name == "takeout-photos-export.zip"


def test_bad_upload_id_rejected(env):
    _, client = env
    r = client.post("/api/takeout/upload/..%2F..%2Fetc/complete")
    assert r.status_code in (400, 404)


@pytest.mark.parametrize("keep", [True, False])
def test_keep_zip_option(env, keep):
    root, _ = env
    settings.save({"takeout_keep_zip": keep})
    z = root / "takeout-a.zip"
    z.write_bytes(_make_zip({"Takeout/Google 포토/a.jpg": b"img"}))
    added = takeout.process_pending()
    assert added == 1
    assert (root / "Takeout" / "Google 포토" / "a.jpg").exists()
    assert not z.exists()
    assert (root / takeout.DONE_DIRNAME / "takeout-a.zip").exists() == keep
    assert takeout.find_zips() == []


def test_setting_default_and_public():
    cfg = dict(settings._DEFAULTS)
    assert cfg["takeout_keep_zip"] is True
