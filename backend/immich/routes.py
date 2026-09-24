"""서버 정보 / 인증 / 사용자 / 에셋 / 동기화 엔드포인트.

경로는 Immich 앱이 호출하는 것과 한 글자도 다르면 안 된다. PhotoNest 본체의
/api/albums 등과 충돌하지 않도록 이 라우터 전체는 /immich/api 아래에 붙고,
앱은 /.well-known/immich을 보고 그 주소를 찾아간다(__init__.py 참고).
"""
import base64
import hashlib
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel

from .. import db, indexer, upload
from . import auth, dto, media, query, state, sync

router = APIRouter()

# 앱에 보고할 서버 버전. 2.5.0으로 보고하면 앱이 전부 V1 동기화 타입을 쓰고
# (assetEditsV1은 2.6+, assetOcrV1/syncV2는 3.0+) 이 계층이 구현한 범위와 맞는다.
# 앱은 버전이 낮아도 막지 않는다 — latestVersion을 모르면 안내 배너도 안 뜬다.
REPORT_VERSION = os.environ.get("IMMICH_REPORT_VERSION", "2.5.0")


def _version_parts():
    try:
        major, minor, patch = (int(x) for x in REPORT_VERSION.split(".")[:3])
    except ValueError:
        major, minor, patch = 2, 5, 0
    return major, minor, patch


# ---------- 서버 정보 (앱이 로그인 전에 부른다) ----------

@router.get("/server/ping")
def ping():
    """앱이 "이 주소가 Immich 서버인가"를 판정하는 엔드포인트."""
    return {"res": "pong"}


@router.get("/server/version")
def server_version():
    major, minor, patch = _version_parts()
    return {"major": major, "minor": minor, "patch": patch, "prerelease": None}


@router.get("/server/features")
def server_features():
    return {
        "configFile": False,
        "duplicateDetection": True,
        "email": False,
        "facialRecognition": False,   # PhotoNest 얼굴 인식은 이 계층으로 내보내지 않는다
        "importFaces": False,
        "map": True,
        "oauth": False,
        "oauthAutoLaunch": False,
        "ocr": False,
        "passwordLogin": True,
        "realtimeTranscoding": False,  # 동영상은 원본 그대로 스트리밍한다
        "reverseGeocoding": True,
        "search": True,
        "sidecar": False,
        "smartSearch": False,          # 의미 검색은 PhotoNest 화면에서 쓴다
        "trash": True,
    }


@router.get("/server/config")
def server_config():
    return {
        "externalDomain": "",
        "isInitialized": auth.configured(),
        "isOnboarded": True,
        "loginPageMessage": "",
        "maintenanceMode": False,
        "mapDarkStyleUrl": "https://tiles.immich.cloud/v1/style/dark.json",
        "mapLightStyleUrl": "https://tiles.immich.cloud/v1/style/light.json",
        "minFaces": 3,
        "oauthButtonText": "",
        "publicUsers": False,
        "trashDays": 30,
        "userDeleteDelay": 7,
    }


@router.get("/server/media-types")
def server_media_types():
    return {
        "image": sorted(indexer.IMAGE_EXTS),
        "video": sorted(indexer.VIDEO_EXTS),
        "sidecar": [],
    }


@router.get("/server/about")
def server_about(session: str = Depends(auth.require_session)):
    return {
        "version": "v" + REPORT_VERSION,
        "versionUrl": "https://github.com/immich-app/immich/releases",
        "licensed": False,
        "build": "photonest-compat",
        "repository": "photonest",
        "repositoryUrl": "",
        "sourceRef": "photonest-immich-compat",
        "thirdPartySourceUrl": "",
        "thirdPartyBugFeatureUrl": "",
        "thirdPartyDocumentationUrl": "",
        "thirdPartySupportUrl": "",
    }


def _human(n):
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if n < 1024 or unit == "TiB":
            return "%.1f%s" % (n, unit)
        n /= 1024.0


@router.get("/server/storage")
def server_storage(session: str = Depends(auth.require_session)):
    usage = shutil.disk_usage(str(indexer.PHOTOS_DIR))
    return {
        "diskSize": _human(usage.total), "diskSizeRaw": usage.total,
        "diskUse": _human(usage.used), "diskUseRaw": usage.used,
        "diskAvailable": _human(usage.free), "diskAvailableRaw": usage.free,
        "diskUsagePercentage": round(usage.used / usage.total * 100, 2)
        if usage.total else 0.0,
    }


