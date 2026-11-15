"""Google Takeout zip 자동 처리.

photos 폴더(또는 PHOTOS_DIR)에 `takeout-*.zip`을 넣어두면, 색인 전에 자동으로:
  1. zip 안의 Takeout/ 구조를 photos 아래로 머지 추출 (이미 있는 파일은 건너뜀)
  2. 처리한 zip은 photos/_zips_done/ 으로 이동 — 설정 takeout_keep_zip이 꺼져
     있으면 대신 삭제 (어느 쪽이든 재처리 안 됨)
  3. 처리 내역을 data/takeout_state.json에 기록

PC에서 받은 zip(수 GB)은 브라우저로 올릴 수 있다 — /api/takeout/upload/*:
한 번에 보내면 와이파이가 잠깐만 끊겨도 처음부터 다시라서, 16MB 조각으로 나눠
보내고 끊기면 받은 지점부터 이어 받는다. 조각은 photos/_zips_incoming/에 쌓이고
다 받으면 zip인지 확인한 뒤 photos/로 옮겨 색인(=위 자동 처리)을 시작한다.

계정 직접 로그인/API 백업은 구글 정책상 불가하며, 메타데이터(GPS 등)를 보존하는
유일한 합법 경로가 Takeout이므로 이 워크플로를 매끄럽게 만든다.
"""
import hashlib
import json
import os
import re
import shutil
import threading
import time
import zipfile
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

# 순환 import 방지: indexer에서 PHOTOS_DIR/DATA_DIR를 가져온다
from . import db

STATE_FILE = db.DATA_DIR / "takeout_state.json"
DONE_DIRNAME = "_zips_done"
INCOMING_DIRNAME = "_zips_incoming"  # 업로드 중인 조각 — 스캔·백업에서 제외

_state = {"running": False, "phase": "", "done": 0, "total": 0, "added": 0}
_lock = threading.Lock()


def _photos_dir():
    from . import indexer
    return indexer.PHOTOS_DIR


def _media_exts():
    from . import indexer
    return indexer.IMAGE_EXTS | indexer.VIDEO_EXTS


def keep_zip_enabled():
    """처리 끝난 zip을 보관할지 (설정 takeout_keep_zip, 기본 보관)."""
    from . import settings
    return bool(settings.load().get("takeout_keep_zip", True))


def find_zips():
    """처리 대상 takeout zip 목록 (이름순). _zips_done 안은 제외."""
    root = _photos_dir()
    done = root / DONE_DIRNAME
    zips = []
    incoming = root / INCOMING_DIRNAME
    for p in sorted(root.rglob("*.zip")):
        if done in p.parents or incoming in p.parents:
            continue
        if "takeout" in p.name.lower():
            zips.append(p)
    return zips


def _load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            return {}
    return {}


def _save_state(state):
    db.DATA_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=1))


def _sig(p: Path):
    st = p.stat()
    return f"{st.st_size}-{int(st.st_mtime)}"


def _safe_target(root: Path, member_name: str):
    """zip-slip 방지: 멤버를 root 안으로만 풀도록 정규화. 벗어나면 None."""
    # 절대경로·드라이브 표기 제거
    name = member_name.replace("\\", "/").lstrip("/")
    target = (root / name).resolve()
    if root.resolve() not in target.parents and target != root.resolve():
        return None
    return target


def _extract_one(zip_path: Path, root: Path):
    """zip 한 개를 root 아래로 머지 추출. (추가된 미디어 수, 건너뛴 수) 반환."""
    exts = _media_exts()
    added = skipped = 0
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            target = _safe_target(root, info.filename)
            if target is None:
                continue  # 경로 탈출 시도 무시
            is_media = target.suffix.lower() in exts
            if target.exists():
                skipped += 1
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as dst:
                while True:
                    chunk = src.read(1024 * 1024)
                    if not chunk:
                        break
                    dst.write(chunk)
            if is_media:
                added += 1
    return added, skipped


