"""Immich 공식 앱 호환 계층 계약 테스트.

여기서 검증하는 것은 "우리 코드가 도는지"가 아니라 "Immich 앱이 전제하는 계약을
지키는지"다. 앱은 소스를 고칠 수 없으므로 아래 항목이 하나라도 어긋나면 앱이
붙지 않거나 사진을 중복 업로드한다.

  - 서버 판정: /api/server/ping 이 무인증으로 {"res":"pong"}
  - 주소 탐색: /.well-known/immich 이 실제 API 접두사를 알려준다
  - id 형식: 모든 asset id가 Immich의 UUID v4 정규식을 통과한다
  - 체크섬: base64(SHA-1(전체 파일)) — 앱이 이 값으로 중복을 판정한다
  - 동기화: NDJSON 줄이 종류별로 뭉쳐 나오고, ack를 돌려주면 다음엔 안 나온다
"""
import base64
import hashlib
import json
import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from backend import caption, db, faces, indexer, ocr, upload
from backend import immich
from backend.immich import auth, media, state

# Immich OpenAPI가 asset id에 강제하는 형식 (버전 4 + RFC4122 변형)
UUID_V4 = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-4[0-9a-fA-F]{3}"
    r"-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"
)

EMAIL = "tester@home.lan"
PASSWORD = "photonest-test"


def _make_jpeg(path, color=(200, 120, 40), size=(64, 48)):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path, "JPEG", quality=90)
    return path


def _sha1_b64(path):
    return base64.b64encode(hashlib.sha1(path.read_bytes()).digest()).decode()


@pytest.fixture
def env(tmp_path, monkeypatch):
    """색인까지 끝난 사진 2장이 있는 독립 환경."""
    photos, data = tmp_path / "photos", tmp_path / "data"
    photos.mkdir(parents=True)
    data.mkdir(parents=True)

    monkeypatch.setattr(db, "DATA_DIR", data)
    monkeypatch.setattr(db, "DB_FILE", data / "photonest.db")
    monkeypatch.setattr(indexer, "PHOTOS_DIR", photos)
    monkeypatch.setattr(indexer, "DATA_DIR", data)
    monkeypatch.setattr(indexer, "THUMBS_DIR", data / "thumbs")
    monkeypatch.setattr(indexer, "TRASH_DIR", data / "trash")
    monkeypatch.setattr(media, "_PREVIEW_DIR", data / "preview" / "immich")
    monkeypatch.setattr(auth, "_FILE", data / "app" / "immich_auth.json")

    # 색인의 AI 단계는 이 테스트 대상이 아니다 (모델 다운로드도 피한다)
    monkeypatch.setattr(indexer, "ai_available", lambda: False)
    monkeypatch.setattr(faces, "available", lambda: False)
    monkeypatch.setattr(ocr, "available", lambda: False)
    monkeypatch.setattr(caption, "available", lambda: False)
    monkeypatch.setattr(db, "media_missing_place", lambda: [])
    monkeypatch.setattr(upload, "_trigger_index", lambda: None)
    # 체크섬 계산은 테스트가 직접 동기로 돌린다. 백그라운드 스레드를 띄우면
    # 테스트가 끝나 monkeypatch가 풀린 뒤에도 살아남아 실제 DB/사진첩을 건드린다.
    monkeypatch.setattr(state, "start_backfill", lambda: None)
    upload._recent_hashes.clear()

    _make_jpeg(photos / "a.jpg", (10, 20, 30))
    _make_jpeg(photos / "trip" / "b.jpg", (90, 180, 250))

    db.init()
    state.init()
    indexer.build_index()
    # 지명 기반 엔드포인트(/search/explore, /search/cities)를 실제로 태우기 위해
    # 역지오코딩 결과를 한 장에 직접 넣는다 (GeoNames 다운로드 없이)
    with db.conn() as c:
        c.execute("UPDATE media SET place_name='협재리 한림읍', lat=33.39, lon=126.24 "
                  "WHERE path='a.jpg'")
    # 테스트는 동기적으로 검사한다 — 백그라운드 스레드 대신 직접 채운다
    state._backfill_loop()
    yield {"photos": photos, "data": data}
    upload._recent_hashes.clear()


