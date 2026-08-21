"""전용 앱(APK) 배포 갱신 테스트.

APK는 git에 없어서 git pull로는 갱신되지 않는다. 서버가 mobile/APK_URL.txt와
로컬 APK의 출처를 비교해 새 빌드를 받아오는 것이 유일한 배포 경로다.
"""
import threading
import time
import urllib.error

import pytest

from backend import appdist


class FakeResponse:
    """urlopen이 돌려주는 응답 흉내 — with 문과 read(n)만 쓴다."""

    def __init__(self, body, length=None):
        self._body = body
        self._pos = 0
        self.headers = {"Content-Length": str(length if length is not None else len(body))}

    def read(self, n):
        chunk = self._body[self._pos:self._pos + n]
        self._pos += len(chunk)
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def dist(tmp_path, monkeypatch):
    """임시 저장소·data 폴더로 배포 모듈을 갈아끼운다."""
    apk = tmp_path / "data" / "app" / "photonest-uploader.apk"
    apk.parent.mkdir(parents=True)
    mobile = tmp_path / "mobile"
    mobile.mkdir()
    monkeypatch.setattr(appdist, "APK_PATH", apk)
    monkeypatch.setattr(appdist, "SRC_FILE", apk.parent / (apk.name + ".url"))
    monkeypatch.setattr(appdist, "URL_FILE", mobile / "APK_URL.txt")
    monkeypatch.setattr(appdist, "APP_JSON", mobile / "app.json")
    appdist._state.clear()
    appdist._state.update(state="idle", percent=0, error=None)
    yield tmp_path
    appdist._state.clear()
    appdist._state.update(state="idle", percent=0, error=None)


def set_release(dist, url, version, body=b"APK-BYTES"):
    """저장소가 새 빌드를 가리키게 하고, 그 링크가 내려줄 내용을 정한다."""
    (dist / "mobile" / "APK_URL.txt").write_text(url + "\n", encoding="utf-8")
    (dist / "mobile" / "app.json").write_text(
        '{"expo": {"version": "%s"}}' % version, encoding="utf-8")
    return body


def serve(monkeypatch, body, length=None, error=None):
    """urlopen을 가로채 준비한 바이트를 돌려준다. 받은 Request 목록 반환."""
    calls = []

    def fake(req, timeout=None):
        calls.append(req)
        if error:
            raise error
        return FakeResponse(body, length)

    monkeypatch.setattr(appdist.urllib.request, "urlopen", fake)
    return calls


def urls(calls):
    return [c.full_url for c in calls]


def wait_idle(timeout=5):
    """백그라운드 다운로드가 끝날 때까지 기다린다."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if appdist._state["state"] != "downloading":
            return
        time.sleep(0.01)
    raise AssertionError("다운로드가 끝나지 않았다")


# ---------- 신선도 판정 ----------

def test_not_current_when_apk_missing(dist):
    set_release(dist, "https://e.dev/new.apk", "1.1.0")
    assert not appdist.is_current()


def test_not_current_when_source_url_differs(dist):
    """v1.0.0을 받아둔 기기에 새 링크가 내려온 상황 — 갱신 대상."""
    set_release(dist, "https://e.dev/new.apk", "1.1.0")
    appdist.APK_PATH.write_bytes(b"OLD")
    appdist.SRC_FILE.write_text("https://e.dev/old.apk\n", encoding="utf-8")

    assert not appdist.is_current()


def test_not_current_when_source_unknown(dist):
    """출처 기록 없이 APK만 있는 기존 설치도 한 번은 갱신 대상이 된다."""
    set_release(dist, "https://e.dev/new.apk", "1.1.0")
    appdist.APK_PATH.write_bytes(b"OLD")

    assert not appdist.is_current()


def test_current_when_source_matches(dist):
    set_release(dist, "https://e.dev/new.apk", "1.1.0")
    appdist.APK_PATH.write_bytes(b"NEW")
    appdist.SRC_FILE.write_text("https://e.dev/new.apk\n", encoding="utf-8")

    assert appdist.is_current()


def test_non_https_url_ignored(dist):
    """저장소 파일이 신뢰 경계 — http나 file:// 링크는 받지 않는다."""
    set_release(dist, "file:///etc/passwd", "1.1.0")

    assert appdist.wanted_url() == ""
    assert appdist.refresh() is False


# ---------- 다운로드 ----------

def test_downloads_new_build_and_records_source(dist, monkeypatch):
    body = set_release(dist, "https://e.dev/new.apk", "1.1.0", b"NEW-APK-BYTES")
    calls = serve(monkeypatch, body)

    assert appdist.refresh() is True
    wait_idle()

    assert urls(calls) == ["https://e.dev/new.apk"]
    assert appdist.APK_PATH.read_bytes() == b"NEW-APK-BYTES"
    assert appdist.SRC_FILE.read_text().strip() == "https://e.dev/new.apk"
    assert appdist.is_current()
    assert appdist._state["state"] == "done"


