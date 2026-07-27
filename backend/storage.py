"""저장소 관리 — 보관 통계 + USB 백업.

- /api/storage/stats: 사진/동영상 개수·용량, 휴지통·앱데이터 용량,
  라이브러리 드라이브의 사용/가용 공간
- /api/backup/targets: 꽂혀 있는 이동식 볼륨(USB) 목록
- /api/backup/start|status|cancel: photos 라이브러리 + DB 스냅샷을
  USB로 증분 복사 (크기·수정시각이 같으면 건너뜀 — 재실행해도 바뀐 것만)
"""
import os
import shutil
import sqlite3
import sys
import threading
import time
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel

from . import db, indexer

router = APIRouter()

BACKUP_DIR_NAME = "PhotoNestBackup"
_SKIP_DIRS = {"_zips_done"}  # 처리 끝난 Takeout zip — 원본이 이미 풀려 있어 제외


# ---------- 보관 통계 ----------

def _dir_bytes(root: Path, skip: set = frozenset()):
    total = 0
    for dirpath, dirnames, filenames in os.walk(root, onerror=lambda e: None):
        dirnames[:] = [d for d in dirnames if d not in skip and not d.startswith(".")]
        for fn in filenames:
            try:
                total += os.stat(os.path.join(dirpath, fn)).st_size
            except OSError:
                pass
    return total


@router.get("/api/storage/stats")
def storage_stats():
    photos = {"count": 0, "bytes": 0}
    videos = {"count": 0, "bytes": 0}
    trash = {"count": 0, "bytes": 0}
    with db.conn() as c:
        rows = c.execute(
            "SELECT type, path, trash_path, trashed_at FROM media"
        ).fetchall()
    for r in rows:
        # path는 photos/ 기준, trash_path는 data/ 기준 상대경로
        p = (db.DATA_DIR / r["trash_path"]) if r["trashed_at"] \
            else (indexer.PHOTOS_DIR / r["path"])
        try:
            size = os.stat(p).st_size
        except (OSError, TypeError):
            size = 0
        bucket = trash if r["trashed_at"] else (videos if r["type"] == "video" else photos)
        bucket["count"] += 1
        bucket["bytes"] += size

    # 앱 데이터(썸네일·미리보기·색인 DB 등) — 휴지통 원본은 위 trash에 이미 집계
    data_bytes = _dir_bytes(db.DATA_DIR, skip={"trash"})

    du = shutil.disk_usage(indexer.PHOTOS_DIR)
    return {
        "photos": photos,
        "videos": videos,
        "trash": trash,
        "data_bytes": data_bytes,
        "disk": {"total": du.total, "used": du.used, "free": du.free},
    }


# ---------- USB(이동식 볼륨) 감지 ----------

def _removable_volumes():
    vols = []
    seen = set()
    if sys.platform == "darwin":
        candidates = [p for p in Path("/Volumes").glob("*")]
    else:
        candidates = []
        for base in ("/media", "/run/media", "/mnt"):
            b = Path(base)
            if not b.is_dir():
                continue
            for p in b.iterdir():  # /media/<vol> 또는 /media/<user>/<vol>
                if not p.is_dir():
                    continue
                if os.path.ismount(p):
                    candidates.append(p)
                else:
                    candidates.extend(ch for ch in p.iterdir() if ch.is_dir())
    for p in candidates:
        try:
            if not p.is_dir() or not os.path.ismount(p):
                continue
            if os.path.samefile(p, "/"):  # 부팅 볼륨 제외
                continue
            key = os.stat(p).st_dev
            if key in seen:
                continue
            seen.add(key)
            du = shutil.disk_usage(p)
            vols.append({
                "name": p.name,
                "path": str(p),
                "total": du.total,
                "free": du.free,
                "writable": os.access(p, os.W_OK),
            })
        except OSError:
            continue
    return vols


@router.get("/api/backup/targets")
def backup_targets():
    return {"targets": _removable_volumes()}


# ---------- USB 백업 실행 ----------

_bk_lock = threading.Lock()
_bk_cancel = threading.Event()
_bk = {"state": "idle"}  # idle|preparing|running|done|cancelled|error


def _bk_set(**kw):
    with _bk_lock:
        _bk.update(kw)


