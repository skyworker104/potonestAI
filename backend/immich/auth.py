"""Immich 호환 계층의 인증 — 단일 계정 + 세션 토큰 + API 키.

PhotoNest 본체는 무인증이다(같은 와이파이 안에서만 쓰는 전제). 하지만 Immich
앱은 이메일/비밀번호 로그인과 Bearer 토큰을 전제로 만들어져 있어서, 이 계층
안에서만 최소한의 계정을 둔다. TV 앱들은 로그인 화면 없이 x-api-key만 쓰므로
같은 계정의 API 키로도 통과시킨다.

계정은 scripts/immich_account.py로 설정한다. 설정 전에는 로그인이 401이며,
그 상태로는 Immich 앱이 붙지 않는다(무인증으로 열어두지 않는다).
환경변수 IMMICH_EMAIL / IMMICH_PASSWORD / IMMICH_API_KEY가 있으면 그것이 우선.
"""
import hashlib
import hmac
import json
import os
import secrets
from datetime import datetime

from fastapi import HTTPException, Request

from .. import db
from . import state

_FILE = db.DATA_DIR / "app" / "immich_auth.json"
_ITERATIONS = 200_000
API_KEY_SESSION = "api-key"


def _hash(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt), _ITERATIONS
    ).hex()


def _read_file():
    try:
        with open(_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def account():
    """현재 계정 정보. 환경변수가 파일보다 우선. 없으면 {}."""
    acct = _read_file()
    email = os.environ.get("IMMICH_EMAIL", "").strip()
    password = os.environ.get("IMMICH_PASSWORD", "")
    if email and password:
        salt = hashlib.sha256(email.encode("utf-8")).hexdigest()
        acct = {
            "email": email,
            "salt": salt,
            "hash": _hash(password, salt),
            "api_key": os.environ.get("IMMICH_API_KEY", "") or acct.get("api_key", ""),
            "name": acct.get("name") or email.split("@")[0],
        }
    env_key = os.environ.get("IMMICH_API_KEY", "").strip()
    if env_key and acct:
        acct["api_key"] = env_key
    return acct


def configured() -> bool:
    acct = account()
    return bool(acct.get("email") and acct.get("hash"))


def set_account(email: str, password: str, name=None):
    """계정을 파일에 저장하고 API 키를 (없으면) 새로 만든다. 저장 내용 반환."""
    email = (email or "").strip()
    if not email or "@" not in email:
        raise ValueError("email이 필요합니다 (Immich 앱이 이메일 형식을 요구함)")
    if not password or len(password) < 8:
        raise ValueError("비밀번호는 8자 이상이어야 합니다")
    existing = _read_file()
    salt = secrets.token_hex(16)
    data = {
        "email": email,
        "name": (name or "").strip() or email.split("@")[0],
        "salt": salt,
        "hash": _hash(password, salt),
        "api_key": existing.get("api_key") or secrets.token_urlsafe(32),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    _FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    try:
        os.chmod(_FILE, 0o600)  # 비밀번호 해시·API 키 → 소유자만 읽기
    except OSError:
        pass
    # 계정이 바뀌면 기존 세션은 무효
    with db.conn() as c:
        c.execute("DELETE FROM immich_sessions")
    return data


def verify(email: str, password: str) -> bool:
    acct = account()
    if not acct.get("hash"):
        return False
    if (email or "").strip().lower() != acct["email"].lower():
        return False
    return hmac.compare_digest(_hash(password, acct["salt"]), acct["hash"])


# ---------- 세션 ----------

def create_session(device: str) -> str:
    token = secrets.token_urlsafe(32)
    with db.conn() as c:
        c.execute(
            "INSERT INTO immich_sessions (token, device, created_at) VALUES (?, ?, ?)",
            (token, device or "unknown", datetime.now().isoformat(timespec="seconds")),
        )
    return token


def drop_session(token: str):
    with db.conn() as c:
        c.execute("DELETE FROM immich_sessions WHERE token=?", (token,))


def _session_id(token: str):
    with db.conn() as c:
        r = c.execute(
            "SELECT token FROM immich_sessions WHERE token=?", (token,)
        ).fetchone()
    return r["token"] if r else None


def identify(request: Request):
    """요청의 자격증명을 확인해 세션 식별자를 돌려준다. 실패하면 None.

    세션 식별자는 동기화 워터마크(ack)의 키로도 쓴다 — 기기마다 따로 진행된다.
    """
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        token = header[7:].strip()
        sid = _session_id(token)
        if sid:
            return sid
    api_key = request.headers.get("x-api-key", "").strip()
    if api_key:
        acct = account()
        configured_key = acct.get("api_key") or ""
        if configured_key and hmac.compare_digest(api_key, configured_key):
            return API_KEY_SESSION
    return None


def require_session(request: Request) -> str:
    """인증 필수 엔드포인트용 FastAPI 의존성. 세션 식별자를 돌려준다."""
    sid = identify(request)
    if sid is not None:
        # 앱이 실제로 붙었을 때만 체크섬 계산을 진행한다 (멱등)
        state.start_backfill()
        return sid
    if sid is None:
        raise HTTPException(
            status_code=401,
            detail="Invalid user or password" if configured()
            else "Immich 호환 계정이 아직 설정되지 않았습니다 "
                 "(scripts/immich_account.py 실행)",
        )


def public_status():
    """비밀 없는 설정 현황 — PhotoNest 화면에서 안내용으로 쓴다."""
    acct = account()
    return {
        "configured": configured(),
        "email": acct.get("email", ""),
        "api_key_set": bool(acct.get("api_key")),
        "user_id": state.USER_ID,
    }
