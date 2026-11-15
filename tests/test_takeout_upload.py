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


# ---------- 처리 내역 · 보관 zip 정리 ----------

def _process(root, name, files, keep=True):
    settings.save({"takeout_keep_zip": keep})
    (root / name).write_bytes(_make_zip(files))
    return takeout.process_pending()


def test_history_records_upload_and_stats(env):
    root, client = env
    blob = _make_zip({
        "Takeout/Google 포토/제주 여행/a.jpg": b"a",
        "Takeout/Google 포토/2016년의 사진/b.jpg": b"b",
        "Takeout/Google 포토/제주 여행/a.jpg.supplemental-metadata.json": b"{}",
    })
    uid = _upload(client, "takeout-x.zip", blob, chunk=100)
    client.post(f"/api/takeout/upload/{uid}/complete")
    [imp] = takeout.history()
    assert (imp["source"], imp["status"]) == ("upload", "queued")

    settings.save({"takeout_keep_zip": True})
    assert takeout.process_pending() == 2
    # 색인된 것처럼 media 행을 만든다 — a.jpg는 OCR 글자·얼굴까지
    with db.conn() as c:
        c.execute("INSERT INTO media (id, path, type, lat, taken_at, ocr_scanned,"
                  " ocr_text, faces_scanned) VALUES ('m1', ?, 'image', 33.5,"
                  " '2020-01-01', 1, '감귤', 1)", ("Takeout/Google 포토/제주 여행/a.jpg",))
        c.execute("INSERT INTO faces (media_id, person_id) VALUES ('m1', 7)")
    [imp] = client.get("/api/takeout/history").json()["imports"]
    assert (imp["source"], imp["status"], imp["added"]) == ("upload", "ok", 2)
    assert imp["zip_state"] == "kept" and imp["kept_size"] == len(blob)
    s = imp["stats"]
    assert (s["files"], s["photos"], s["missing"]) == (2, 1, 1)
    assert (s["gps"], s["ocr_done"], s["ocr_text"], s["faces"], s["persons"]) == (1, 1, 1, 1, 1)
    assert (s["albums"], s["albums_created"]) == (1, 0)  # 연도 폴더는 앨범 아님


def test_folder_drop_recorded_as_folder(env):
    root, _ = env
    _process(root, "takeout-f.zip", {"Takeout/Google 포토/a.jpg": b"1"}, keep=False)
    [imp] = takeout.history()
    assert (imp["source"], imp["zip_state"]) == ("folder", "deleted")


def test_delete_done_zips(env):
    root, client = env
    _process(root, "takeout-1.zip", {"Takeout/a.jpg": b"1"})
    _process(root, "takeout-2.zip", {"Takeout/b.jpg": b"2"})
    zips = client.get("/api/takeout/done").json()["zips"]
    assert [z["name"] for z in zips] == ["takeout-1.zip", "takeout-2.zip"]

    r = client.post("/api/takeout/done/delete", json={"names": ["takeout-1.zip"]}).json()
    assert r["deleted"] == 1 and r["freed"] > 0
    states = {i["zip_name"]: i["zip_state"] for i in takeout.history()}
    assert states == {"takeout-1.zip": "deleted", "takeout-2.zip": "kept"}

    r = client.post("/api/takeout/done/delete", json={"names": None}).json()
    assert r["deleted"] == 1
    assert client.get("/api/takeout/done").json()["zips"] == []
    assert (root / "Takeout" / "a.jpg").exists()  # 사진은 그대로


def test_delete_done_rejects_path_escape(env):
    root, client = env
    victim = root / "keep.zip"
    victim.write_bytes(b"x")
    (root / takeout.DONE_DIRNAME).mkdir()
    for bad in ["../keep.zip", "/etc/passwd", "..", "sub/../../keep.zip"]:
        r = client.post("/api/takeout/done/delete", json={"names": [bad]}).json()
        assert r["deleted"] == 0
    assert victim.exists()


def test_legacy_state_migrated_with_files_from_kept_zip(env):
    root, _ = env
    # 내역 기능 이전 방식으로 처리된 상태를 흉내 낸다
    done = root / takeout.DONE_DIRNAME
    done.mkdir()
    (done / "takeout-old.zip").write_bytes(_make_zip({"Takeout/old.jpg": b"o"}))
    (root / "Takeout").mkdir()
    (root / "Takeout" / "old.jpg").write_bytes(b"o")
    takeout._save_state({"takeout-old.zip": {"sig": "123-1", "added": 1, "ts": 1}})
    [imp] = takeout.history()
    assert (imp["zip_name"], imp["zip_state"], imp["stats"]["files"]) == \
        ("takeout-old.zip", "kept", 1)
    assert len(takeout.history()) == 1  # 두 번 옮기지 않는다
