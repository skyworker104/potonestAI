"""폰 리모콘 — 폰에서 말한 내용을 이 서버 화면에서 실행한다.

폰이 인식한 텍스트를 /api/remote/say로 보내면, 태블릿 브라우저가
/api/remote/poll로 받아 handleUtterance()에 그대로 넘긴다. 즉 서버 화면에서
마이크를 눌러 말한 것과 완전히 같은 경로를 탄다 — 검색뿐 아니라 슬라이드쇼·
앨범 같은 화면 조작까지 동일하게 동작한다(그 명령들은 브라우저 안에서만
처리되므로 /api/chat으로는 대신할 수 없다).

밀린 명령은 흘려보낸다. 화면을 켜자마자 몇 분 전 "슬라이드쇼 시작"이 갑자기
실행되면 곤란하므로, 처음 붙는 클라이언트는 현재 seq만 받아 그 이후부터 듣는다.
"""
import asyncio
import threading
import time

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()

MAX_QUEUE = 50          # 최근 명령만 유지
VIEWER_TIMEOUT = 15.0   # 이 시간 안에 폴링이 있었으면 화면이 열려 있다고 본다
POLL_TICK = 0.25        # 롱폴링 확인 주기
MAX_WAIT = 30.0         # 한 번의 폴링이 붙들 수 있는 최대 시간

_lock = threading.Lock()
_queue = []             # [{seq, text, at}]
_seq = 0
_last_poll = 0.0


class RemoteSay(BaseModel):
    text: str


def _viewer_online():
    return (time.time() - _last_poll) < VIEWER_TIMEOUT


@router.post("/api/remote/say")
def remote_say(req: RemoteSay):
    """폰이 인식한 발화를 큐에 넣는다."""
    global _seq
    text = (req.text or "").strip()
    if not text:
        return {"ok": False, "error": "빈 명령입니다.", "viewer": _viewer_online()}
    with _lock:
        _seq += 1
        _queue.append({"seq": _seq, "text": text, "at": time.time()})
        del _queue[:-MAX_QUEUE]
        seq = _seq
    return {"ok": True, "seq": seq, "text": text, "viewer": _viewer_online()}


@router.get("/api/remote/poll")
async def remote_poll(after: int = -1, wait: float = 25.0):
    """화면이 부르는 롱폴링. after를 주지 않으면 현재 위치만 알려준다.

    after < 0 은 '지금부터 듣겠다'는 최초 동기화 — 밀린 명령은 건너뛴다.
    """
    global _last_poll
    _last_poll = time.time()
    if after < 0:
        with _lock:
            return {"commands": [], "seq": _seq}

    deadline = time.monotonic() + max(0.0, min(wait, MAX_WAIT))
    while True:
        _last_poll = time.time()  # 기다리는 동안에도 화면이 살아 있음을 알린다
        with _lock:
            items = [c for c in _queue if c["seq"] > after]
            seq = _seq
        if items or time.monotonic() >= deadline:
            return {"commands": items, "seq": seq}
        await asyncio.sleep(POLL_TICK)


@router.get("/api/remote/status")
def remote_status():
    """폰이 '태블릿 화면이 열려 있나'를 확인할 때 쓴다."""
    with _lock:
        return {"viewer": _viewer_online(), "seq": _seq}