def _collect_library_files():
    """백업 대상 파일 목록 [(원본 Path, 상대경로, 크기, mtime)]."""
    root = indexer.PHOTOS_DIR
    out = []
    for dirpath, dirnames, filenames in os.walk(root, onerror=lambda e: None):
        dirnames[:] = [d for d in dirnames
                       if d not in _SKIP_DIRS and not d.startswith(".")]
        for fn in filenames:
            if fn.startswith("."):
                continue
            p = Path(dirpath) / fn
            try:
                st = p.stat()
            except OSError:
                continue
            out.append((p, p.relative_to(root), st.st_size, st.st_mtime))
    return out


def _same_file(dst: Path, size, mtime):
    try:
        st = dst.stat()
    except OSError:
        return False
    # FAT/exFAT의 시각 해상도(2초)를 감안한 증분 판정
    return st.st_size == size and abs(st.st_mtime - mtime) <= 2


def _snapshot_db(dest_data: Path):
    """WAL 중간 상태 없이 일관된 DB 사본 생성 (sqlite backup API)."""
    dest_data.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(f"file:{db.DB_FILE}?mode=ro", uri=True)
    dst = sqlite3.connect(dest_data / "photonest.db")
    try:
        src.backup(dst)
    finally:
        src.close()
        dst.close()
    skills = db.DATA_DIR / "skills.json"
    if skills.exists():
        shutil.copy2(skills, dest_data / "skills.json")


def _run_backup(target: Path):
    try:
        _bk_set(state="preparing", dest=str(target), error=None,
                done_files=0, copied=0, skipped=0, failed=0,
                done_bytes=0, current="")
        dest_root = target / BACKUP_DIR_NAME
        files = _collect_library_files()
        todo = [(p, rel, size, mt) for p, rel, size, mt in files
                if not _same_file(dest_root / "photos" / rel, size, mt)]
        skipped = len(files) - len(todo)
        need = sum(size for _, _, size, _ in todo)
        free = shutil.disk_usage(target).free
        if need > free:
            _bk_set(state="error", error=(
                f"USB 공간 부족: {need / 1e9:.1f}GB 필요, "
                f"{free / 1e9:.1f}GB 남음"))
            return
        _bk_set(state="running", total_files=len(files), skipped=skipped,
                total_bytes=need)

        copied = failed = 0
        done_bytes = 0
        for p, rel, size, mtime in todo:
            if _bk_cancel.is_set():
                _bk_set(state="cancelled", current="")
                return
            _bk_set(current=str(rel))
            dst = dest_root / "photos" / rel
            try:
                dst.parent.mkdir(parents=True, exist_ok=True)
                tmp = dst.with_name(f".pncopy-{dst.name}")
                shutil.copy2(p, tmp)
                os.replace(tmp, dst)
                copied += 1
            except OSError:
                failed += 1
            done_bytes += size
            _bk_set(copied=copied, failed=failed, done_bytes=done_bytes,
                    done_files=skipped + copied + failed)

        _bk_set(current="색인 DB 스냅샷…")
        try:
            _snapshot_db(dest_root / "data")
        except Exception:  # noqa: BLE001 — DB 사본 실패가 사진 백업을 무효화하진 않음
            failed += 1
        _bk_set(state="done", current="", failed=failed,
                finished_at=time.time())
    except Exception as e:  # noqa: BLE001
        _bk_set(state="error", error=str(e))


class BackupStart(BaseModel):
    path: str


@router.post("/api/backup/start")
def backup_start(req: BackupStart):
    with _bk_lock:
        if _bk["state"] in ("preparing", "running"):
            return {"ok": False, "error": "이미 백업이 진행 중입니다."}
    target = Path(req.path)
    allowed = {v["path"] for v in _removable_volumes()}
    if str(target) not in allowed:
        return {"ok": False, "error": "USB(이동식 볼륨)를 찾지 못했습니다. 새로고침 후 다시 선택해 주세요."}
    if not os.access(target, os.W_OK):
        return {"ok": False, "error": "쓰기 권한이 없는 드라이브입니다."}
    _bk_cancel.clear()
    threading.Thread(target=_run_backup, args=(target,), daemon=True).start()
    return {"ok": True}


@router.get("/api/backup/status")
def backup_status():
    with _bk_lock:
        return dict(_bk)


@router.post("/api/backup/cancel")
def backup_cancel():
    _bk_cancel.set()
    return {"ok": True}
