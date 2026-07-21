#!/data/data/com.termux/files/usr/bin/bash
# PhotoNest AI — 안드로이드 태블릿(Termux) 설치 스크립트
#
# 준비물: F-Droid판 Termux (Play스토어판은 구버전이라 비권장)
# 사용법:
#   1) Termux에서 프로젝트를 받는다 (예: git clone <저장소URL> photonest)
#   2) cd photonest && bash scripts/install-termux.sh
#      배포판 바꾸려면:  DISTRO=ubuntu bash scripts/install-termux.sh
#
# 왜 proot + Debian인가:
#   Termux 네이티브는 파이썬 휠(onnxruntime 등)이 없어 빌드가 어렵다.
#   → proot-distro(가짜 루트, 루팅 불필요) 안에서 표준 aarch64 휠로 설치한다.
#   기본 배포판은 Debian 12(Python 3.11) — Ubuntu 최신판은 Python 3.14 같은
#   갓 나온 버전이라 tokenizers/onnxruntime/jiter 등 Rust·C 확장의 휠이 아직
#   없어 소스 컴파일에 들어가 실패한다. Debian 12는 전 패키지 휠이 존재한다.
set -e

DISTRO="${DISTRO:-debian}"

if [ ! -f backend/main.py ]; then
  echo "❌ 프로젝트 루트에서 실행하세요: cd photonest && bash scripts/install-termux.sh"
  exit 1
fi
PROJ=$(pwd)
case "$PROJ" in *" "*) echo "❌ 프로젝트 경로에 공백이 있으면 안 됩니다: $PROJ"; exit 1;; esac

echo "📷 PhotoNest — Termux(proot $DISTRO) 설치"
echo

pkg update -y
pkg install -y proot-distro tmux termux-api curl >/dev/null || pkg install -y proot-distro tmux curl

# 공유 저장소 권한 (사진 폴더/SD카드 접근) — 팝업이 뜨면 허용
if [ ! -d "$HOME/storage" ]; then
  echo "→ 저장소 권한을 요청합니다 (팝업에서 허용을 누르세요)"
  termux-setup-storage
  sleep 3
fi

echo "→ $DISTRO(proot) 설치 중… (최초 1회, 수 분)"
proot-distro install "$DISTRO" 2>/dev/null || echo "  ✓ $DISTRO 이미 설치됨"

# 바인드 마운트: 프로젝트 + 내부 공유저장소 + (있으면) SD카드
BINDS="--bind $PROJ:/opt/photonest --bind $HOME/storage/shared:/media/shared"
SD=""
for d in "$HOME"/storage/external-*; do
  [ -d "$d" ] && SD="$d" && break
done
[ -n "$SD" ] && BINDS="$BINDS --bind $SD:/media/sdcard"

echo "→ $DISTRO 안에 의존성 + PhotoNest 설치 중…"
# 핵심 전략: 컴파일이 필요한 건 하나도 pip로 받지 않는다.
#  - numpy·opencv·pillow·psutil → apt 미리 빌드 바이너리(+venv --system-site-packages)
#  - 순수 파이썬(fastapi 등) → pip
#  - onnxruntime·tokenizers·pillow-heif → pip이되 --only-binary(휠만, 소스빌드 금지)
#    로 받아 휠이 없으면 즉시(느린 Rust 컴파일 없이) 실패하고 그 기능만 끈다.
proot-distro login "$DISTRO" $BINDS -- bash -c '
  set -e
  export DEBIAN_FRONTEND=noninteractive
  # 이전 설치가 중간에(SSH 끊김 등) 끊겼으면 dpkg가 불완전 상태 — 먼저 복구
  dpkg --configure -a 2>/dev/null || true
  apt-get install -f -y -qq 2>/dev/null || true
  apt-get update -qq
  echo "→ 시스템 바이너리 패키지 설치 (컴파일 회피)…"
  apt-get install -y -qq \
    python3-full python3-venv python3-pip \
    python3-numpy python3-opencv python3-pil python3-psutil \
    libgl1 libglib2.0-0 tesseract-ocr tesseract-ocr-kor
  echo "→ Python: $(python3 --version)"

  cd /opt/photonest
  # 이전에 Termux 네이티브로 만든 .venv가 남아 있으면 그 파이썬은 proot 안에서
  # 동작하지 않는다(경로가 /data/data/com.termux/... 를 가리킴). 그대로 재사용하면
  # pip이 네이티브에서 돌아 manylinux 휠을 못 쓰고 전부 소스 빌드→실패한다.
  # → venv 파이썬이 proot 안에서 실제로 실행되는지 검사하고, 아니면 다시 만든다.
  if [ -d .venv ] && ! .venv/bin/python -c "import sys" >/dev/null 2>&1; then
    echo "→ 호환되지 않는 이전 venv 감지 — 다시 만듭니다"
    rm -rf .venv
  fi
  [ -d .venv ] || python3 -m venv --system-site-packages .venv
  # venv가 proot(Debian) 파이썬으로 만들어졌는지 최종 확인
  .venv/bin/python -c "import sys; assert \"com.termux\" not in sys.executable, sys.executable"
  .venv/bin/pip install --upgrade pip -q

  echo "→ 순수 파이썬 의존성 설치…"
  .venv/bin/pip install -q -r backend/requirements-termux.txt

  # 시스템 opencv/numpy/pillow가 venv에서 보이는지 확인 (핵심 — 실패하면 중단)
  .venv/bin/python -c "import cv2, numpy, PIL; print(\"  ✓ cv2\", cv2.__version__, \"numpy\", numpy.__version__)"

  echo "→ 경량 AI(ONNX 의미검색) 설치 시도…"
  if .venv/bin/pip install -q --only-binary=:all: onnxruntime tokenizers; then
    AI_BACKEND=clip-onnx
    echo "  ✓ AI 의미검색 사용 가능"
  else
    AI_BACKEND=off
    echo "  ⚠️  이 Python($(python3 -V 2>&1))용 onnxruntime/tokenizers 휠이 없어"
    echo "     AI 의미검색을 끄고 설치합니다(날짜·장소·글자·얼굴 검색은 동작)."
    echo "     전체 기능을 원하면 Debian으로: proot-distro remove $DISTRO 후 재실행"
  fi

  # HEIC(아이폰 사진) 지원 — 휠 있으면만 (선택)
  .venv/bin/pip install -q --only-binary=:all: pillow-heif && echo "  ✓ HEIC 지원" \
    || echo "  · HEIC 미지원(선택 기능, 건너뜀)"

  # run.sh 생성 (install.sh를 거치지 않으므로 직접)
  if [ "$AI_BACKEND" = "off" ]; then AI_SEARCH=0; EXPORT_BACKEND="# (AI 의미검색 비활성 — 휠 없음)"; \
  else AI_SEARCH=1; EXPORT_BACKEND="export EMBED_BACKEND=clip-onnx"; fi
  cat > run.sh <<RUNEOF
