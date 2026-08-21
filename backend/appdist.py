"""전용 업로더 앱(APK) 배포 — 저장소의 링크와 로컬 APK를 맞춘다.

APK는 80MB가 넘어 git에 넣지 않는다(data/는 gitignore). 저장소에는
mobile/APK_URL.txt(EAS 빌드 링크)만 올라가므로 git pull로는 앱이 갱신되지
않는다 — 그래서 서버가 그 링크와 로컬 APK가 어긋나면 직접 받아온다.
받은 뒤에는 출처를 .url 옆파일에 남겨 다음에 다시 받지 않는다
(scripts/install-termux.sh도 같은 규칙을 쓴다).
"""
import json
import os
import threading
import urllib.error
import urllib.request
from pathlib import Path

from fastapi import APIRouter

BASE_DIR = Path(__file__).resolve().parent.parent
APK_PATH = BASE_DIR / "data" / "app" / "photonest-uploader.apk"
APK_DEV_PATH = BASE_DIR / "data" / "app" / "photonest-uploader-dev.apk"
SRC_FILE = APK_PATH.parent / (APK_PATH.name + ".url")  # 받아둔 APK의 출처
URL_FILE = BASE_DIR / "mobile" / "APK_URL.txt"
APP_JSON = BASE_DIR / "mobile" / "app.json"

router = APIRouter()

_lock = threading.Lock()
_state = {"state": "idle", "percent": 0, "error": None}  # idle|downloading|done|error


def _read(path: Path):
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def wanted_url():
    """저장소가 가리키는 APK 링크. https만 허용(저장소 파일이 곧 신뢰 경계)."""
    url = _read(URL_FILE)
    return url if url.startswith("https://") else ""


def wanted_version():
    """그 링크가 가리키는 앱 버전 — 같은 커밋에서 함께 올라간다."""
    try:
        return json.loads(APP_JSON.read_text(encoding="utf-8"))["expo"]["version"]
    except (OSError, ValueError, KeyError):
        return None


def is_current():
    """로컬 APK가 저장소 링크와 같은 빌드인가."""
    url = wanted_url()
    return bool(url) and APK_PATH.is_file() and _read(SRC_FILE) == url


def _set(**kw):
    with _lock:
        _state.update(kw)


def _download(url):
    tmp = APK_PATH.parent / f".pndl-{APK_PATH.name}"
    # EAS(CloudFront)는 urllib 기본 User-Agent를 403으로 막는다 — 밝혀서 요청한다.
    req = urllib.request.Request(url, headers={"User-Agent": "PhotoNest-Server"})
    try:
        APK_PATH.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310 — https 고정
            total = int(r.headers.get("Content-Length") or 0)
            got = 0
            with open(tmp, "wb") as f:
                while True:
                    chunk = r.read(256 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
                    got += len(chunk)
                    if total:
                        _set(percent=min(99, got * 100 // total))
        if total and got != total:  # 끊긴 다운로드를 완료로 오인하지 않도록
            raise OSError(f"내려받은 크기가 다릅니다 ({got}/{total})")
        os.replace(tmp, APK_PATH)  # 원자적 교체 — 받는 도중의 파일이 배포되지 않게
        SRC_FILE.write_text(url + "\n", encoding="utf-8")
        _set(state="done", percent=100, error=None)
    except urllib.error.HTTPError as e:
        # EAS 아티팩트는 일정 기간이 지나면 사라진다. 링크가 죽은 것과 네트워크
        # 문제를 구분해줘야 '새로 빌드해야 한다'는 걸 알 수 있다.
        _abort(tmp, f"HTTP {e.code} — 빌드 링크가 만료됐거나 접근할 수 없습니다. "
                    f"새로 빌드해 mobile/APK_URL.txt를 갱신하세요."
                    if e.code in (403, 404) else f"HTTP {e.code} {e.reason}")
    except Exception as e:  # noqa: BLE001 — 배포는 선택 기능, 서버는 계속 뜬다
        _abort(tmp, str(e))


def _abort(tmp: Path, message):
    try:
        tmp.unlink()
    except OSError:
        pass
    _set(state="error", error=message)


def refresh(force=False):
    """필요하면 백그라운드로 최신 APK를 받아온다. 시작했으면 True."""
    url = wanted_url()
    if not url:
        return False
    with _lock:
        if _state["state"] == "downloading":
            return True
        if not force and (is_current() or _state["state"] == "error"):
            # 실패한 뒤에는 자동 재시도하지 않는다 — 페이지 열 때마다 80MB를
            # 다시 받는 일이 없도록. 사용자가 '다시 시도'를 누르면 force로 온다.
            return False
        _state.update(state="downloading", percent=0, error=None)
    threading.Thread(target=_download, args=(url,), daemon=True).start()
    return True


def status():
    with _lock:
        st = dict(_state)
    return {
        "apk_version": wanted_version(),
        "apk_current": is_current(),
        "apk_update": st,
    }


@router.post("/api/app-refresh")
def app_refresh():
    """'최신 앱 받기' — 실패 후 재시도나 강제 재다운로드."""
    return {"ok": True, "started": refresh(force=True), **status()}
