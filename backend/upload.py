"""폰 사진 수신 — 업로드 API + WebDAV 서버.

- /api/upload: 모바일 업로드 페이지용 멀티파트 업로드 (원본 바이트 그대로 저장
  → 화질·EXIF·GPS 무손실). 콘텐츠 해시로 이미 있는 사진은 자동 스킵.
- /webdav/*: PhotoSync·FolderSync 등 자동백업 앱용 최소 WebDAV
  (OPTIONS/PROPFIND/MKCOL/PUT/HEAD/GET/DELETE) — 와이파이 진입 시
  백그라운드 자동 업로드는 이 앱들이 담당한다.
"""
import hashlib
import json
import os
import shutil
import socket
import threading
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional
from xml.sax.saxutils import escape

from fastapi import APIRouter, Form, Request, Response, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse

from . import db, indexer

router = APIRouter()

UPLOAD_DIR_NAME = "MobileBackup"


def _upload_root():
    root = indexer.PHOTOS_DIR / UPLOAD_DIR_NAME
    root.mkdir(parents=True, exist_ok=True)
    return root


def _hash_file(path: Path):
    h = hashlib.md5()
    h.update(str(path.stat().st_size).encode())
    with open(path, "rb") as f:
        h.update(f.read(4 * 1024 * 1024))
    return h.hexdigest()


# 방금 업로드되어 아직 색인 전인 파일의 해시 (비동기 색인 지연 동안의 중복 방지)
_recent_hashes = set()


def _hash_exists(hash_):
    if hash_ in _recent_hashes:
        return True
    with db.conn() as c:
        # 활성 사진만 중복으로 간주 — 휴지통(삭제 예정)에 있는 것은 재업로드 허용
        return c.execute(
            "SELECT 1 FROM media WHERE hash=? AND trashed_at IS NULL LIMIT 1", (hash_,)
        ).fetchone() is not None


def forget_hash(hash_):
    """영구삭제 시 메모리 중복 캐시에서 해시 제거 (재업로드 가능하도록)."""
    _recent_hashes.discard(hash_)


def _unique_path(d: Path, name: str):
    name = Path(name).name  # 경로 조작 방지
    p = d / name
    stem, suf = p.stem, p.suffix
    i = 1
    while p.exists():
        p = d / f"{stem}_{i}{suf}"
        i += 1
    return p


def _trigger_index():
    threading.Thread(target=indexer.build_index, daemon=True).start()


# ---------- 서버 정보 (QR 접속용) ----------

@router.get("/api/server-info")
def server_info():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    port = int(os.environ.get("PORT", 8765))
    return {
        "ip": ip,
        "port": port,
        "upload_url": f"http://{ip}:{port}/upload",
        "webdav_url": f"http://{ip}:{port}/webdav",
    }


# ---------- 모바일 업로드 ----------

@router.post("/api/upload")
async def upload(
    files: List[UploadFile] = File(...),
    device: str = Form("phone"),
    meta: str = Form("{}"),  # {filename: lastModified(ms)} — EXIF 없는 파일 날짜 보존
):
    try:
        meta_map = json.loads(meta)
    except Exception:
        meta_map = {}
    device = "".join(ch for ch in device if ch.isalnum() or ch in "-_가-힣a-zA-Z") or "phone"
    dest_dir = _upload_root() / device
    dest_dir.mkdir(parents=True, exist_ok=True)

    results = []
    saved_any = False
    for f in files:
        name = Path(f.filename or "file").name
        ext = Path(name).suffix.lower()
        if ext not in indexer.IMAGE_EXTS | indexer.VIDEO_EXTS:
            results.append({"name": name, "status": "unsupported"})
            continue
        tmp = dest_dir / f".uploading-{name}"
        try:
            with open(tmp, "wb") as out:  # 스트리밍 저장 — 대용량 동영상 대응
                shutil.copyfileobj(f.file, out, length=1024 * 1024)
            h = _hash_file(tmp)
            if _hash_exists(h):
                tmp.unlink()
                results.append({"name": name, "status": "duplicate"})
                continue
            dst = _unique_path(dest_dir, name)
            tmp.rename(dst)
            _recent_hashes.add(h)
            lm = meta_map.get(f.filename)
            if lm:  # 파일 수정시각을 촬영 추정시각으로 보존
                try:
                    os.utime(dst, (lm / 1000, lm / 1000))
                except Exception:
                    pass
            saved_any = True
            results.append({"name": name, "status": "saved"})
        except Exception as e:  # noqa: BLE001
            if tmp.exists():
                tmp.unlink()
            results.append({"name": name, "status": "error", "detail": str(e)})

    if saved_any:
        _trigger_index()
    return {
        "ok": True,
        "saved": sum(1 for r in results if r["status"] == "saved"),
        "duplicate": sum(1 for r in results if r["status"] == "duplicate"),
        "results": results,
    }