#!/usr/bin/env bash
cd "\$(dirname "\$0")"
export AI_SEARCH=$AI_SEARCH
$EXPORT_BACKEND
# 사진 폴더: 내부 공유저장소 /media/shared, SD카드 /media/sdcard
# export PHOTOS_DIR=/media/sdcard/DCIM
echo "📷 PhotoNest AI — http://localhost:8765"
exec .venv/bin/uvicorn backend.main:app --host "\${HOST:-127.0.0.1}" --port 8765
RUNEOF
  chmod +x run.sh
  mkdir -p photos
  echo "✅ proot 내부 설치 완료 (AI: $AI_BACKEND)"
'

# ---- 폰 업로더 앱(APK) 자동 다운로드 ----
# APK는 data/(gitignore)에 있어 clone에 안 딸려온다 → EAS에서 내려받아
# 서버가 '폰 연결' 탭 QR로 배포하게 한다. 실패해도 설치는 계속(선택 기능).
APK_DST="$PROJ/data/app/photonest-uploader.apk"
if [ -f "$APK_DST" ]; then
  echo "→ 폰 업로더 앱(APK) 이미 있음 — 건너뜀"
elif [ -f "$PROJ/mobile/APK_URL.txt" ]; then
  APK_URL=$(tr -d ' \t\r\n' < "$PROJ/mobile/APK_URL.txt")
  if [ -n "$APK_URL" ]; then
    echo "→ 폰 업로더 앱(APK) 다운로드 중… (~80MB)"
    mkdir -p "$PROJ/data/app"
    if curl -fL --retry 2 -o "$APK_DST.part" "$APK_URL" && [ -s "$APK_DST.part" ]; then
      mv "$APK_DST.part" "$APK_DST"
      echo "  ✓ APK 준비 완료 — '폰 연결' 탭 QR로 폰에 설치하세요"
    else
      rm -f "$APK_DST.part"
      echo "  ⚠️  APK 다운로드 실패(선택 기능) — 나중에 '폰 연결' 탭 안내대로 받으면 됩니다"
    fi
  fi
fi

# ---- Termux 쪽 실행 스크립트 생성 ----
cat > "$HOME/run-photonest.sh" <<EOF
#!/data/data/com.termux/files/usr/bin/bash
# PhotoNest 서버 시작 — tmux 세션 'photonest'에서 실행
#   화면 보기: tmux attach -t photonest   (빠져나오기: Ctrl+b 후 d)
#   중지:      tmux kill-session -t photonest
# 사진 위치를 바꾸려면 PHOTOS_DIR을 수정하세요 (proot 안 경로 기준):
#   내부 공유저장소 = /media/shared , SD카드 = /media/sdcard
termux-wake-lock 2>/dev/null || true   # 화면 꺼져도 서버 유지
if tmux has-session -t photonest 2>/dev/null; then
  echo "이미 실행 중입니다 — tmux attach -t photonest"
  exit 0
fi
tmux new-session -d -s photonest \\
  "proot-distro login $DISTRO $BINDS -- bash -c 'cd /opt/photonest && HOST=0.0.0.0 PHOTOS_DIR=\\\${PHOTOS_DIR:-/opt/photonest/photos} ./run.sh'"
echo "✅ PhotoNest 시작됨 (tmux 세션 photonest)"
echo "   이 태블릿에서:   http://localhost:8765"
echo "   같은 와이파이:   http://<태블릿IP>:8765  (설정→와이파이→현재 네트워크에서 IP 확인)"
echo "   ※ LAN에 공개됩니다 — 신뢰하는 홈 네트워크에서만 사용하세요"
EOF
chmod +x "$HOME/run-photonest.sh"

echo
echo "✅ 설치 완료!"
echo "   시작:  ~/run-photonest.sh"
echo "   화면:  tmux attach -t photonest"
echo
echo "⚠️  안정 운용을 위해 한 번만 해두세요:"
echo "   1) 안드로이드 설정 → 배터리 → Termux → '제한 없음'으로"
echo "   2) 안드로이드 12+는 백그라운드 프로세스 킬러(phantom process killer)가"
echo "      서버를 죽일 수 있습니다. PC에서 adb로 1회 해제:"
echo "      adb shell settings put global settings_enable_monitor_phantom_procs false"
echo "   3) 대화형 검색: 앱 접속 후 ⚙️ 설정에서 OpenRouter 키를 넣으면"
echo "      한국어 발화 해석·번역이 클라우드로 처리됩니다 (사진은 전송 안 됨)"