def _asset_counts():
    with db.conn() as c:
        row = c.execute(
            "SELECT SUM(type='image') images, SUM(type='video') videos, COUNT(*) total "
            "FROM media WHERE trashed_at IS NULL"
        ).fetchone()
    return int(row["images"] or 0), int(row["videos"] or 0), int(row["total"] or 0)


@router.get("/server/statistics")
def server_statistics(session: str = Depends(auth.require_session)):
    images, videos, _total = _asset_counts()
    return {
        "photos": images, "videos": videos,
        "usage": 0, "usagePhotos": 0, "usageVideos": 0, "usageByUser": [],
    }


# ---------- 인증 ----------

class LoginBody(BaseModel):
    email: str
    password: str


@router.post("/auth/login")
def login(body: LoginBody, request: Request):
    if not auth.configured():
        return JSONResponse(
            {"statusCode": 401, "error": "Unauthorized",
             "message": "Immich 호환 계정이 설정되지 않았습니다. "
                        "서버에서 python -m scripts.immich_account 를 실행하세요."},
            status_code=401,
        )
    if not auth.verify(body.email, body.password):
        return JSONResponse(
            {"statusCode": 401, "error": "Unauthorized",
             "message": "Invalid email or password"},
            status_code=401,
        )
    acct = auth.account()
    device = "%s %s" % (request.headers.get("deviceType", "unknown"),
                        request.headers.get("deviceModel", ""))
    token = auth.create_session(device.strip())
    return {
        "accessToken": token,
        "userId": state.USER_ID,
        "userEmail": acct["email"],
        "name": acct.get("name") or acct["email"],
        "isAdmin": True,
        "isOnboarded": True,
        "profileImagePath": "",
        "shouldChangePassword": False,
    }


@router.post("/auth/logout")
def logout(request: Request):
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        auth.drop_session(header[7:].strip())
    return {"successful": True, "redirectUri": "/"}


@router.post("/auth/validateToken")
def validate_token(session: str = Depends(auth.require_session)):
    return {"authStatus": True}


@router.get("/auth/status")
def auth_status(session: str = Depends(auth.require_session)):
    return {"isElevated": True, "password": True, "pinCode": False}


# ---------- 사용자 ----------

@router.get("/users/me")
def users_me(session: str = Depends(auth.require_session)):
    return dto.user_admin_response(auth.account())


@router.get("/users")
def users_list(session: str = Depends(auth.require_session)):
    return [dto.user_response(auth.account())]


@router.get("/users/{user_id}")
def users_get(user_id: str, session: str = Depends(auth.require_session)):
    if user_id != state.USER_ID:
        raise HTTPException(status_code=404, detail="User not found")
    return dto.user_response(auth.account())


_PREFERENCES = {
    "albums": {"defaultAssetOrder": "desc"},
    "cast": {"gCastEnabled": False},
    "download": {"archiveSize": 4 * 1024 ** 3, "includeEmbeddedVideos": False},
    "emailNotifications": {"enabled": False, "albumInvite": False,
                           "albumUpdate": False},
    "folders": {"enabled": True, "sidebarWeb": False},
    "memories": {"enabled": False, "duration": 5, "sidebarWeb": False},
    "people": {"enabled": False, "minimumFaces": 3, "sidebarWeb": False},
    "purchase": {"showSupportBadge": False, "hideBuyButtonUntil":
                 "2100-01-01T00:00:00.000Z"},
    "ratings": {"enabled": False},
    "recentlyAdded": {"sidebarWeb": False},
    "sharedLinks": {"enabled": False, "sidebarWeb": False},
    "tags": {"enabled": False, "sidebarWeb": False},
}


@router.get("/users/me/preferences")
def preferences_get(session: str = Depends(auth.require_session)):
    return _PREFERENCES


@router.put("/users/me/preferences")
def preferences_put(session: str = Depends(auth.require_session)):
    # 설정은 PhotoNest에 저장하지 않는다 — 앱이 보내온 값은 무시하고 현재 값을 돌려준다
    return _PREFERENCES


@router.get("/users/me/onboarding")
def onboarding_get(session: str = Depends(auth.require_session)):
    return {"isOnboarded": True}


@router.put("/users/me/onboarding")
def onboarding_put(session: str = Depends(auth.require_session)):
    return {"isOnboarded": True}


@router.get("/users/{user_id}/profile-image")
def profile_image(user_id: str):
    raise HTTPException(status_code=404, detail="No profile image")


# ---------- 동기화 ----------

class SyncStreamBody(BaseModel):
    types: List[str]
    reset: Optional[bool] = False


@router.post("/sync/stream")
def sync_stream(body: SyncStreamBody, session: str = Depends(auth.require_session)):
    return StreamingResponse(
        sync.stream(session, body.types, bool(body.reset)),
        media_type="application/jsonlines+json",
    )


