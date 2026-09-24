#!/usr/bin/env python3
"""Immich 호환 계층의 계정 설정 — 이메일·비밀번호·API 키.

Immich 앱은 이메일/비밀번호 로그인을 전제로 만들어져 있어서, PhotoNest 본체와
달리 이 계층만은 계정이 필요하다. 설정 전에는 앱이 붙지 않는다.

    .venv/bin/python -m scripts.immich_account                # 대화형
    .venv/bin/python -m scripts.immich_account --email me@home.lan --password 비밀번호12
    .venv/bin/python -m scripts.immich_account --show         # 현재 설정 보기
    .venv/bin/python -m scripts.immich_account --rotate-key   # API 키만 새로 발급

Termux(안드로이드) 서버는 venv가 proot Debian 안에 있어 proot로 들어가야 한다:

    proot-distro login debian --bind ~/photonest:/opt/photonest -- \
      bash -c 'cd /opt/photonest && .venv/bin/python -m scripts.immich_account'

시스템 파이썬으로 부르면 venv로 자동 재실행하고, 그마저 안 되는 환경에서는
무엇을 해야 하는지 안내한다.

저장 위치: data/app/immich_auth.json (권한 0600). 비밀번호는 PBKDF2 해시로만
보관한다. API 키는 TV 앱처럼 로그인 화면이 없는 클라이언트가 x-api-key로 쓴다.
"""
import argparse
import getpass
import os
import secrets
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _deps_ready():
    try:
        import fastapi  # noqa: F401
        import PIL  # noqa: F401
    except ImportError:
        return False
    return True


def _venv_python():
    for rel in ("bin/python", "Scripts/python.exe"):
        candidate = ROOT / ".venv" / rel
        if candidate.exists():
            return candidate
    return None


def _explain_and_exit():
    """venv로 어떻게 실행하는지 알려준다 — 날것의 ImportError보다 낫다."""
    termux = "com.termux" in sys.prefix or "com.termux" in (os.environ.get("PREFIX") or "")
    out = sys.stderr
    print("필요한 패키지(fastapi/Pillow)를 찾을 수 없습니다 — "
          "시스템 파이썬이 아니라 프로젝트 venv로 실행해야 합니다.", file=out)
    print(file=out)
    if termux:
        # Termux의 .venv는 proot Debian 파이썬으로 만들어져 Termux 셸에서는
        # 실행조차 되지 않는다 (install-termux.sh가 그렇게 만든다).
        print("Termux에서는 venv가 proot(Debian) 안에 있어 Termux 셸에서 못 씁니다.",
              file=out)
        print("proot 안에서 실행하세요:", file=out)
        print(file=out)
        print("  proot-distro login debian --bind %s:/opt/photonest -- \\" % ROOT,
              file=out)
        print("    bash -c 'cd /opt/photonest && "
              ".venv/bin/python -m scripts.immich_account'", file=out)
    else:
        print("  cd %s && .venv/bin/python -m scripts.immich_account" % ROOT, file=out)
        print(file=out)
        print("(.venv이 없으면 먼저 ./install.sh 를 실행하세요)", file=out)
    sys.exit(1)


def _bootstrap():
    """의존성이 없으면 venv 파이썬으로 자기 자신을 다시 실행한다."""
    if _deps_ready():
        return
    if os.environ.get("PHOTONEST_REEXEC") != "1":
        venv_python = _venv_python()
        if venv_python is not None:
            os.environ["PHOTONEST_REEXEC"] = "1"  # 무한 재실행 방지
            try:
                os.execv(str(venv_python),
                         [str(venv_python), str(Path(__file__).resolve())]
                         + sys.argv[1:])
            except OSError:
                pass  # 이 셸에서 실행할 수 없는 venv (Termux ↔ proot 등)
    _explain_and_exit()


_bootstrap()

from backend import db  # noqa: E402
from backend.immich import auth, state  # noqa: E402


def _lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def _print_summary(acct):
    ip = _lan_ip()
    print()
    print("계정이 설정되었습니다.")
    print("  이메일   : %s" % acct["email"])
    print("  API 키   : %s" % acct["api_key"])
    print()
    print("Immich 폰 앱(안드로이드/iOS) — 서버 주소에 이것을 넣으세요:")
    print("  http://%s:8765" % ip)
    print("  그 뒤 위 이메일과 방금 정한 비밀번호로 로그인합니다.")
    print()
    print("TV 앱 등 로그인 화면이 없는 클라이언트 — 주소와 API 키:")
    print("  http://%s:8765/immich" % ip)
    print("  x-api-key: %s" % acct["api_key"])
    print()
    print("※ 서버를 HOST=0.0.0.0 으로 띄워야 다른 기기에서 접속됩니다.")


def main():
    ap = argparse.ArgumentParser(description="Immich 호환 계정 설정")
    ap.add_argument("--email")
    ap.add_argument("--password")
    ap.add_argument("--name", help="앱에 표시될 이름 (기본: 이메일 앞부분)")
    ap.add_argument("--show", action="store_true", help="현재 설정 출력")
    ap.add_argument("--rotate-key", action="store_true", help="API 키만 재발급")
    args = ap.parse_args()

    db.init()
    state.init()

    if args.show:
        acct = auth.account()
        if not acct.get("email"):
            print("아직 설정되지 않았습니다. 인자 없이 실행하면 대화형으로 설정합니다.")
            return 1
        print("이메일 : %s" % acct["email"])
        print("이름   : %s" % (acct.get("name") or ""))
        print("API 키 : %s" % (acct.get("api_key") or "(없음)"))
        print("주소   : http://%s:8765  (TV 앱은 .../immich)" % _lan_ip())
        return 0

    if args.rotate_key:
        acct = auth._read_file()
        if not acct.get("email"):
            print("먼저 계정을 설정하세요.", file=sys.stderr)
            return 1
        acct["api_key"] = secrets.token_urlsafe(32)
        import json
        import os
        with open(auth._FILE, "w", encoding="utf-8") as f:
            json.dump(acct, f, ensure_ascii=False, indent=2)
        os.chmod(auth._FILE, 0o600)
        print("새 API 키: %s" % acct["api_key"])
        return 0

    email = args.email or input("이메일 (아무 주소나 됩니다, 예: me@home.lan): ").strip()
    password = args.password
    if not password:
        password = getpass.getpass("비밀번호 (8자 이상): ")
        if password != getpass.getpass("비밀번호 확인: "):
            print("비밀번호가 일치하지 않습니다.", file=sys.stderr)
            return 1
    try:
        acct = auth.set_account(email, password, args.name)
    except ValueError as e:
        print("오류: %s" % e, file=sys.stderr)
        return 1
    _print_summary(acct)
    return 0


if __name__ == "__main__":
    sys.exit(main())
