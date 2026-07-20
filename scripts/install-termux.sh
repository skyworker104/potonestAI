#!/data/data/com.termux/files/usr/bin/bash
# PhotoNest AI — 안드로이드 태블릿(Termux) 설치 스크립트
#
# 준비물: F-Droid판 Termux (Play스토어판은 구버전이라 비권장)
# 사용법:
#   1) Termux에서 프로젝트를 받는다 (예: git clone <저장소URL> photonest)
#   2) cd photonest && bash scripts/install-termux.sh
#
# 구조: Termux 네이티브는 파이썬 휠(onnxruntime/opencv)이 없어 빌드가 어렵다.
# 대신 proot-distro Ubuntu(가짜 루트, 루팅 불필요)를 깔고 그 안에서
# 표준 aarch64 휠로 설치한다. 프로젝트 폴더는 Termux 홈에 그대로 두고
# proot 세션에 바인드 마운트한다 (사진/SD카드도 함께).
set -e

if [ ! -f backend/main.py ]; then
  echo "❌ 프로젝트 루트에서 실행하세요: cd photonest && bash scripts/install-termux.sh"
  exit 1
fi
PROJ=$(pwd)
case "$PROJ" in *" "*) echo "❌ 프로젝트 경로에 공백이 있으면 안 됩니다: $PROJ"; exit 1;; esac

echo "📷 PhotoNest — Termux(proot Ubuntu) 설치"
echo

pkg update -y
pkg install -y proot-distro tmux termux-api >/dev/null || pkg install -y proot-distro tmux

# 공유 저장소 권한 (사진 폴더/SD카드 접근) — 팝업이 뜨면 허용
if [ ! -d "$HOME/storage" ]; then
  echo "→ 저장소 권한을 요청합니다 (팝업에서 허용을 누르세요)"
  termux-setup-storage
  sleep 3
fi

echo "→ Ubuntu(proot) 설치 중… (최초 1회, 수 분)"
proot-distro install ubuntu 2>/dev/null || echo "  ✓ Ubuntu 이미 설치됨"

# 바인드 마운트: 프로젝트 + 내부 공유저장소 + (있으면) SD카드
BINDS="--bind $PROJ:/opt/photonest --bind $HOME/storage/shared:/media/shared"
SD=""
for d in "$HOME"/storage/external-*; do
  [ -d "$d" ] && SD="$d" && break
done
[ -n "$SD" ] && BINDS="$BINDS --bind $SD:/media/sdcard"

echo "→ Ubuntu 안에 의존성 + PhotoNest(경량 AI) 설치 중…"
# 핵심 전략: numpy·opencv·pillow·psutil은 pip로 받으면 소스 컴파일(CMake)에 들어가
# 저사양 ARM에서 실패한다 → apt의 미리 빌드된 바이너리를 쓰고, venv는
# --system-site-packages로 만들어 그것들을 그대로 재사용한다. pip는 순수 파이썬과
# 안정적 aarch64 휠(onnxruntime 등)만 설치한다.
proot-distro login ubuntu $BINDS -- bash -c '
  set -e
  apt-get update -qq
  echo "→ 시스템 바이너리 패키지 설치 (컴파일 회피)…"
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
    python3-full python3-venv python3-pip \
    python3-numpy python3-opencv python3-pil python3-psutil \
    libglib2.0-0 libgl1 tesseract-ocr tesseract-ocr-kor
  echo "→ Python: $(python3 --version)"

  cd /opt/photonest
  [ -d .venv ] || python3 -m venv --system-site-packages .venv
  .venv/bin/pip install --upgrade pip -q

  echo "→ 순수 파이썬 의존성 설치…"
  .venv/bin/pip install -q -r backend/requirements-termux.txt || {
    # pillow-heif 휠 실패 시 그것만 빼고 재시도 (HEIC는 선택 기능)
    echo "⚠️  일부(HEIC 등) 설치 실패 — 필수 항목만 재시도"
    grep -v pillow-heif backend/requirements-termux.txt > /tmp/req-core.txt
    .venv/bin/pip install -q -r /tmp/req-core.txt
  }

  echo "→ 경량 AI(ONNX) 설치…"
  if .venv/bin/pip install -q onnxruntime tokenizers; then
    AI_BACKEND=clip-onnx
  else
    echo "⚠️  onnxruntime 휠이 이 Python 버전에 없어 AI 의미검색을 끄고 설치합니다."
    echo "   (날짜·장소·글자(OCR)·얼굴 검색은 그대로 동작)"
    AI_BACKEND=off
  fi

  # 시스템 opencv/numpy/pillow가 venv에서 보이는지 확인
  .venv/bin/python -c "import cv2, numpy, PIL; print(\"  ✓ cv2\", cv2.__version__, \"numpy\", numpy.__version__)"

  # run.sh 생성 (install.sh를 거치지 않으므로 직접)
  if [ "$AI_BACKEND" = "off" ]; then AI_SEARCH=0; EXPORT_BACKEND=""; \
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
  "proot-distro login ubuntu $BINDS -- bash -c 'cd /opt/photonest && HOST=0.0.0.0 PHOTOS_DIR=\\\${PHOTOS_DIR:-/opt/photonest/photos} ./run.sh'"
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