class SyncAckSetBody(BaseModel):
    acks: List[str]


@router.post("/sync/ack")
def sync_ack_set(body: SyncAckSetBody,
                 session: str = Depends(auth.require_session)):
    for ack in body.acks:
        parsed = sync.parse_ack(ack)
        if parsed:
            state.ack_set(session, parsed[0], parsed[1])
    return Response(status_code=204)


class SyncAckDeleteBody(BaseModel):
    types: Optional[List[str]] = None


@router.delete("/sync/ack")
def sync_ack_delete(body: SyncAckDeleteBody,
                    session: str = Depends(auth.require_session)):
    state.ack_clear(session, body.types)
    return Response(status_code=204)


@router.get("/sync/ack")
def sync_ack_list(session: str = Depends(auth.require_session)):
    return [
        {"type": type_, "ack": "%s|%d" % (type_, seq)}
        for type_, seq in state.ack_all(session).items()
    ]


# ---------- 에셋 조회 ----------

def _asset_or_404(asset_id):
    media_id = state.resolve(asset_id, "asset")
    row = query.asset_by_id(media_id) if media_id else None
    if row is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    return row


@router.get("/assets/statistics")
def asset_statistics(session: str = Depends(auth.require_session)):
    images, videos, total = _asset_counts()
    return {"images": images, "videos": videos, "total": total}


@router.get("/assets/{asset_id}")
def asset_get(asset_id: str, session: str = Depends(auth.require_session)):
    row = _asset_or_404(asset_id)
    checksums = state.checksums_for([row["id"]])
    return dto.asset_response(row, checksums.get(row["id"]) or "")


@router.get("/assets/{asset_id}/thumbnail")
def asset_thumbnail(asset_id: str, size: str = "thumbnail",
                    session: str = Depends(auth.require_session)):
    return media.thumb_response(_asset_or_404(asset_id), size)


@router.get("/assets/{asset_id}/original")
def asset_original(asset_id: str, session: str = Depends(auth.require_session)):
    return media.original_response(_asset_or_404(asset_id))


@router.get("/assets/{asset_id}/video/playback")
def asset_video(asset_id: str, session: str = Depends(auth.require_session)):
    return media.video_response(_asset_or_404(asset_id))


# ---------- 에셋 수정 ----------

class UpdateAssetBody(BaseModel):
    isFavorite: Optional[bool] = None
    description: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None


def _apply_update(media_id, body):
    if body.isFavorite is not None:
        db.set_favorite(media_id, body.isFavorite)
    if body.description is not None:
        db.set_comment(media_id, body.description)
    if body.latitude is not None and body.longitude is not None:
        db.set_location(media_id, body.latitude, body.longitude)


@router.put("/assets/{asset_id}")
def asset_update(asset_id: str, body: UpdateAssetBody,
                 session: str = Depends(auth.require_session)):
    row = _asset_or_404(asset_id)
    _apply_update(row["id"], body)
    row = query.asset_by_id(row["id"])
    checksums = state.checksums_for([row["id"]])
    return dto.asset_response(row, checksums.get(row["id"]) or "")


class BulkUpdateBody(UpdateAssetBody):
    ids: List[str]


@router.put("/assets")
def assets_bulk_update(body: BulkUpdateBody,
                       session: str = Depends(auth.require_session)):
    for media_id in state.resolve_many(body.ids, "asset").values():
        _apply_update(media_id, body)
    return Response(status_code=204)


class BulkDeleteBody(BaseModel):
    ids: List[str]
    force: Optional[bool] = False


@router.delete("/assets")
def assets_delete(body: BulkDeleteBody,
                  session: str = Depends(auth.require_session)):
    for media_id in state.resolve_many(body.ids, "asset").values():
        if body.force:
            indexer.delete_permanently(media_id)
        else:
            indexer.move_to_trash(media_id)
    return Response(status_code=204)


class BulkIdsBody(BaseModel):
    ids: List[str]


@router.post("/trash/restore/assets")
def trash_restore_assets(body: BulkIdsBody,
                         session: str = Depends(auth.require_session)):
    count = 0
    for media_id in state.resolve_many(body.ids, "asset").values():
        indexer.restore_from_trash(media_id)
        count += 1
    return {"count": count}


@router.post("/trash/restore")
def trash_restore_all(session: str = Depends(auth.require_session)):
    items = db.list_trash()
    for item in items:
        indexer.restore_from_trash(item["id"])
    return {"count": len(items)}