def process_pending(now_ts=None):
    """미처리 takeout zip을 모두 추출. 새로 추가된 미디어 파일 수 반환."""
    with _lock:
        if _state["running"]:
            return 0
        _state["running"] = True
        _state.update(phase="압축 해제", done=0, total=0, added=0)

    total_added = 0
    try:
        zips = find_zips()
        state = _load_state()
        # 이미 처리된 zip(같은 이름+크기/시각) 제외
        pending = [z for z in zips if state.get(z.name, {}).get("sig") != _sig(z)]
        _state["total"] = len(pending)
        if not pending:
            return 0

        root = _photos_dir()
        keep = keep_zip_enabled()
        done_dir = root / DONE_DIRNAME
        if keep:
            done_dir.mkdir(parents=True, exist_ok=True)

        for i, z in enumerate(pending, 1):
            _state["phase"] = f"압축 해제 {i}/{len(pending)}"
            try:
                added, skipped = _extract_one(z, root)
            except zipfile.BadZipFile:
                state[z.name] = {"sig": _sig(z), "error": "손상된 zip",
                                 "added": 0, "ts": now_ts or int(time.time())}
                _save_state(state)
                _state["done"] += 1
                continue

            total_added += added
            _state["added"] = total_added

            # 처리 완료 → 기록하고 zip을 보관(_zips_done) 또는 삭제(재처리 방지)
            state[z.name] = {"sig": _sig(z), "added": added, "skipped": skipped,
                             "ts": now_ts or int(time.time()),
                             "kept": keep}
            _save_state(state)
            try:
                if keep:
                    dest = done_dir / z.name
                    if dest.exists():
                        dest = done_dir / f"{z.stem}-{int(time.time())}{z.suffix}"
                    z.rename(dest)
                else:
                    z.unlink()
            except OSError:
                pass  # 이동·삭제 실패해도 기록상 처리됨으로 재추출 안 함
            _state["done"] += 1
    finally:
        _state["running"] = False
        _state["phase"] = ""
    return total_added


# ---------- 구글포토 앨범 구조 → PhotoNest 앨범 ----------

# 사용자 앨범이 아닌 자동 생성 폴더 (연도 버킷·휴지통·보관함 등)
_NON_ALBUM = re.compile(
    r"^(\d{4}년의 사진|Photos from \d{4}|휴지통|보관처리된 사진.*|"
    r"처리 실패한 동영상|Failed videos.*|"
    r"Trash|Bin|Archive|Untitled.*|제목없음.*)$", re.IGNORECASE)
_GPHOTO_DIRS = ("Google 포토", "Google Photos")


def detect_albums():
    """색인된 미디어 경로에서 구글포토 앨범 폴더를 감지.

    Takeout 구조: Takeout*/Google 포토/<앨범명>/파일 — 폴더가 곧 앨범.
    여러 조각(zip)에 나뉜 같은 앨범명은 병합한다. 연도 버킷("2016년의 사진")
    같은 자동 폴더는 제외. 반환: [{name, count, album_exists}] (count 내림차순).

    주의: macOS 파일시스템은 한글을 NFD(자모 분해)로 저장하므로 경로를
    NFC로 정규화해 비교한다 — 안 하면 "Google 포토"가 영영 매칭 안 됨.
    """
    import unicodedata
    groups = {}  # 앨범명(NFC) → media_id 목록
    with db.conn() as c:
        rows = c.execute(
            "SELECT id, path FROM media WHERE trashed_at IS NULL"
        ).fetchall()
    for r in rows:
        parts = Path(unicodedata.normalize("NFC", r["path"])).parts
        for i, p in enumerate(parts):
            # Google 포토 바로 아래 '폴더'(파일명이 아닌)가 앨범
            if p in _GPHOTO_DIRS and i + 2 <= len(parts) - 1:
                folder = parts[i + 1]
                if not _NON_ALBUM.match(folder):
                    groups.setdefault(folder, []).append(r["id"])
                break
    existing = {a["name"] for a in db.list_albums()}
    out = [
        {"name": name, "count": len(ids), "album_exists": name in existing}
        for name, ids in groups.items()
    ]
    out.sort(key=lambda a: -a["count"])
    return out, groups