@pytest.fixture
def client(env):
    app = FastAPI()
    app.include_router(immich.router)
    app.include_router(immich.discovery)
    return TestClient(app)


@pytest.fixture
def token(client):
    auth.set_account(EMAIL, PASSWORD)
    r = client.post("/immich/api/auth/login",
                    json={"email": EMAIL, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return r.json()["accessToken"]


def _hdr(token):
    return {"Authorization": "Bearer %s" % token}


# ---------- 서버 판정 / 주소 탐색 ----------

def test_ping_is_unauthenticated_and_says_pong(client):
    r = client.get("/immich/api/server/ping")
    assert r.status_code == 200
    assert r.json() == {"res": "pong"}


def test_well_known_points_at_the_api_prefix(client):
    r = client.get("/.well-known/immich")
    assert r.status_code == 200
    # 앱은 이 값을 서버 주소에 이어 붙이고 뒤에 /api가 이미 있는지 확인한다
    assert r.json()["api"]["endpoint"] == "/immich/api"
    assert r.json()["api"]["endpoint"].endswith("/api")


def test_version_is_parseable_semver(client):
    body = client.get("/immich/api/server/version").json()
    assert isinstance(body["major"], int) and isinstance(body["minor"], int)
    assert body["prerelease"] is None
    # 2.6 미만으로 보고해야 앱이 전부 V1 동기화 타입을 요청한다
    assert (body["major"], body["minor"]) < (2, 6)


# ---------- 인증 ----------

def test_login_refused_until_account_configured(client):
    r = client.post("/immich/api/auth/login",
                    json={"email": EMAIL, "password": PASSWORD})
    assert r.status_code == 401
    assert "message" in r.json()  # 앱이 이 키를 읽어 사용자에게 보여준다


def test_login_rejects_wrong_password(client):
    auth.set_account(EMAIL, PASSWORD)
    r = client.post("/immich/api/auth/login",
                    json={"email": EMAIL, "password": "wrong-password"})
    assert r.status_code == 401


def test_login_returns_every_field_the_app_requires(client, token):
    r = client.post("/immich/api/auth/login",
                    json={"email": EMAIL, "password": PASSWORD})
    body = r.json()
    for key in ("accessToken", "userId", "userEmail", "name", "isAdmin",
                "isOnboarded", "profileImagePath", "shouldChangePassword"):
        assert key in body, key
    assert UUID_V4.match(body["userId"])


def test_protected_endpoints_need_credentials(client, token):
    assert client.get("/immich/api/users/me").status_code == 401
    assert client.get("/immich/api/users/me", headers=_hdr(token)).status_code == 200


def test_api_key_works_for_clients_without_login(client, token):
    key = auth.account()["api_key"]
    r = client.get("/immich/api/albums", headers={"x-api-key": key})
    assert r.status_code == 200


def test_logout_invalidates_the_token(client, token):
    assert client.post("/immich/api/auth/logout",
                       headers=_hdr(token)).status_code == 200
    assert client.get("/immich/api/users/me", headers=_hdr(token)).status_code == 401


# ---------- id 형식과 체크섬 ----------

def test_all_asset_ids_are_uuid_v4(client, token):
    r = client.post("/immich/api/search/metadata", json={},
                    headers=_hdr(token))
    items = r.json()["assets"]["items"]
    assert len(items) == 2
    for item in items:
        assert UUID_V4.match(item["id"]), item["id"]


def test_checksum_is_base64_sha1_of_the_whole_file(client, token, env):
    r = client.post("/immich/api/search/metadata",
                    json={"originalFileName": "a.jpg"}, headers=_hdr(token))
    item = r.json()["assets"]["items"][0]
    assert item["checksum"] == _sha1_b64(env["photos"] / "a.jpg")


def test_uuid_is_stable_across_reindex(client, token):
    before = {i["id"] for i in client.post(
        "/immich/api/search/metadata", json={}, headers=_hdr(token)
    ).json()["assets"]["items"]}
    indexer.build_index(force=True)
    state._backfill_loop()
    after = {i["id"] for i in client.post(
        "/immich/api/search/metadata", json={}, headers=_hdr(token)
    ).json()["assets"]["items"]}
    assert before == after


# ---------- 업로드 ----------

def test_upload_stores_original_and_returns_created(client, token, tmp_path, env):
    src = _make_jpeg(tmp_path / "phone.jpg", (5, 5, 200))
    r = client.post(
        "/immich/api/assets", headers=_hdr(token),
        files={"assetData": ("phone.jpg", src.read_bytes(), "image/jpeg")},
        data={"deviceAssetId": "local-1", "deviceId": "test-device",
              "fileCreatedAt": "2024-03-01T10:00:00.000Z",
              "fileModifiedAt": "2024-03-01T10:00:00.000Z",
              "isFavorite": "false", "duration": "0"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "created"
    assert UUID_V4.match(body["id"])

    stored = env["photos"] / "MobileBackup" / "Immich" / "phone.jpg"
    assert stored.read_bytes() == src.read_bytes()  # 원본 무손실
    # 색인 전에도 체크섬이 준비돼 있어야 앱이 곧바로 "백업됨"으로 본다
    assert state.checksum(state.media_id_for_path(
        "MobileBackup/Immich/phone.jpg")) == _sha1_b64(src)


def test_reuploading_the_same_bytes_reports_duplicate_with_same_id(
        client, token, tmp_path):
    src = _make_jpeg(tmp_path / "dup.jpg", (7, 7, 7))
    payload = {
        "files": {"assetData": ("dup.jpg", src.read_bytes(), "image/jpeg")},
        "data": {"fileCreatedAt": "2024-03-01T10:00:00.000Z",
                 "fileModifiedAt": "2024-03-01T10:00:00.000Z"},
    }
    first = client.post("/immich/api/assets", headers=_hdr(token), **payload)
    indexer.build_index()
    second = client.post("/immich/api/assets", headers=_hdr(token), **payload)
    assert first.json()["status"] == "created"
    assert second.json()["status"] == "duplicate"
    assert second.json()["id"] == first.json()["id"]


def test_upload_rejects_unsupported_extension(client, token):
    r = client.post("/immich/api/assets", headers=_hdr(token),
                    files={"assetData": ("notes.txt", b"hello", "text/plain")},
                    data={"fileCreatedAt": "2024-03-01T10:00:00.000Z",
                          "fileModifiedAt": "2024-03-01T10:00:00.000Z"})
    assert r.status_code == 400


def test_bulk_upload_check_rejects_known_checksums(client, token, env):
    known = _sha1_b64(env["photos"] / "a.jpg")
    r = client.post("/immich/api/assets/bulk-upload-check", headers=_hdr(token),
                    json={"assets": [{"id": "x", "checksum": known},
                                     {"id": "y", "checksum": "AAAA"}]})
    results = {item["id"]: item for item in r.json()["results"]}
    assert results["x"]["action"] == "reject"
    assert UUID_V4.match(results["x"]["assetId"])
    assert results["y"]["action"] == "accept"


# ---------- 동기화 스트림 ----------

ALL_TYPES = ["AuthUsersV1", "UsersV1", "AssetsV1", "AssetExifsV1",
             "AlbumsV1", "AlbumToAssetsV1", "PartnersV1", "MemoriesV1",
             "StacksV1", "PeopleV1", "AssetFacesV1", "UserMetadataV1"]


def _stream(client, token, types=None, reset=False):
    r = client.post("/immich/api/sync/stream", headers=_hdr(token),
                    json={"types": types or ALL_TYPES, "reset": reset})
    assert r.status_code == 200, r.text
    return [json.loads(line) for line in r.text.splitlines() if line.strip()]


def test_stream_emits_user_assets_and_exif(client, token):
    lines = _stream(client, token)
    kinds = [line["type"] for line in lines]
    assert "AuthUserV1" in kinds
    assert kinds.count("AssetV1") == 2
    assert kinds.count("AssetExifV1") == 2
    # 클라이언트는 같은 type이 연속으로 오는 구간을 한 배치로 처리한다
    assert kinds == sorted(kinds, key=lambda k: kinds.index(k))


def test_every_streamed_line_has_type_data_and_ack(client, token):
    for line in _stream(client, token):
        assert set(line) == {"type", "data", "ack"}
        assert isinstance(line["ack"], str) and line["ack"]


def test_sync_asset_payload_has_all_required_fields(client, token):
    # SyncAssetV1에서 null이 허용되지 않는 필드들
    required = ["id", "ownerId", "checksum", "originalFileName", "type",
                "visibility", "isFavorite", "isEdited"]
    assets = [line for line in _stream(client, token) if line["type"] == "AssetV1"]
    for line in assets:
        for key in required:
            assert line["data"][key] is not None, key
        assert UUID_V4.match(line["data"]["id"])
        assert line["data"]["type"] in ("IMAGE", "VIDEO")


def test_exif_payload_carries_all_25_keys(client, token):
    exifs = [line for line in _stream(client, token)
             if line["type"] == "AssetExifV1"]
    expected = {
        "assetId", "city", "country", "dateTimeOriginal", "description",
        "exifImageHeight", "exifImageWidth", "exposureTime", "fNumber",
        "fileSizeInByte", "focalLength", "fps", "iso", "latitude", "lensModel",
        "longitude", "make", "model", "modifyDate", "orientation",
        "profileDescription", "projectionType", "rating", "state", "timeZone",
    }
    missing = expected - set(exifs[0]["data"])
    assert not missing, missing


def _ack_everything(client, token, lines):
    acks = {}
    for line in lines:
        acks[line["type"]] = line["ack"]  # 종류별 마지막 ack만 보낸다 (앱과 동일)
    r = client.post("/immich/api/sync/ack", headers=_hdr(token),
                    json={"acks": list(acks.values())})
    assert r.status_code == 204


def test_acked_rows_are_not_sent_again(client, token):
    first = _stream(client, token)
    assert first
    _ack_everything(client, token, first)
    assert _stream(client, token) == []


def test_new_photo_appears_after_ack(client, token, env):
    _ack_everything(client, token, _stream(client, token))
    _make_jpeg(env["photos"] / "c.jpg", (1, 2, 3))
    indexer.build_index()
    state._backfill_loop()
    kinds = [line["type"] for line in _stream(client, token)]
    assert kinds.count("AssetV1") == 1
    assert kinds.count("AssetExifV1") == 1


def test_stream_hashes_pending_photos_itself(client, token, env):
    """색인이 끝난 직후 체크섬이 아직 없어도 그 동기화에서 사진이 나와야 한다.

    백그라운드 backfill만 의존하면 첫 동기화에 사진이 몇 장 빠진다 — 실제로
    라이브 서버에서 2장 중 1장만 내려가는 것을 확인해 고친 지점이다.
    """
    _ack_everything(client, token, _stream(client, token))
    _make_jpeg(env["photos"] / "late.jpg", (11, 22, 33))
    indexer.build_index()          # 색인만 — 체크섬은 일부러 계산하지 않는다
    assert state.checksum(state.media_id_for_path("late.jpg")) is None
    kinds = [line["type"] for line in _stream(client, token)]
    assert kinds.count("AssetV1") == 1
    assert kinds.count("AssetExifV1") == 1


def test_trashing_a_photo_sets_deleted_at_not_a_delete_event(client, token):
    _ack_everything(client, token, _stream(client, token))
    items = client.post("/immich/api/search/metadata", json={},
                        headers=_hdr(token)).json()["assets"]["items"]
    target = items[0]["id"]
    assert client.request("DELETE", "/immich/api/assets", headers=_hdr(token),
                          json={"ids": [target]}).status_code == 204
    lines = _stream(client, token)
    updated = [x for x in lines if x["type"] == "AssetV1"]
    assert [x["data"]["id"] for x in updated] == [target]
    assert updated[0]["data"]["deletedAt"] is not None


def test_permanent_delete_emits_asset_delete(client, token):
    _ack_everything(client, token, _stream(client, token))
    items = client.post("/immich/api/search/metadata", json={},
                        headers=_hdr(token)).json()["assets"]["items"]
    target = items[0]["id"]
    client.request("DELETE", "/immich/api/assets", headers=_hdr(token),
                   json={"ids": [target], "force": True})
    deletes = [x for x in _stream(client, token) if x["type"] == "AssetDeleteV1"]
    assert [x["data"]["assetId"] for x in deletes] == [target]


def test_reset_resends_everything(client, token):
    _ack_everything(client, token, _stream(client, token))
    assert _stream(client, token) == []
    assert _stream(client, token, reset=True)


def test_delete_ack_makes_the_app_resync_that_type(client, token):
    _ack_everything(client, token, _stream(client, token))
    # 앱은 첫 동기화 때 마이그레이션으로 이 호출을 실제로 보낸다
    r = client.request("DELETE", "/immich/api/sync/ack", headers=_hdr(token),
                       json={"types": ["AssetExifV1"]})
    assert r.status_code == 204
    kinds = [line["type"] for line in _stream(client, token)]
    assert kinds.count("AssetExifV1") == 2
    assert "AssetV1" not in kinds


def test_unsupported_sync_types_are_silently_empty(client, token):
    lines = _stream(client, token, types=["PartnersV1", "StacksV1", "MemoriesV1"])
    assert lines == []


# ---------- 앨범 ----------

def test_album_lifecycle_and_sync_links(client, token):
    assets = client.post("/immich/api/search/metadata", json={},
                         headers=_hdr(token)).json()["assets"]["items"]
    created = client.post("/immich/api/albums", headers=_hdr(token),
                          json={"albumName": "여행", "assetIds": [assets[0]["id"]]})
    assert created.status_code == 201, created.text
    album = created.json()
    assert UUID_V4.match(album["id"])
    assert album["assetCount"] == 1

    detail = client.get("/immich/api/albums/%s" % album["id"], headers=_hdr(token))
    assert [a["id"] for a in detail.json()["assets"]] == [assets[0]["id"]]

    added = client.put("/immich/api/albums/%s/assets" % album["id"],
                       headers=_hdr(token), json={"ids": [assets[1]["id"]]})
    assert [x["success"] for x in added.json()] == [True]
    again = client.put("/immich/api/albums/%s/assets" % album["id"],
                       headers=_hdr(token), json={"ids": [assets[1]["id"]]})
    assert again.json()[0]["error"] == "duplicate"

    lines = _stream(client, token, types=["AlbumsV1", "AlbumToAssetsV1"])
    kinds = [x["type"] for x in lines]
    assert kinds.count("AlbumV1") == 1
    assert kinds.count("AlbumToAssetV1") == 2

    renamed = client.patch("/immich/api/albums/%s" % album["id"],
                           headers=_hdr(token), json={"albumName": "제주"})
    assert renamed.json()["albumName"] == "제주"
    assert client.request("DELETE", "/immich/api/albums/%s" % album["id"],
                          headers=_hdr(token)).status_code == 204
    assert client.get("/immich/api/albums", headers=_hdr(token)).json() == []


def test_unknown_album_id_is_404(client, token):
    missing = "11111111-2222-4333-8444-555555555555"
    assert client.get("/immich/api/albums/%s" % missing,
                      headers=_hdr(token)).status_code == 404


# ---------- 타임라인 ----------

def test_timeline_buckets_and_bucket_are_self_consistent(client, token):
    buckets = client.get("/immich/api/timeline/buckets",
                         headers=_hdr(token)).json()
    assert buckets, "촬영시각이 없는 사진도 파일 수정시각으로 묶여야 한다"
    assert sum(b["count"] for b in buckets) == 2
    for bucket in buckets:
        assert re.match(r"^\d{4}-\d{2}-01$", bucket["timeBucket"])

    # 앱은 buckets가 준 값을 그대로 다시 보낸다
    body = client.get("/immich/api/timeline/bucket",
                      params={"timeBucket": buckets[0]["timeBucket"]},
                      headers=_hdr(token)).json()
    assert len(body["id"]) == buckets[0]["count"]
    # 열 단위 배열이라 길이가 모두 같아야 한다
    assert len({len(v) for v in body.values()}) == 1
    assert all(UUID_V4.match(x) for x in body["id"])
    assert all(r > 0 for r in body["ratio"])


def test_timeline_bucket_accepts_the_iso_form_too(client, token):
    buckets = client.get("/immich/api/timeline/buckets",
                         headers=_hdr(token)).json()
    iso = buckets[0]["timeBucket"] + "T00:00:00.000Z"
    body = client.get("/immich/api/timeline/bucket",
                      params={"timeBucket": iso}, headers=_hdr(token)).json()
    assert len(body["id"]) == buckets[0]["count"]


# ---------- 미디어 ----------

def test_thumbnail_and_preview_and_original(client, token, env):
    asset = client.post("/immich/api/search/metadata", json={},
                        headers=_hdr(token)).json()["assets"]["items"][0]
    thumb = client.get("/immich/api/assets/%s/thumbnail" % asset["id"],
                       headers=_hdr(token))
    assert thumb.status_code == 200
    assert thumb.headers["content-type"] == "image/jpeg"

    preview = client.get("/immich/api/assets/%s/thumbnail" % asset["id"],
                         params={"size": "preview"}, headers=_hdr(token))
    assert preview.status_code == 200

    original = client.get("/immich/api/assets/%s/original" % asset["id"],
                          headers=_hdr(token))
    assert original.status_code == 200
    source = env["photos"] / asset["originalPath"]
    assert original.content == source.read_bytes()


def test_original_supports_range_requests(client, token):
    asset = client.post("/immich/api/search/metadata", json={},
                        headers=_hdr(token)).json()["assets"]["items"][0]
    r = client.get("/immich/api/assets/%s/original" % asset["id"],
                   headers=dict(_hdr(token), Range="bytes=0-9"))
    # 동영상 탐색(seek)이 되려면 서버가 부분 응답을 해야 한다
    assert r.status_code == 206
    assert len(r.content) == 10


def test_unknown_asset_id_is_404(client, token):
    missing = "11111111-2222-4333-8444-555555555555"
    assert client.get("/immich/api/assets/%s" % missing,
                      headers=_hdr(token)).status_code == 404


# ---------- 수정 ----------

def test_favorite_and_description_round_trip(client, token):
    asset = client.post("/immich/api/search/metadata", json={},
                        headers=_hdr(token)).json()["assets"]["items"][0]
    r = client.put("/immich/api/assets/%s" % asset["id"], headers=_hdr(token),
                   json={"isFavorite": True, "description": "노을"})
    assert r.json()["isFavorite"] is True
    assert r.json()["exifInfo"]["description"] == "노을"
    favorites = client.post("/immich/api/search/metadata",
                            json={"isFavorite": True},
                            headers=_hdr(token)).json()["assets"]["items"]
    assert [a["id"] for a in favorites] == [asset["id"]]


def test_trash_restore_brings_the_photo_back(client, token):
    asset = client.post("/immich/api/search/metadata", json={},
                        headers=_hdr(token)).json()["assets"]["items"][0]
    client.request("DELETE", "/immich/api/assets", headers=_hdr(token),
                   json={"ids": [asset["id"]]})
    assert len(client.post("/immich/api/search/metadata", json={},
                           headers=_hdr(token)).json()["assets"]["items"]) == 1
    r = client.post("/immich/api/trash/restore/assets", headers=_hdr(token),
                    json={"ids": [asset["id"]]})
    assert r.json()["count"] == 1
    assert len(client.post("/immich/api/search/metadata", json={},
                           headers=_hdr(token)).json()["assets"]["items"]) == 2


# ---------- 빈 응답으로 두는 것들 ----------

def test_features_without_backing_data_return_empty_collections(client, token):
    for path in ("/immich/api/partners", "/immich/api/memories",
                 "/immich/api/stacks", "/immich/api/tags",
                 "/immich/api/shared-links", "/immich/api/sessions",
                 "/immich/api/duplicates", "/immich/api/notifications"):
        r = client.get(path, headers=_hdr(token))
        assert r.status_code == 200, path
        assert r.json() == [], path


def test_explore_and_cities_have_different_shapes(client, token):
    # 두 엔드포인트는 같은 데이터를 다른 스키마로 낸다 — 섞으면 앱이 파싱에 실패한다
    explore = client.get("/immich/api/search/explore", headers=_hdr(token)).json()
    assert explore[0]["fieldName"] == "exifInfo.city"
    item = explore[0]["items"][0]
    assert item["value"] == "협재리 한림읍"
    assert UUID_V4.match(item["data"]["id"])

    cities = client.get("/immich/api/search/cities", headers=_hdr(token)).json()
    assert UUID_V4.match(cities[0]["id"])       # 에셋 배열이어야 한다
    assert "value" not in cities[0]

    suggestions = client.get("/immich/api/search/suggestions",
                             params={"type": "city"}, headers=_hdr(token)).json()
    assert suggestions == ["협재리 한림읍"]


def test_map_markers_expose_coordinates(client, token):
    markers = client.get("/immich/api/map/markers", headers=_hdr(token)).json()
    assert len(markers) == 1
    assert UUID_V4.match(markers[0]["id"])
    assert markers[0]["lat"] == pytest.approx(33.39)
    assert markers[0]["city"] == "협재리 한림읍"


def test_duplicate_upload_is_caught_before_indexing(client, token, tmp_path):
    """색인이 끝나기 전에 같은 사진이 또 올라와도 중복으로 잡아야 한다.

    앱의 백업은 여러 장을 연달아 올리므로 이 구간이 실제로 발생한다.
    """
    src = _make_jpeg(tmp_path / "race.jpg", (3, 9, 27))
    payload = {
        "files": {"assetData": ("race.jpg", src.read_bytes(), "image/jpeg")},
        "data": {"fileCreatedAt": "2024-05-01T10:00:00.000Z",
                 "fileModifiedAt": "2024-05-01T10:00:00.000Z"},
    }
    first = client.post("/immich/api/assets", headers=_hdr(token), **payload)
    # 색인을 돌리지 않은 상태 — media 행이 아직 없다
    second = client.post("/immich/api/assets", headers=_hdr(token), **payload)
    assert first.json()["status"] == "created"
    assert second.json()["status"] == "duplicate"
    assert second.json()["id"] == first.json()["id"]


def test_trashed_photo_keeps_checksum_but_can_be_reuploaded(client, token, env):
    """휴지통 항목은 체크섬이 살아 있어야(동기화가 deletedAt으로 내보냄) 하지만,
    중복 판정에서는 빠져야 한다(사용자가 다시 올릴 수 있어야 한다)."""
    asset = client.post("/immich/api/search/metadata",
                        json={"originalFileName": "a.jpg"},
                        headers=_hdr(token)).json()["assets"]["items"][0]
    source = env["photos"] / "a.jpg"
    expected = _sha1_b64(source)
    payload = source.read_bytes()
    client.request("DELETE", "/immich/api/assets", headers=_hdr(token),
                   json={"ids": [asset["id"]]})

    media_id = state.resolve(asset["id"], "asset")
    assert state.checksum(media_id) == expected      # 동기화용으로는 유효
    assert state.media_id_by_sha1(expected) is None  # 중복 판정에서는 제외

    r = client.post("/immich/api/assets", headers=_hdr(token),
                    files={"assetData": ("a.jpg", payload, "image/jpeg")},
                    data={"fileCreatedAt": "2024-01-01T00:00:00.000Z",
                          "fileModifiedAt": "2024-01-01T00:00:00.000Z"})
    assert r.json()["status"] == "created"


def test_orphan_checksums_are_pruned(client, token, env):
    """파일도 없고 media 행도 없는 체크섬은 지워져야 한다.

    남아 있으면 없는 사진을 "이미 있음"으로 판정해 앱 업로드를 잘못 막는다.
    """
    state.record_checksum("ghost__x_jpg", "ghost/x.jpg", "10-20", "Z" * 27 + "=")
    assert state.media_id_by_sha1("Z" * 27 + "=") is None  # 파일이 없으니 무효
    assert state.prune_checksums() >= 1
    assert state.checksum("ghost__x_jpg") is None


def test_compat_status_never_leaks_the_secret(client, token):
    body = client.get("/api/immich/status").json()
    assert body["configured"] is True
    assert body["email"] == EMAIL
    assert body["api_key_set"] is True
    serialized = json.dumps(body)
    assert auth.account()["api_key"] not in serialized
    assert PASSWORD not in serialized
