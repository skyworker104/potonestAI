"""Immich 호환 계층 — Immich 공식 폰 앱(안드로이드/iOS)과 TV 앱이 PhotoNest에 붙게 한다.

## 왜 별도 경로인가
Immich API는 /api/albums, /api/trash/empty 처럼 PhotoNest 본체와 겹치는 경로를
쓴다. 같은 자리에 올리면 웹 화면이 깨지므로 이 계층은 전부 /immich/api 아래에
붙이고, 앱이 그 주소를 찾아오도록 /.well-known/immich을 내보낸다.
Immich 앱은 서버 주소를 받으면 먼저 그 문서를 보고 실제 API 주소를 정한다.

## 접속 방법
- 폰 앱(안드로이드/iOS): 서버 주소에 http://<서버IP>:8765 를 넣으면 된다
  (/.well-known/immich이 /immich/api 로 안내한다). 그 뒤 이메일/비밀번호 로그인.
- TV 앱 등 well-known을 안 보는 클라이언트: http://<서버IP>:8765/immich 을 넣는다
  (클라이언트가 뒤에 /api 를 붙인다). 인증은 x-api-key.

## 계정
scripts/immich_account.py 로 이메일·비밀번호를 정한다. 설정 전에는 로그인이
401이다 — PhotoNest 본체는 무인증이지만 이 계층을 무인증으로 열어두지 않는다.

## 아직 내보내지 않는 것
파트너 공유, 메모리, 스택, 태그, 공유링크, 그리고 인물/얼굴(동기화 스트림).
앱에서는 빈 화면으로 보인다. 인물은 TV 앱이 쓰는 REST(/people)로는 제공된다.
"""
from fastapi import APIRouter

from . import auth, library, routes, state

API_PREFIX = "/immich/api"

# Immich 앱이 호출하는 모든 경로 (인증은 각 엔드포인트가 처리)
router = APIRouter(prefix=API_PREFIX)
router.include_router(routes.router)
router.include_router(library.router)

# 접두사 없이 붙는 것들 — 앱의 서버 주소 탐색과 PhotoNest 화면용 현황
discovery = APIRouter()


@discovery.get("/.well-known/immich")
def well_known():
    """Immich 앱에 실제 API 주소를 알려준다 (앱이 로그인 전에 먼저 읽는다)."""
    return {"api": {"endpoint": API_PREFIX}}


@discovery.get("/api/immich/status")
def compat_status():
    """호환 계층 현황 — 계정 설정 여부와 체크섬 준비 상태 (비밀값 없음).

    체크섬이 다 차기 전에는 그만큼의 사진이 앱에 안 보인다. 그 진행률을
    PhotoNest 화면에서 확인할 수 있도록 내보낸다.
    """
    return dict(
        auth.public_status(),
        endpoint=API_PREFIX,
        report_version=routes.REPORT_VERSION,
        checksums=state.backfill_state(),
    )


def init():
    """서버 시작 시 호출 — 호환 테이블을 만든다.

    SHA-1 체크섬 계산은 계정이 설정돼 있을 때만 시작한다. 계정이 없으면 어떤
    앱도 붙을 수 없으니, 라이브러리 전체를 읽는 작업을 괜히 돌리지 않는다.
    (계정을 나중에 만들면 첫 인증 요청에서 시작된다 — auth.require_session)
    """
    state.init()
    if auth.configured():
        state.start_backfill()