def apply_albums(names=None):
    """감지된 구글포토 앨범 폴더를 실제 앨범으로 생성/보충 (멱등).

    names를 주면 그 앨범들만, None이면 감지된 전부.
    같은 이름의 앨범이 이미 있으면 새로 만들지 않고 사진만 추가한다.
    """
    detected, groups = detect_albums()
    targets = {a["name"] for a in detected} if names is None \
        else {n for n in names if n in groups}
    existing = {a["name"]: a["id"] for a in db.list_albums()}
    created, updated, added_total = 0, 0, 0
    for name in targets:
        ids = groups[name]
        if name in existing:
            aid = existing[name]
            updated += 1
        else:
            aid = db.create_album(name)
            created += 1
        with db.conn() as c:
            before = c.execute(
                "SELECT COUNT(*) FROM album_items WHERE album_id=?", (aid,)
            ).fetchone()[0]
        db.add_to_album(aid, ids)
        with db.conn() as c:
            after = c.execute(
                "SELECT COUNT(*) FROM album_items WHERE album_id=?", (aid,)
            ).fetchone()[0]
        added_total += after - before
    return {"ok": True, "created": created, "updated": updated,
            "added": added_total}


def get_state():
    return _state


# ---------- PC에서 대용량 zip 업로드 (조각 + 이어받기) ----------

router = APIRouter()

CHUNK_MAX = 64 * 1024 * 1024        # 조각 하나의 상한 (클라이언트는 16MB로 보낸다)
STALE_SECONDS = 7 * 24 * 3600       # 일주일 넘게 멈춘 업로드 조각은 지운다
_upload_lock = threading.Lock()


class UploadInit(BaseModel):
    name: str
    size: int
    last_modified: int = 0  # 같은 이름·크기라도 다른 파일이면 새로 받도록


def _incoming_dir():
    from . import indexer
    # SD 카드가 빠진 채로 받으면 내부 저장소에 수 GB가 쌓인다
    if not indexer.library_online():
        raise HTTPException(status_code=503, detail=indexer.library_offline_message())
    d = _photos_dir() / INCOMING_DIRNAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def _upload_id(name, size, last_modified):
    """같은 파일을 다시 고르면 같은 id → 받은 지점부터 이어 받는다."""
    raw = f"{name}|{size}|{last_modified}".encode()
    return hashlib.sha1(raw).hexdigest()[:20]


def _paths(upload_id):
    if not re.fullmatch(r"[0-9a-f]{20}", upload_id):
        raise HTTPException(status_code=400, detail="잘못된 업로드 id")
    d = _incoming_dir()
    return d / f"{upload_id}.part", d / f"{upload_id}.json"


def _read_meta(meta_path):
    try:
        return json.loads(meta_path.read_text())
    except (OSError, ValueError):
        raise HTTPException(status_code=404, detail="업로드를 찾을 수 없습니다. 다시 시작하세요.")


def _cleanup_stale(d: Path):
    now = time.time()
    for p in d.iterdir():
        try:
            if p.suffix in (".part", ".json") and now - p.stat().st_mtime > STALE_SECONDS:
                p.unlink()
        except OSError:
            pass


def _final_name(name):
    """photos/에 둘 이름 — find_zips가 알아보도록 'takeout'이 들어가게."""
    from .upload import portable_name
    name = portable_name(name)
    if "takeout" not in name.lower():
        name = f"takeout-{name}"
    return name


