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


def get_state():
    return _state