# ---------- 최소 WebDAV (자동백업 앱용) ----------

def _dav_path(rest: str) -> Optional[Path]:
    rest = urllib.parse.unquote(rest or "").strip("/")
    p = (_upload_root() / rest).resolve()
    if not str(p).startswith(str(_upload_root().resolve())):
        return None
    return p


def _http_date(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime(
        "%a, %d %b %Y %H:%M:%S GMT"
    )


def _propfind_entry(href: str, p: Path):
    is_dir = p.is_dir()
    return f"""<D:response>
<D:href>{escape(href)}</D:href>
<D:propstat><D:prop>
<D:displayname>{escape(p.name or UPLOAD_DIR_NAME)}</D:displayname>
<D:resourcetype>{"<D:collection/>" if is_dir else ""}</D:resourcetype>
{"" if is_dir else f"<D:getcontentlength>{p.stat().st_size}</D:getcontentlength>"}
<D:getlastmodified>{_http_date(p.stat().st_mtime)}</D:getlastmodified>
</D:prop><D:status>HTTP/1.1 200 OK</D:status></D:propstat>
</D:response>"""


DAV_HEADERS = {
    "DAV": "1, 2",
    "MS-Author-Via": "DAV",
    "Allow": "OPTIONS, GET, HEAD, PUT, DELETE, PROPFIND, MKCOL",
}


@router.api_route("/webdav", methods=["OPTIONS", "PROPFIND", "GET", "HEAD"])
@router.api_route("/webdav/{rest:path}",
                  methods=["OPTIONS", "PROPFIND", "MKCOL", "PUT", "GET", "HEAD", "DELETE"])
async def webdav(request: Request, rest: str = ""):
    method = request.method
    p = _dav_path(rest)
    if p is None:
        return Response(status_code=403)

    if method == "OPTIONS":
        return Response(status_code=200, headers=DAV_HEADERS)

    if method == "PROPFIND":
        if not p.exists():
            return Response(status_code=404, headers=DAV_HEADERS)
        depth = request.headers.get("Depth", "1")
        base_href = "/webdav" + ("/" + rest.strip("/") if rest.strip("/") else "")
        entries = [_propfind_entry(base_href + ("/" if p.is_dir() else ""), p)]
        if depth != "0" and p.is_dir():
            for child in sorted(p.iterdir()):
                if child.name.startswith("."):
                    continue
                href = base_href.rstrip("/") + "/" + urllib.parse.quote(child.name)
                entries.append(_propfind_entry(href + ("/" if child.is_dir() else ""), child))
        xml = ('<?xml version="1.0" encoding="utf-8"?>\n'
               '<D:multistatus xmlns:D="DAV:">' + "".join(entries) + "</D:multistatus>")
        return Response(xml, status_code=207, media_type="application/xml",
                        headers=DAV_HEADERS)

    if method == "MKCOL":
        if p.exists():
            return Response(status_code=405, headers=DAV_HEADERS)
        p.mkdir(parents=True, exist_ok=True)
        return Response(status_code=201, headers=DAV_HEADERS)

    if method == "PUT":
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(f".uploading-{p.name}")
        with open(tmp, "wb") as out:
            async for chunk in request.stream():
                out.write(chunk)
        h = _hash_file(tmp)
        if _hash_exists(h) and not p.exists():
            tmp.unlink()  # 라이브러리에 이미 있는 사진 — 저장 생략, 앱에는 성공 응답
            return Response(status_code=201, headers=DAV_HEADERS)
        tmp.replace(p)
        _recent_hashes.add(h)
        if p.suffix.lower() in indexer.IMAGE_EXTS | indexer.VIDEO_EXTS:
            _trigger_index()
        return Response(status_code=201, headers=DAV_HEADERS)

    if method in ("GET", "HEAD"):
        if not p.is_file():
            return Response(status_code=404, headers=DAV_HEADERS)
        if method == "HEAD":
            return Response(status_code=200, headers={
                **DAV_HEADERS,
                "Content-Length": str(p.stat().st_size),
                "Last-Modified": _http_date(p.stat().st_mtime),
            })
        return FileResponse(p, headers=DAV_HEADERS)

    if method == "DELETE":
        if p.is_file():
            p.unlink()
            return Response(status_code=204, headers=DAV_HEADERS)
        return Response(status_code=404, headers=DAV_HEADERS)

    return Response(status_code=405, headers=DAV_HEADERS)
