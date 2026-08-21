"""index.html 캐시 무효화 테스트.

?v= 번호를 손으로 올리는 방식은 잊기 쉽다. 실제로 views.js를 고치고 번호를
안 올려서 태블릿이 몇 커밋 전 스크립트를 계속 썼다 — 서버가 파일 수정시각으로
찍어주는지, 그래서 파일이 바뀌면 주소가 달라지는지를 고정한다.
"""
import os
import re

import pytest
from fastapi.testclient import TestClient

from backend import main


@pytest.fixture
def client(tmp_path, monkeypatch):
    """작은 가짜 프론트엔드로 갈아끼운다."""
    (tmp_path / "app.js").write_text("// app", encoding="utf-8")
    (tmp_path / "views.js").write_text("// views", encoding="utf-8")
    (tmp_path / "styles.css").write_text("body{}", encoding="utf-8")
    (tmp_path / "index.html").write_text(
        '<link rel="stylesheet" href="styles.css?v=24">\n'
        '<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>\n'
        '<script src="app.js?v=23"></script>\n'
        '<script src="views.js?v=20"></script>\n'
        '<script src="없는파일.js?v=3"></script>\n',
        encoding="utf-8")
    monkeypatch.setattr(main, "FRONTEND_DIR", tmp_path)
    with TestClient(main.app) as c:
        yield c, tmp_path


def versions(html):
    return dict(re.findall(r"([\w.\-]+\.(?:js|css))\?v=(\d+)", html))


def test_versions_follow_file_mtime(client):
    c, root = client
    html = c.get("/").text

    v = versions(html)
    assert v["views.js"] == str(int((root / "views.js").stat().st_mtime))
    assert v["app.js"] == str(int((root / "app.js").stat().st_mtime))
    assert v["styles.css"] == str(int((root / "styles.css").stat().st_mtime))


def test_editing_a_file_changes_its_version(client):
    """이게 핵심 — 파일을 고치면 주소가 달라져 브라우저가 새로 받는다."""
    c, root = client
    before = versions(c.get("/").text)["views.js"]

    (root / "views.js").write_text("// views v2", encoding="utf-8")
    os.utime(root / "views.js", (2_000_000_000, 2_000_000_000))

    assert versions(c.get("/").text)["views.js"] != before


def test_untouched_file_keeps_its_version(client):
    """안 바뀐 파일까지 매번 다시 받게 하지는 않는다."""
    c, _ = client

    assert versions(c.get("/").text)["app.js"] == versions(c.get("/").text)["app.js"]


def test_cdn_urls_untouched(client):
    c, _ = client

    assert "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js" in c.get("/").text


def test_missing_file_left_alone(client):
    """없는 파일은 그대로 둔다 — 오타가 500으로 번지지 않게."""
    c, _ = client

    assert "없는파일.js?v=3" in c.get("/").text


def test_index_itself_is_not_cached(client):
    """이 문서가 캐시되면 새 버전 번호가 전달되지 않아 의미가 없다."""
    c, _ = client

    assert "no-cache" in c.get("/").headers.get("cache-control", "")


def test_index_html_path_also_stamped(client):
    c, root = client

    v = versions(c.get("/index.html").text)
    assert v["views.js"] == str(int((root / "views.js").stat().st_mtime))


# ---------- 실제 프론트엔드 ----------

def test_real_index_has_no_stale_manual_versions():
    """실제 index.html의 모든 로컬 스크립트가 서버 스탬프 대상인지 확인."""
    html = (main.FRONTEND_DIR / "index.html").read_text(encoding="utf-8")
    local = re.findall(r'src="([\w.\-]+\.js)(\?v=\d+)?"', html)

    missing = [name for name, ver in local if not ver]
    assert missing == [], f"?v= 가 없어 캐시가 갱신되지 않는 스크립트: {missing}"
