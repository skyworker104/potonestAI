"""폰 리모콘 명령 채널 테스트.

폰이 보낸 발화가 태블릿 화면으로 정확히 한 번 전달되는지, 그리고 화면을
켜기 전에 쌓인 명령이 갑자기 실행되지 않는지를 고정한다.
"""
import threading
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import remote


@pytest.fixture
def client():
    remote._queue.clear()
    remote._seq = 0
    remote._last_poll = 0.0
    app = FastAPI()
    app.include_router(remote.router)
    with TestClient(app) as c:
        yield c
    remote._queue.clear()
    remote._seq = 0
    remote._last_poll = 0.0


def say(client, text):
    return client.post("/api/remote/say", json={"text": text}).json()


def sync(client):
    """화면이 처음 붙을 때처럼 현재 위치만 받아온다."""
    return client.get("/api/remote/poll").json()["seq"]


def poll(client, after, wait=0.0):
    return client.get(f"/api/remote/poll?after={after}&wait={wait}").json()


# ---------- 전달 ----------

def test_utterance_reaches_the_screen(client):
    seq = sync(client)
    say(client, "바닷가 사진 찾아줘")

    got = poll(client, seq)

    assert [c["text"] for c in got["commands"]] == ["바닷가 사진 찾아줘"]


def test_delivered_only_once(client):
    seq = sync(client)
    say(client, "슬라이드쇼 시작")

    first = poll(client, seq)
    second = poll(client, first["seq"])

    assert len(first["commands"]) == 1
    assert second["commands"] == []


def test_order_preserved(client):
    seq = sync(client)
    for t in ["첫 번째", "두 번째", "세 번째"]:
        say(client, t)

    got = poll(client, seq)

    assert [c["text"] for c in got["commands"]] == ["첫 번째", "두 번째", "세 번째"]


def test_backlog_is_skipped_on_first_connect(client):
    """화면을 켜기 전에 쌓인 명령이 갑자기 실행되면 안 된다."""
    say(client, "슬라이드쇼 시작")
    say(client, "휴지통 비워")

    seq = sync(client)                 # 이제 막 화면이 열렸다
    got = poll(client, seq)

    assert got["commands"] == []


def test_reconnect_resumes_from_last_seen(client):
    """폴링이 끊겼다 붙어도 그 사이 명령을 놓치지 않는다."""
    seq = sync(client)
    say(client, "다음 사진")
    got = poll(client, seq)            # 받고 나서 연결이 끊겼다고 치자
    say(client, "즐겨찾기에 추가해줘")

    resumed = poll(client, got["seq"])

    assert [c["text"] for c in resumed["commands"]] == ["즐겨찾기에 추가해줘"]


# ---------- 입력 검증 ----------

@pytest.mark.parametrize("text", ["", "   ", "\n"])
def test_blank_utterance_rejected(client, text):
    r = say(client, text)

    assert r["ok"] is False
    assert remote._queue == []


def test_whitespace_is_trimmed(client):
    seq = sync(client)
    say(client, "  슬라이드쇼 시작  ")

    assert poll(client, seq)["commands"][0]["text"] == "슬라이드쇼 시작"


def test_queue_is_bounded(client):
    """오래 켜둬도 메모리가 늘지 않는다."""
    for i in range(remote.MAX_QUEUE + 20):
        say(client, f"명령{i}")

    assert len(remote._queue) == remote.MAX_QUEUE


# ---------- 화면 연결 여부 ----------

def test_viewer_reported_offline_before_any_poll(client):
    """태블릿 화면이 안 열려 있으면 폰에 알려줘야 한다."""
    assert say(client, "슬라이드쇼")["viewer"] is False


def test_viewer_reported_online_after_poll(client):
    sync(client)

    assert say(client, "슬라이드쇼")["viewer"] is True
    assert client.get("/api/remote/status").json()["viewer"] is True


def test_viewer_goes_offline_after_timeout(client, monkeypatch):
    """화면을 닫고 시간이 지나면 다시 offline으로 본다."""
    sync(client)
    real_time = time.time  # 패치된 함수를 다시 부르지 않도록 원본을 붙잡는다
    monkeypatch.setattr(
        remote.time, "time", lambda: real_time() + remote.VIEWER_TIMEOUT + 1)

    assert say(client, "슬라이드쇼")["viewer"] is False


# ---------- 롱폴링 ----------

def test_long_poll_returns_promptly_when_idle(client):
    """명령이 없으면 wait 만큼만 기다리고 조용히 돌아온다."""
    seq = sync(client)
    started = time.monotonic()

    got = poll(client, seq, wait=0.5)

    assert got["commands"] == []
    assert 0.3 < time.monotonic() - started < 5.0


def test_long_poll_wait_is_capped(client, monkeypatch):
    """클라이언트가 큰 wait를 보내도 서버가 무한정 붙들지 않는다."""
    monkeypatch.setattr(remote, "MAX_WAIT", 0.5)
    seq = sync(client)
    started = time.monotonic()

    client.get(f"/api/remote/poll?after={seq}&wait=99999")

    assert time.monotonic() - started < 5


def test_long_poll_wakes_as_soon_as_command_arrives(client):
    """리모콘이니까 폴링이 끝날 때까지 기다리지 않고 바로 전달돼야 한다."""
    seq = sync(client)
    t = threading.Thread(target=lambda: (time.sleep(0.3), say(client, "다음 사진")),
                         daemon=True)
    t.start()
    started = time.monotonic()

    got = poll(client, seq, wait=20)
    t.join(5)

    assert [c["text"] for c in got["commands"]] == ["다음 사진"]
    assert time.monotonic() - started < 10  # 20초를 다 기다리지 않는다
