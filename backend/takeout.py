"""Google Takeout zip 자동 처리.

photos 폴더(또는 PHOTOS_DIR)에 `takeout-*.zip`을 넣어두면, 색인 전에 자동으로:
  1. zip 안의 Takeout/ 구조를 photos 아래로 머지 추출 (이미 있는 파일은 건너뜀)
  2. 처리한 zip은 photos/_zips_done/ 으로 이동 (재처리 방지)
  3. 처리 내역을 data/takeout_state.json에 기록

계정 직접 로그인/API 백업은 구글 정책상 불가하며, 메타데이터(GPS 등)를 보존하는
유일한 합법 경로가 Takeout이므로 이 워크플로를 매끄럽게 만든다.
"""
import json
import os
import re
import threading
import time
import zipfile
from pathlib import Path

# 순환 import 방지: indexer에서 PHOTOS_DIR/DATA_DIR를 가져온다
from . import db

STATE_FILE = db.DATA_DIR / "takeout_state.json"
DONE_DIRNAME = "_zips_done"

_state = {"running": False, "phase": "", "done": 0, "total": 0, "added": 0}
_lock = threading.Lock()


def _photos_dir():
    from . import indexer
    return indexer.PHOTOS_DIR


def _media_exts():
    from . import indexer
    return indexer.IMAGE_EXTS | indexer.VIDEO_EXTS


def find_zips():
    """처리 대상 takeout zip 목록 (이름순). _zips_done 안은 제외."""
    root = _photos_dir()
    done = root / DONE_DIRNAME
    zips = []
    for p in sorted(root.rglob("*.zip")):
        if done in p.parents:
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
        done_dir = root / DONE_DIRNAME
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

            # 처리 완료 → 기록하고 zip을 _zips_done으로 이동(재처리 방지)
            state[z.name] = {"sig": _sig(z), "added": added, "skipped": skipped,
                             "ts": now_ts or int(time.time())}
            _save_state(state)
            try:
                dest = done_dir / z.name
                if dest.exists():
                    dest = done_dir / f"{z.stem}-{int(time.time())}{z.suffix}"
                z.rename(dest)
            except OSError:
                pass  # 이동 실패해도 기록상 처리됨으로 재추출 안 함
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
