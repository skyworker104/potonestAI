"""PhotoNest 행 → Immich 응답/동기화 객체 변환.

시간 처리가 이 파일의 핵심이다. PhotoNest의 taken_at은 타임존이 없는 현지
벽시계 문자열(예: '2024-01-15T10:30:00')인데, Immich는 두 가지를 구분한다.
  fileCreatedAt  촬영 순간의 UTC 시각
  localDateTime  촬영지 벽시계 (앱이 타임라인을 이 값으로 묶는다)
그래서 taken_at을 현지시각으로 해석해 UTC로 바꾼 값과, 벽시계 그대로
UTC 표기만 붙인 값을 각각 내보낸다.
"""
import mimetypes
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import state

# 이 계층이 읽는 media 컬럼 (sig에서 파일크기·수정시각을 얻는다)
ASSET_COLS = (
    "id, path, type, taken_at, lat, lon, width, height, duration, favorite, "
    "trashed_at, trash_path, comment, place_name, sig"
)

_IMAGE_MIME = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".webp": "image/webp", ".bmp": "image/bmp", ".gif": "image/gif",
    ".heic": "image/heic", ".heif": "image/heif",
}
_VIDEO_MIME = {
    ".mp4": "video/mp4", ".mov": "video/quicktime", ".avi": "video/x-msvideo",
    ".mkv": "video/x-matroska", ".webm": "video/webm",
}


def mime_type(rel_path):
    suffix = Path(rel_path or "").suffix.lower()
    return (_IMAGE_MIME.get(suffix) or _VIDEO_MIME.get(suffix)
            or mimetypes.guess_type(rel_path or "")[0] or "application/octet-stream")


def _sig_parts(sig):
    """sig('크기-수정시각') → (size, mtime). 못 읽으면 (None, None)."""
    try:
        size, mtime = str(sig).rsplit("-", 1)
        return int(size), int(mtime)
    except (ValueError, AttributeError):
        return None, None


def file_size(row):
    return _sig_parts(row["sig"])[0]


def taken_local(row):
    """촬영시각을 타임존이 붙은 datetime으로. taken_at이 없으면 파일 수정시각."""
    raw = row["taken_at"]
    if raw:
        try:
            dt = datetime.fromisoformat(raw)
        except (ValueError, TypeError):
            dt = None
        if dt is not None:
            # 타임존이 없으면 현지시각으로 해석한다 (astimezone이 현지 오프셋을 붙인다)
            return dt if dt.tzinfo else dt.astimezone()
    _size, mtime = _sig_parts(row["sig"])
    if mtime:
        return datetime.fromtimestamp(mtime).astimezone()
    return datetime.now().astimezone()


