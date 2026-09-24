"""Immich 앱이 요청하는 이미지/동영상 응답.

Immich는 크기를 thumbnail(격자용) / preview(전체화면용) / original로 나눈다.
PhotoNest가 색인할 때 만드는 썸네일은 640px 한 종류뿐이라, preview는 원본에서
한 번 더 만들어 디스크에 캐시한다(HEIC 변환 캐시와 같은 폴더).
"""
import hashlib
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse

from .. import indexer
from . import dto

PREVIEW_SIZE = 1440
_PREVIEW_DIR = indexer.DATA_DIR / "preview" / "immich"


def original_path(row) -> Path:
    """원본 파일 경로. 휴지통 항목은 휴지통 안의 실제 파일을 가리킨다."""
    if row.get("trashed_at") and row.get("trash_path"):
        return indexer.DATA_DIR / row["trash_path"]
    return indexer.PHOTOS_DIR / (row["path"] or "")


def _require_file(row) -> Path:
    p = original_path(row)
    if not p.is_file():
        raise HTTPException(status_code=404, detail="Asset file not found")
    return p


def _preview_file(row) -> Path:
    """전체화면용 JPEG (없으면 만든다). 동영상은 색인 썸네일을 쓴다."""
    src = _require_file(row)
    digest = hashlib.md5(
        ("%s|%s|%d" % (row["id"], row["sig"], PREVIEW_SIZE)).encode("utf-8")
    ).hexdigest()
    cached = _PREVIEW_DIR / ("%s.jpg" % digest)
    if cached.is_file():
        return cached
    from PIL import Image, ImageOps
    _PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
    img = Image.open(src)
    img = ImageOps.exif_transpose(img).convert("RGB")
    img.thumbnail((PREVIEW_SIZE, PREVIEW_SIZE))
    tmp = cached.with_suffix(".part")
    img.save(tmp, "JPEG", quality=88)
    tmp.replace(cached)  # 동시 요청이 반쯤 쓰인 파일을 읽지 않도록
    return cached


def thumb_response(row, size="thumbnail"):
    """GET /assets/{id}/thumbnail 응답."""
    thumb = indexer.THUMBS_DIR / ("%s.jpg" % row["id"])
    if size in ("preview", "fullsize") and row["type"] != "video":
        try:
            return FileResponse(_preview_file(row), media_type="image/jpeg")
        except HTTPException:
            raise
        except Exception:
            pass  # 변환 실패 시 색인 썸네일로 대체
    if thumb.is_file():
        return FileResponse(thumb, media_type="image/jpeg")
    if row["type"] == "video":
        raise HTTPException(status_code=404, detail="No thumbnail for this asset")
    return FileResponse(_preview_file(row), media_type="image/jpeg")


def original_response(row):
    """GET /assets/{id}/original — 원본 바이트 그대로 (Range 지원)."""
    p = _require_file(row)
    return FileResponse(p, media_type=dto.mime_type(row["path"]),
                        filename=Path(row["path"] or "").name)


def video_response(row):
    """GET /assets/{id}/video/playback — 트랜스코딩 없이 원본 스트리밍."""
    if row["type"] != "video":
        raise HTTPException(status_code=404, detail="Not a video")
    p = _require_file(row)
    return FileResponse(p, media_type=dto.mime_type(row["path"]))


def person_thumb_response(face_id):
    from ..faces import FACES_DIR
    p = FACES_DIR / ("%s.jpg" % face_id)
    if not p.is_file():
        raise HTTPException(status_code=404, detail="No face thumbnail")
    return FileResponse(p, media_type="image/jpeg")
