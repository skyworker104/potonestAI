"""앨범 상세 — album_items와 media 둘 다 added_at 열이 있어, 조인 질의가
'ambiguous column name'으로 실패해 앨범을 열면 빈(검은) 화면만 나왔다."""
from fastapi.testclient import TestClient

from backend import db, indexer, main


def test_album_detail_lists_items(tmp_path, monkeypatch):
    monkeypatch.setattr(indexer, "PHOTOS_DIR", tmp_path / "photos")
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_FILE", tmp_path / "photonest.db")
    db.init()
    with db.conn() as c:
        c.execute("INSERT INTO media (id, path, type, taken_at, added_at) "
                  "VALUES ('m1', 'a.jpg', 'image', '2026-10-01T10:00:00', '2026-10-02T09:00:00')")
    album_id = db.create_album("북한산")
    db.add_to_album(album_id, ["m1"])

    monkeypatch.setattr(main, "_warmup_search", lambda: None)
    monkeypatch.setattr(indexer, "build_index", lambda force=False: None)
    with TestClient(main.app) as client:
        r = client.get(f"/api/albums/{album_id}")
    assert r.status_code == 200
    items = r.json()["items"]
    assert [it["id"] for it in items] == ["m1"]
    assert items[0]["added_at"] == "2026-10-02T09:00:00"  # 미디어의 값 (앨범에 넣은 시각 아님)