@router.post("/api/takeout/upload/init")
def upload_init(body: UploadInit):
    """업로드 시작(또는 이어받기). 이미 받은 바이트 수(offset)를 돌려준다."""
    if not body.name.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="zip 파일만 올릴 수 있습니다.")
    if body.size <= 0:
        raise HTTPException(status_code=400, detail="빈 파일입니다.")
    d = _incoming_dir()
    _cleanup_stale(d)
    uid = _upload_id(body.name, body.size, body.last_modified)
    part, meta = _paths(uid)
    with _upload_lock:
        offset = part.stat().st_size if part.exists() else 0
        if offset > body.size:  # 망가진 조각 — 처음부터
            part.unlink()
            offset = 0
        # 남은 조각 + 압축 해제분(대략 zip 크기)이 들어갈 자리가 있어야 한다
        need = (body.size - offset) + body.size
        free = shutil.disk_usage(d).free
        if free < need:
            gb = 1024 ** 3
            raise HTTPException(
                status_code=507,
                detail=f"저장 공간이 부족합니다 — 필요 약 {need / gb:.1f}GB "
                       f"(zip + 압축 해제분), 남은 공간 {free / gb:.1f}GB")
        meta.write_text(json.dumps(
            {"name": body.name, "size": body.size, "ts": int(time.time())},
            ensure_ascii=False))
        part.touch()
    return {"upload_id": uid, "offset": offset, "size": body.size}


@router.put("/api/takeout/upload/{upload_id}")
async def upload_chunk(upload_id: str, offset: int, request: Request):
    """offset 위치에 조각을 이어 붙인다. offset이 서버가 받은 크기와 다르면 409와
    함께 실제 offset을 알려 준다 — 클라이언트는 거기서부터 다시 보낸다."""
    part, meta_path = _paths(upload_id)
    meta = _read_meta(meta_path)
    current = part.stat().st_size if part.exists() else 0
    if offset != current:
        raise HTTPException(status_code=409, detail={"offset": current})
    written = 0
    with open(part, "ab") as out:
        async for chunk in request.stream():
            written += len(chunk)
            if written > CHUNK_MAX or current + written > meta["size"]:
                out.truncate(current)  # 넘친 조각은 버리고 원래 크기로
                raise HTTPException(status_code=413, detail="조각이 너무 큽니다.")
            out.write(chunk)
    return {"offset": current + written, "size": meta["size"]}


@router.post("/api/takeout/upload/{upload_id}/complete")
def upload_complete(upload_id: str):
    """다 받았으면 zip인지 확인하고 photos/로 옮긴 뒤 색인(압축 해제)을 시작."""
    from . import indexer
    part, meta_path = _paths(upload_id)
    meta = _read_meta(meta_path)
    with _upload_lock:
        if not part.exists():
            raise HTTPException(status_code=404, detail="업로드를 찾을 수 없습니다.")
        got = part.stat().st_size
        if got != meta["size"]:
            raise HTTPException(status_code=409, detail={"offset": got})
        if not zipfile.is_zipfile(part):
            part.unlink(missing_ok=True)
            meta_path.unlink(missing_ok=True)
            raise HTTPException(status_code=400,
                                detail="올바른 zip 파일이 아닙니다 (손상되었거나 다른 형식).")
        root = _photos_dir()
        name = _final_name(meta["name"])
        dest = root / name
        i = 1
        while dest.exists():
            dest = root / f"{Path(name).stem}_{i}.zip"
            i += 1
        part.rename(dest)  # 같은 드라이브 안 — 복사 없이 즉시
        meta_path.unlink(missing_ok=True)
    threading.Thread(target=indexer.build_index, daemon=True).start()
    return {"ok": True, "name": dest.name, "size": got}


@router.delete("/api/takeout/upload/{upload_id}")
def upload_cancel(upload_id: str):
    part, meta_path = _paths(upload_id)
    with _upload_lock:
        part.unlink(missing_ok=True)
        meta_path.unlink(missing_ok=True)
    return {"ok": True}
