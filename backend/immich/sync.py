"""POST /sync/stream — 폰 앱이 기기 안 DB를 채우는 NDJSON 델타 스트림.

Immich 폰 앱(v2 이후)은 서버에서 타임라인 화면을 받아오지 않는다. 이 스트림으로
에셋 목록을 기기 SQLite에 복제하고 화면은 로컬에서 그린다. 즉 이 엔드포인트가
비면 앱 화면도 빈다.

형식: 한 줄에 JSON 하나.
  {"type": "<SyncEntityType>", "data": {...}, "ack": "<임의 문자열>"}
클라이언트는 같은 type이 연속으로 오는 구간을 한 배치로 묶어 처리한 뒤 그 배치
마지막 줄의 ack를 POST /sync/ack로 돌려준다. 그래서 종류별로 뭉쳐서 내보내야
하고, ack 문자열에 워터마크(seq)를 담아 두면 다음 요청에서 그 뒤부터 보낸다.

지원하지 않는 요청 종류(파트너 공유, 메모리, 스택, 인물/얼굴)는 아무 줄도
내보내지 않는다 — 클라이언트는 데이터가 없는 것으로 보고 정상 진행한다.
"""
import json

from . import auth, dto, query, state

BATCH = 500

# 그림자 테이블 kind ↔ 내보낼 엔티티 타입
_ASSET = ("AssetV1", "AssetDeleteV1")
_ALBUM = ("AlbumV1", "AlbumDeleteV1")
_ALBUM_TO_ASSET = ("AlbumToAssetV1", "AlbumToAssetDeleteV1")

# 이 계층이 실제 데이터를 내보내는 요청 종류
HANDLED = {
    "AuthUsersV1", "UsersV1", "AssetsV1", "AssetExifsV1",
    "AlbumsV1", "AlbumToAssetsV1",
}


def _line(entity_type, data, ack):
    return json.dumps(
        {"type": entity_type, "data": data, "ack": ack},
        ensure_ascii=False, separators=(",", ":"),
    ) + "\n"


def _ack(entity_type, seq):
    return "%s|%d" % (entity_type, seq)


def parse_ack(ack: str):
    """ack 문자열 → (엔티티 타입, seq). 형식이 아니면 None."""
    try:
        entity_type, seq = str(ack).rsplit("|", 1)
        return entity_type, int(seq)
    except (ValueError, AttributeError):
        return None


# ---------- 그림자 테이블 갱신 ----------

def refresh_all():
    """스트림을 열기 전에 현재 라이브러리 상태를 그림자 테이블에 반영한다."""
    acct = auth.account()
    state.refresh("user", {
        state.USER_ID: query.fingerprint(acct.get("email"), acct.get("name")),
    })
    upserts, deletes = query.asset_changes()
    state.apply_changes("asset", upserts, deletes)
    state.register("asset", [ref for ref, _fp in upserts])

    albums = query.albums()
    state.refresh("album", query.album_fingerprints(albums))
    state.register("album", [a["id"] for a in albums])

    state.refresh("album_asset", {
        "%s|%s" % (album_id, media_id): "1"
        for album_id, media_id in query.album_asset_pairs()
    })


# ---------- 종류별 스트림 ----------

def _stream_deletes(session, kind, entity_type, build):
    after = state.ack_get(session, entity_type)
    while True:
        rows = state.changed_rows(kind, True, after, BATCH)
        if not rows:
            return
        for ref, seq in rows:
            payload = build(ref)
            if payload is not None:
                yield _line(entity_type, payload, _ack(entity_type, seq))
        after = rows[-1][1]


def _stream_upserts(session, kind, entity_type, build_batch):
    """build_batch(refs) → {ref: payload}. payload가 없는 ref는 건너뛴다."""
    after = state.ack_get(session, entity_type)
    while True:
        rows = state.changed_rows(kind, False, after, BATCH)
        if not rows:
            return
        payloads = build_batch([ref for ref, _seq in rows])
        for ref, seq in rows:
            payload = payloads.get(ref)
            if payload is not None:
                yield _line(entity_type, payload, _ack(entity_type, seq))
        after = rows[-1][1]


def _asset_rows(refs):
    rows = query.assets_by_ids(refs)
    checksums = state.checksums_for([r["id"] for r in rows])
    return rows, checksums


def _build_assets(refs):
    rows, checksums = _asset_rows(refs)
    out = {}
    for row in rows:
        sha1 = checksums.get(row["id"])
        if sha1:  # 체크섬이 사라졌다면(파일 교체 중) 다음 동기화에서 다시 나간다
            out[row["id"]] = dto.sync_asset(row, sha1)
    return out


def _build_asset_exifs(refs):
    rows, _checksums = _asset_rows(refs)
    return {row["id"]: dto.sync_asset_exif(row) for row in rows}


def _build_albums(refs):
    wanted = set(str(r) for r in refs)
    out = {}
    for album in query.albums():
        if str(album["id"]) in wanted:
            out[str(album["id"])] = dto.sync_album(album, album["thumb_media_id"])
    return out


def _album_to_asset(ref):
    """'앨범id|미디어id' → AlbumToAssetV1 / 삭제 payload."""
    try:
        album_id, media_id = str(ref).split("|", 1)
    except ValueError:
        return None
    return {
        "albumId": state.album_uuid(album_id),
        "assetId": state.asset_uuid(media_id),
    }


def _handle(session, request_type):
    """요청 종류 하나에 해당하는 줄들을 생성한다."""
    acct = auth.account()

    if request_type == "AuthUsersV1":
        for line in _stream_upserts(
            session, "user", "AuthUserV1",
            lambda refs: {r: dto.sync_auth_user(acct) for r in refs},
        ):
            yield line

    elif request_type == "UsersV1":
        for line in _stream_upserts(
            session, "user", "UserV1",
            lambda refs: {r: dto.sync_user(acct) for r in refs},
        ):
            yield line

    elif request_type == "AssetsV1":
        for line in _stream_deletes(
            session, "asset", "AssetDeleteV1",
            lambda ref: {"assetId": state.asset_uuid(ref)},
        ):
            yield line
        for line in _stream_upserts(session, "asset", "AssetV1", _build_assets):
            yield line

    elif request_type == "AssetExifsV1":
        for line in _stream_upserts(
            session, "asset", "AssetExifV1", _build_asset_exifs
        ):
            yield line

    elif request_type == "AlbumsV1":
        for line in _stream_deletes(
            session, "album", "AlbumDeleteV1",
            lambda ref: {"albumId": state.album_uuid(ref)},
        ):
            yield line
        for line in _stream_upserts(session, "album", "AlbumV1", _build_albums):
            yield line

    elif request_type == "AlbumToAssetsV1":
        for line in _stream_deletes(
            session, "album_asset", "AlbumToAssetDeleteV1", _album_to_asset
        ):
            yield line
        for line in _stream_upserts(
            session, "album_asset", "AlbumToAssetV1",
            lambda refs: {r: _album_to_asset(r) for r in refs},
        ):
            yield line


def stream(session: str, request_types, reset=False):
    """POST /sync/stream 본문 생성기. 요청한 종류를 순서대로 훑는다."""
    if reset:
        state.ack_clear(session)
    refresh_all()
    for request_type in request_types:
        if request_type not in HANDLED:
            continue  # 지원 안 하는 종류 — 데이터 없음으로 둔다
        for line in _handle(session, request_type):
            yield line.encode("utf-8")
