"""브라우저 재생용 동영상 변환 — 아이폰 HEVC(H.265) 영상 등을 H.264로 바꿔 둔다.

아이폰은 동영상을 HEVC 10비트 HDR로 찍는다. 크롬 등 많은 브라우저가 이 영상을
디코딩하지 못해, 소리는 나는데 화면은 검게 나온다(썸네일은 OpenCV가 만들어서 보인다).
그런 영상을 백그라운드에서 한 편씩 720p H.264로 변환해 data/playback/에 두고,
/media/ 요청 때 원본 대신 내준다. 원본은 그대로 둔다(다운로드·내보내기는 원본).

- ffmpeg가 없으면 아무것도 하지 않는다 (재생 불가 영상은 지금처럼 검은 화면).
- 태블릿에서 1080p 영상 1초 ≈ 변환 1~2초. 그래서 미리, 최근 촬영부터 변환하고,
  사용자가 아직 변환 안 된 영상을 열면 그 영상을 맨 앞으로 당긴다(request).
- HDR(HLG/PQ)은 SDR로 톤매핑한다. 안 하면 색이 바래 보인다.
"""
import os
import shutil
import subprocess
import threading
import time
from collections import deque

from . import db

# 브라우저가 그대로 재생하는 코덱 (OpenCV FOURCC 표기). 나머지는 변환 대상.
PLAYABLE = ("h264", "avc1", "vp80", "vp8", "vp90", "vp9", "vp09", "av01", "av1")

_HDR_TRANSFERS = ("arib-std-b67", "smpte2084")  # HLG(아이폰), PQ
_TONEMAP = ("zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,"
            "tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv")
# 짧은 변 기준 720p로 줄인다 (세로 영상도 같은 화질)
_SCALE = "scale='if(gt(iw,ih),-2,min(720,iw))':'if(gt(iw,ih),min(720,ih),-2)'"

_lock = threading.Lock()
_urgent = deque()      # 사용자가 열어 본, 아직 변환 안 된 영상 — 먼저 처리
_failed = set()        # 변환 실패 — 같은 실행 중 재시도하지 않는다
_state = {"running": False, "current": None}


def available():
    return bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def _cache_dir():
    return db.DATA_DIR / "playback"


def cached_path(media_id):
    return _cache_dir() / f"{media_id}.mp4"


def _source(rel):
    from . import indexer
    return indexer.PHOTOS_DIR / rel


def ready_file(media_id, src):
    """변환본이 있고 원본보다 새것이면 그 경로, 아니면 None."""
    out = cached_path(media_id)
    try:
        if out.stat().st_mtime >= src.stat().st_mtime:
            return out
    except OSError:
        pass
    return None


def _pending():
    """변환할 (id, path, duration) — 이미 변환됐거나 실패한 것은 제외."""
    return [
        (mid, rel, dur) for mid, rel, dur in db.videos_to_convert(PLAYABLE)
        if mid not in _failed and not ready_file(mid, _source(rel))
    ]


def status(media_id):
    """'ready' | 'converting' | 'queued' | 'failed' | 'original'(변환 불필요) | 'unavailable'."""
    m = db.get_media(media_id)
    if not m or m["type"] != "video":
        return "original"
    if ready_file(media_id, _source(m["path"])):
        return "ready"
    if media_id in _failed:
        return "failed"
    if not any(mid == media_id for mid, _, _ in db.videos_to_convert(PLAYABLE)):
        return "original"
    if not available():
        return "unavailable"
    return "converting" if _state["current"] == media_id else "queued"


def request(media_id):
    """이 영상을 다음 차례로 당기고 상태를 돌려준다."""
    st = status(media_id)
    if st == "queued":
        with _lock:
            if media_id not in _urgent:
                _urgent.appendleft(media_id)
        start()
    return st


def start():
    """변환 스레드 시작 (이미 돌고 있으면 무시)."""
    if not available():
        return
    with _lock:
        if _state["running"]:
            return
        _state["running"] = True
    threading.Thread(target=_worker, daemon=True).start()


def drop(media_id):
    """영구 삭제된 미디어의 변환본 제거."""
    try:
        cached_path(media_id).unlink()
    except OSError:
        pass


def _next(queue):
    with _lock:
        while _urgent:
            mid = _urgent.popleft()
            for i, item in enumerate(queue):
                if item[0] == mid:
                    return queue.pop(i)
    return queue.pop(0) if queue else None


def _worker():
    try:
        _cleanup_orphans()
        queue = _pending()
        while True:
            item = _next(queue)
            if item is None:
                # 도는 동안 새로 들어온 영상(업로드)이 있을 수 있다 — 한 번 더 확인
                queue = _pending()
                if not queue and not _urgent:
                    break
                continue
            mid, rel, dur = item
            _wait_for_memory()
            _state["current"] = mid
            if not _convert(mid, _source(rel), dur):
                _failed.add(mid)
            _state["current"] = None
    finally:
        _state["running"] = False
        _state["current"] = None


def _wait_for_memory(min_mb=400):
    """여유 RAM이 모자라면 기다린다 — 1080p 디코딩+인코딩에 수백 MB를 쓴다."""
    from . import indexer
    while (indexer._available_ram_mb() or min_mb) < min_mb:
        time.sleep(30)


def _is_hdr(src):
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=color_transfer", "-of", "csv=p=0", str(src)],
            capture_output=True, text=True, timeout=60,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return False
    return out in _HDR_TRANSFERS


def _convert(media_id, src, duration):
    if not src.is_file():
        return False
    _cache_dir().mkdir(parents=True, exist_ok=True)
    out = cached_path(media_id)
    tmp = out.with_name(out.stem + ".part.mp4")
    vf = _SCALE + ("," + _TONEMAP if _is_hdr(src) else "") + ",format=yuv420p"
    cmd = [
        "ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(src),
        "-map", "0:v:0", "-map", "0:a:0?", "-vf", vf,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "24",
        "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
        "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", "-f", "mp4", str(tmp),
    ]
    try:
        # 서버 응답이 느려지지 않게 낮은 우선순위로. 길이의 10배(최소 10분)면 멈춘 것으로 본다.
        subprocess.run(cmd, capture_output=True, preexec_fn=lambda: os.nice(15),
                       timeout=max(600, (duration or 0) * 10), check=True)
        tmp.replace(out)
        return True
    except (OSError, subprocess.SubprocessError):
        tmp.unlink(missing_ok=True)
        return False


def _cleanup_orphans():
    """라이브러리에서 사라진 영상의 변환본과 중단된 임시 파일 정리."""
    cache = _cache_dir()
    if not cache.is_dir():
        return
    with db.conn() as c:
        ids = {r["id"] for r in c.execute("SELECT id FROM media WHERE type='video'")}
    for f in cache.iterdir():
        if f.name.endswith(".part.mp4") or f.stem not in ids:
            f.unlink(missing_ok=True)