def iso_utc(dt):
    """Immich가 받는 형식: 밀리초 + 'Z'."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") \
        + "%03dZ" % (dt.microsecond // 1000)


def iso_wall(dt):
    """벽시계를 그대로 UTC 표기로 (localDateTime — Immich도 이렇게 저장한다)."""
    if dt is None:
        return None
    return dt.replace(tzinfo=None).strftime("%Y-%m-%dT%H:%M:%S.") \
        + "%03dZ" % (dt.microsecond // 1000)


def offset_hours(dt):
    off = dt.utcoffset()
    return round(off.total_seconds() / 3600, 2) if off else 0.0


def _is_video(row):
    return row["type"] == "video"


def duration_ms(row):
    if not _is_video(row):
        return None
    return int(round(float(row["duration"] or 0) * 1000))


def duration_str(row):
    """동기화 스트림용 문자열 표기('0:00:05.500000')."""
    return str(timedelta(seconds=float(row["duration"] or 0)))


def trashed_at(row):
    raw = row["trashed_at"]
    if not raw:
        return None
    try:
        return iso_utc(datetime.fromisoformat(raw).astimezone())
    except (ValueError, TypeError):
        return None


# ---------- 사용자 ----------

def user_response(acct):
    return {
        "id": state.USER_ID,
        "email": acct.get("email", ""),
        "name": acct.get("name") or acct.get("email", ""),
        "avatarColor": "primary",
        "profileImagePath": "",
        "profileChangedAt": "1970-01-01T00:00:00.000Z",
    }


def user_admin_response(acct):
    return dict(
        user_response(acct),
        clusterGroupId="",
        createdAt="1970-01-01T00:00:00.000Z",
        updatedAt="1970-01-01T00:00:00.000Z",
        deletedAt=None,
        isAdmin=True,
        license=None,
        oauthId="",
        quotaSizeInBytes=None,
        quotaUsageInBytes=None,
        shouldChangePassword=False,
        status="active",
        storageLabel=None,
    )


def sync_auth_user(acct):
    return {
        "id": state.USER_ID,
        "email": acct.get("email", ""),
        "name": acct.get("name") or acct.get("email", ""),
        "avatarColor": "primary",
        "deletedAt": None,
        "hasProfileImage": False,
        "isAdmin": True,
        "oauthId": "",
        "pinCode": None,
        "profileChangedAt": "1970-01-01T00:00:00.000Z",
        "quotaSizeInBytes": None,
        "quotaUsageInBytes": 0,
        "storageLabel": None,
    }


def sync_user(acct):
    return {
        "id": state.USER_ID,
        "email": acct.get("email", ""),
        "name": acct.get("name") or acct.get("email", ""),
        "avatarColor": "primary",
        "deletedAt": None,
        "hasProfileImage": False,
        "profileChangedAt": "1970-01-01T00:00:00.000Z",
    }


# ---------- 에셋 ----------

def exif_response(row):
    local = taken_local(row)
    return {
        "city": row["place_name"] or None,
        "country": None,
        "state": None,
        "dateTimeOriginal": iso_utc(local),
        "description": row["comment"] or "",
        "exifImageWidth": row["width"],
        "exifImageHeight": row["height"],
        "exposureTime": None,
        "fNumber": None,
        "fileSizeInByte": file_size(row),
        "focalLength": None,
        "iso": None,
        "latitude": row["lat"],
        "longitude": row["lon"],
        "lensModel": None,
        "make": None,
        "model": None,
        "modifyDate": iso_utc(local),
        # 썸네일 생성 때 EXIF 방향을 픽셀에 반영했고 width/height도 그 뒤 값이다
        "orientation": "1",
        "projectionType": None,
        "rating": None,
        "timeZone": None,
    }


def asset_response(row, checksum, people=None):
    """AssetResponseDto — /assets/{id}, /search/*, TV 앱이 쓰는 형식."""
    local = taken_local(row)
    created = iso_utc(local)
    return {
        "id": state.asset_uuid(row["id"]),
        "ownerId": state.USER_ID,
        "deviceAssetId": row["id"],
        "deviceId": "photonest",
        "libraryId": None,
        "type": "VIDEO" if _is_video(row) else "IMAGE",
        "originalPath": row["path"],
        "originalFileName": Path(row["path"] or "").name,
        "originalMimeType": mime_type(row["path"]),
        "checksum": checksum,
        "fileCreatedAt": created,
        "fileModifiedAt": created,
        "localDateTime": iso_wall(local),
        "createdAt": created,
        "updatedAt": created,
        "duration": duration_ms(row),
        "width": row["width"],
        "height": row["height"],
        "isFavorite": bool(row["favorite"]),
        "isArchived": False,
        "isTrashed": bool(row["trashed_at"]),
        "isOffline": False,
        "isEdited": False,
        "hasMetadata": True,
        "resized": True,
        "visibility": "timeline",
        "thumbhash": None,
        "duplicateId": None,
        "livePhotoVideoId": None,
        "stack": None,
        "tags": [],
        "people": people or [],
        "exifInfo": exif_response(row),
    }


def sync_asset(row, checksum):
    """SyncAssetV1 — 폰 앱이 기기 내 DB에 넣는 형식."""
    local = taken_local(row)
    created = iso_utc(local)
    return {
        "id": state.asset_uuid(row["id"]),
        "ownerId": state.USER_ID,
        "checksum": checksum,
        "originalFileName": Path(row["path"] or "").name,
        "type": "VIDEO" if _is_video(row) else "IMAGE",
        "visibility": "timeline",
        "createdAt": created,
        "fileCreatedAt": created,
        "fileModifiedAt": created,
        "localDateTime": iso_wall(local),
        "deletedAt": trashed_at(row),
        "duration": duration_str(row) if _is_video(row) else None,
        "width": row["width"],
        "height": row["height"],
        "isFavorite": bool(row["favorite"]),
        "isEdited": False,
        "libraryId": None,
        "livePhotoVideoId": None,
        "stackId": None,
        "thumbhash": None,
    }


def sync_asset_exif(row):
    """SyncAssetExifV1 — 25개 키가 모두 있어야 한다(값은 null 가능).

    fps와 profileDescription은 REST의 ExifResponseDto에는 없고 동기화
    페이로드에만 있는 필드라 여기서 채운다.
    """
    return dict(
        exif_response(row),
        assetId=state.asset_uuid(row["id"]),
        fps=None,
        profileDescription=None,
    )


def time_bucket(rows, checksums):
    """TimeBucketAssetResponseDto — 열 단위 배열 묶음."""
    out = {
        "id": [], "ownerId": [], "createdAt": [], "fileCreatedAt": [],
        "localOffsetHours": [], "duration": [], "isFavorite": [], "isImage": [],
        "isTrashed": [], "livePhotoVideoId": [], "projectionType": [],
        "ratio": [], "thumbhash": [], "visibility": [],
        "city": [], "country": [], "latitude": [], "longitude": [], "stack": [],
    }
    for row in rows:
        if checksums is not None and row["id"] not in checksums:
            continue
        local = taken_local(row)
        w, h = row["width"] or 0, row["height"] or 0
        out["id"].append(state.asset_uuid(row["id"]))
        out["ownerId"].append(state.USER_ID)
        out["createdAt"].append(iso_utc(local))
        out["fileCreatedAt"].append(iso_utc(local))
        out["localOffsetHours"].append(offset_hours(local))
        out["duration"].append(duration_ms(row))
        out["isFavorite"].append(bool(row["favorite"]))
        out["isImage"].append(not _is_video(row))
        out["isTrashed"].append(bool(row["trashed_at"]))
        out["livePhotoVideoId"].append(None)
        out["projectionType"].append(None)
        out["ratio"].append(round(w / h, 4) if w and h else 1.0)
        out["thumbhash"].append(None)
        out["visibility"].append("timeline")
        out["city"].append(row["place_name"] or None)
        out["country"].append(None)
        out["latitude"].append(row["lat"])
        out["longitude"].append(row["lon"])
        out["stack"].append(None)
    return out


# ---------- 앨범 / 인물 ----------

def album_response(album, asset_count, thumb_media_id):
    created = album["created_at"] or datetime.now().isoformat()
    try:
        created_iso = iso_utc(datetime.fromisoformat(created).astimezone())
    except (ValueError, TypeError):
        created_iso = iso_utc(datetime.now().astimezone())
    return {
        "id": state.album_uuid(album["id"]),
        "albumName": album["name"] or "",
        "description": "",
        "ownerId": state.USER_ID,
        "albumThumbnailAssetId": (state.asset_uuid(thumb_media_id)
                                  if thumb_media_id else None),
        "albumUsers": [],
        "assetCount": asset_count,
        "createdAt": created_iso,
        "updatedAt": created_iso,
        "hasSharedLink": False,
        "isActivityEnabled": False,
        "shared": False,
        "order": "desc",
    }


def sync_album(album, thumb_media_id):
    created = album["created_at"] or datetime.now().isoformat()
    try:
        created_iso = iso_utc(datetime.fromisoformat(created).astimezone())
    except (ValueError, TypeError):
        created_iso = iso_utc(datetime.now().astimezone())
    return {
        "id": state.album_uuid(album["id"]),
        "name": album["name"] or "",
        "description": "",
        "ownerId": state.USER_ID,
        "createdAt": created_iso,
        "updatedAt": created_iso,
        "isActivityEnabled": False,
        "order": "desc",
        "thumbnailAssetId": (state.asset_uuid(thumb_media_id)
                             if thumb_media_id else None),
    }


def person_response(person):
    return {
        "id": state.person_uuid(person["id"]),
        "name": person["name"] or "",
        "birthDate": None,
        "isHidden": False,
        "isFavorite": False,
        "color": None,
        "thumbnailPath": "",
        "updatedAt": "1970-01-01T00:00:00.000Z",
    }