@router.post("/trash/empty")
def trash_empty_immich(session: str = Depends(auth.require_session)):
    items = db.list_trash()
    for item in items:
        indexer.delete_permanently(item["id"])
    return {"count": len(items)}


# ---------- 업로드 ----------

class BulkUploadCheckItem(BaseModel):
    id: str
    checksum: str


class BulkUploadCheckBody(BaseModel):
    assets: List[BulkUploadCheckItem]


@router.post("/assets/bulk-upload-check")
def bulk_upload_check(body: BulkUploadCheckBody,
                      session: str = Depends(auth.require_session)):
    """폰이 올리기 전에 "이 체크섬 이미 있나"를 묻는다 (구버전 백업 경로)."""
    results = []
    for item in body.assets:
        media_id = state.media_id_by_sha1(item.checksum)
        if media_id:
            state.register("asset", [media_id])
            results.append({
                "id": item.id, "action": "reject", "reason": "duplicate",
                "assetId": state.asset_uuid(media_id), "isTrashed": False,
            })
        else:
            results.append({"id": item.id, "action": "accept"})
    return {"results": results}


UPLOAD_SUBDIR = "Immich"


def _parse_iso(value):
    """Immich가 보내는 ISO8601(Z) → epoch 초. 실패하면 None."""
    if not value:
        return None
    try:
        text = value.replace("Z", "+00:00")
        return datetime.fromisoformat(text).timestamp()
    except (ValueError, TypeError):
        return None


@router.post("/assets", status_code=201)
async def asset_upload(
    request: Request,
    assetData: UploadFile = File(...),
    filename: Optional[str] = Form(None),
    fileCreatedAt: Optional[str] = Form(None),
    fileModifiedAt: Optional[str] = Form(None),
    session: str = Depends(auth.require_session),
    x_immich_checksum: Optional[str] = Header(None),
):
    """POST /assets — 앱의 백업이 사진을 올리는 곳.

    원본 바이트를 그대로 저장하고(EXIF·GPS 무손실), SHA-1을 계산해 곧바로
    기록한다. 그래서 색인이 끝나기 전에도 앱은 이 사진을 "백업됨"으로 본다.
    """
    name = Path(filename or assetData.filename or "upload").name
    ext = Path(name).suffix.lower()
    if ext not in indexer.IMAGE_EXTS | indexer.VIDEO_EXTS:
        raise HTTPException(status_code=400,
                            detail="Unsupported file type: %s" % ext)

    dest_dir = upload._upload_root() / UPLOAD_SUBDIR
    dest_dir.mkdir(parents=True, exist_ok=True)
    tmp = dest_dir / (".uploading-%s" % name)
    try:
        sha1 = hashlib.sha1()
        with open(tmp, "wb") as out:  # 스트리밍 — 대용량 동영상 대응
            while True:
                chunk = await assetData.read(1024 * 1024)
                if not chunk:
                    break
                sha1.update(chunk)
                out.write(chunk)
        checksum = base64.b64encode(sha1.digest()).decode("ascii")
        # PhotoNest 자체 중복 해시는 이름을 바꿔도 같은 값이라 한 번만 계산한다
        content_hash = upload._hash_file(tmp)

        existing = (state.media_id_by_sha1(checksum)
                    or query.media_id_by_hash(content_hash))
        if existing:
            tmp.unlink()
            state.register("asset", [existing])
            return {"id": state.asset_uuid(existing), "status": "duplicate"}

        dst = upload._unique_path(dest_dir, name)
        tmp.rename(dst)
    except OSError as e:
        if tmp.exists():
            tmp.unlink()
        raise HTTPException(status_code=500, detail="Failed to store file: %s" % e)

    # EXIF가 없는 사진의 촬영시각을 파일 수정시각으로 보존 (색인이 이 값을 쓴다)
    taken = _parse_iso(fileCreatedAt) or _parse_iso(fileModifiedAt)
    if taken:
        try:
            os.utime(dst, (taken, taken))
        except OSError:
            pass

    rel = str(dst.relative_to(indexer.PHOTOS_DIR))
    media_id = state.media_id_for_path(rel)
    stat = dst.stat()
    # 색인이 계산할 sig와 같은 값을 미리 기록해 둔다 (indexer._index_file 참고)
    state.record_checksum(media_id, rel,
                          "%d-%d" % (stat.st_size, int(stat.st_mtime)), checksum)
    state.register("asset", [media_id])
    # 색인 전까지의 중복 업로드를 PhotoNest 쪽에서도 막는다
    upload._recent_hashes.add(content_hash)
    upload._trigger_index()
    return {"id": state.asset_uuid(media_id), "status": "created"}
