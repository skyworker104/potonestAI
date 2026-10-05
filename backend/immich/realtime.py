"""Immich 앱의 실시간 알림 소켓 — 최소한의 socket.io 연결만 받아 준다.

앱은 로그인하면 {API 주소}/socket.io 로 socket.io(웹소켓 전송만)를 열고,
업로드 완료·삭제 같은 이벤트를 기다린다. 이 경로가 없으면 요청이 웹 화면
정적 파일 마운트로 떨어져 500이 나고, 앱은 자동 재접속으로 몇 초마다 다시 온다.

여기서는 Engine.IO v4 / Socket.IO v5 핸드셰이크와 핑만 처리해 연결을 열어 둔다.
이벤트는 보내지 않는다 — 앱은 그래도 동작하고, 변경은 동기화 스트림으로 받는다.

    서버 → 0{"sid":..,"pingInterval":..}   (Engine.IO open)
    앱   → 40[{auth}]                       (기본 네임스페이스 접속)
    서버 → 40{"sid":..}
    서버 → 2 (ping)  /  앱 → 3 (pong)       pingInterval마다
"""
import asyncio
import json
import secrets

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from . import auth

router = APIRouter()

PING_INTERVAL_MS = 25_000   # socket.io 서버 기본값과 같다
PING_TIMEOUT_MS = 20_000


async def _pinger(ws: WebSocket):
    while True:
        await asyncio.sleep(PING_INTERVAL_MS / 1000)
        await ws.send_text("2")


@router.websocket("/socket.io")
@router.websocket("/socket.io/")
async def socket_io(ws: WebSocket):
    if auth.identify(ws) is None:
        await ws.close(code=1008)  # 수락 전 거절 → 403, 앱은 로그인 상태를 다시 확인한다
        return
    await ws.accept()
    await ws.send_text("0" + json.dumps({
        "sid": secrets.token_urlsafe(16),
        "upgrades": [],
        "pingInterval": PING_INTERVAL_MS,
        "pingTimeout": PING_TIMEOUT_MS,
        "maxPayload": 1_000_000,
    }))
    pinger = asyncio.create_task(_pinger(ws))
    try:
        while True:
            # 핑 한 주기 + 대기시간 안에 아무것도 안 오면 끊긴 연결로 본다
            msg = await asyncio.wait_for(
                ws.receive_text(),
                timeout=(PING_INTERVAL_MS + PING_TIMEOUT_MS) / 1000,
            )
            if msg.startswith("40"):        # 기본 네임스페이스 접속 (auth 페이로드는 무시)
                await ws.send_text("40" + json.dumps({"sid": secrets.token_urlsafe(16)}))
            elif msg in ("1", "41"):        # 앱이 닫음
                break
            # "3"(pong)과 그 밖의 메시지는 받기만 한다
    except (WebSocketDisconnect, asyncio.TimeoutError):
        pass
    finally:
        pinger.cancel()
    try:
        await ws.close()
    except RuntimeError:
        pass  # 이미 닫힌 연결