def test_sends_explicit_user_agent(dist, monkeypatch):
    """EAS(CloudFront)는 urllib 기본 UA를 403으로 막는다 — 실제 배포를 막았던 원인."""
    body = set_release(dist, "https://e.dev/new.apk", "1.1.0")
    calls = serve(monkeypatch, body)

    appdist.refresh()
    wait_idle()

    ua = calls[0].get_header("User-agent")
    assert ua and "python-urllib" not in ua.lower()


def test_skips_download_when_already_current(dist, monkeypatch):
    set_release(dist, "https://e.dev/new.apk", "1.1.0")
    appdist.APK_PATH.write_bytes(b"NEW")
    appdist.SRC_FILE.write_text("https://e.dev/new.apk\n", encoding="utf-8")
    calls = serve(monkeypatch, b"SHOULD-NOT-BE-FETCHED")

    assert appdist.refresh() is False
    assert calls == []


def test_truncated_download_is_rejected(dist, monkeypatch):
    """중간에 끊긴 파일을 배포하면 폰에서 설치가 깨진다 — 기존 APK를 지킨다."""
    set_release(dist, "https://e.dev/new.apk", "1.1.0")
    appdist.APK_PATH.write_bytes(b"OLD-BUT-WORKING")
    serve(monkeypatch, b"HALF", length=999)  # 선언한 길이보다 적게 옴

    appdist.refresh()
    wait_idle()

    assert appdist._state["state"] == "error"
    assert appdist.APK_PATH.read_bytes() == b"OLD-BUT-WORKING"
    assert not list(appdist.APK_PATH.parent.glob(".pndl-*"))


def test_network_error_keeps_previous_apk(dist, monkeypatch):
    set_release(dist, "https://e.dev/new.apk", "1.1.0")
    appdist.APK_PATH.write_bytes(b"OLD-BUT-WORKING")
    serve(monkeypatch, b"", error=OSError("연결 실패"))

    appdist.refresh()
    wait_idle()

    assert appdist._state["state"] == "error"
    assert "연결 실패" in appdist._state["error"]
    assert appdist.APK_PATH.read_bytes() == b"OLD-BUT-WORKING"


def test_expired_build_link_is_explained(dist, monkeypatch):
    """EAS 아티팩트는 만료된다 — 404를 '링크 만료'로 풀어줘야 조치할 수 있다."""
    set_release(dist, "https://e.dev/gone.apk", "1.1.0")
    serve(monkeypatch, b"", error=urllib.error.HTTPError(
        "https://e.dev/gone.apk", 404, "Not Found", {}, None))

    appdist.refresh()
    wait_idle()

    assert appdist._state["state"] == "error"
    assert "만료" in appdist._state["error"]
    assert "APK_URL.txt" in appdist._state["error"]


def test_other_http_errors_are_verbatim(dist, monkeypatch):
    set_release(dist, "https://e.dev/new.apk", "1.1.0")
    serve(monkeypatch, b"", error=urllib.error.HTTPError(
        "https://e.dev/new.apk", 500, "Server Error", {}, None))

    appdist.refresh()
    wait_idle()

    assert "500" in appdist._state["error"]
    assert "만료" not in appdist._state["error"]


def test_failure_is_not_retried_automatically(dist, monkeypatch):
    """페이지를 열 때마다 80MB를 다시 받지 않도록, 실패 뒤엔 멈춰 있는다."""
    set_release(dist, "https://e.dev/new.apk", "1.1.0")
    serve(monkeypatch, b"", error=OSError("연결 실패"))
    appdist.refresh()
    wait_idle()

    calls = serve(monkeypatch, b"NEW-APK")
    assert appdist.refresh() is False       # 자동 호출은 그냥 넘어가고
    assert calls == []

    assert appdist.refresh(force=True) is True   # 사용자가 누르면 다시 받는다
    wait_idle()
    assert appdist.APK_PATH.read_bytes() == b"NEW-APK"


def test_concurrent_refresh_downloads_once(dist, monkeypatch):
    """탭을 여러 번 열어도 다운로드는 하나만 돈다."""
    body = set_release(dist, "https://e.dev/new.apk", "1.1.0")
    gate = threading.Event()
    calls = []

    def fake(req, timeout=None):
        calls.append(req)
        gate.wait(5)
        return FakeResponse(body)

    monkeypatch.setattr(appdist.urllib.request, "urlopen", fake)

    assert appdist.refresh() is True
    for _ in range(5):
        appdist.refresh()
    assert appdist._state["state"] == "downloading"
    gate.set()
    wait_idle()

    assert urls(calls) == ["https://e.dev/new.apk"]


# ---------- 상태 보고 ----------

def test_status_reports_version_and_freshness(dist):
    set_release(dist, "https://e.dev/new.apk", "1.1.0")
    appdist.APK_PATH.write_bytes(b"OLD")

    s = appdist.status()

    assert s["apk_version"] == "1.1.0"
    assert s["apk_current"] is False
    assert s["apk_update"]["state"] == "idle"


def test_status_without_repo_files(dist):
    """링크도 app.json도 없는 설치에서 죽지 않는다."""
    s = appdist.status()

    assert s["apk_version"] is None
    assert s["apk_current"] is False
