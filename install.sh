#!/usr/bin/env bash
# PhotoNest AI 설치 스크립트 — macOS / Linux / 라즈베리파이 / proot Ubuntu(태블릿)
#
# AI 검색 방식:
#   full  torch + SigLIP2 (다국어, ~2GB) — PC/고성능 기기
#   lite  onnxruntime + CLIP (~50MB 패키지 + 최초 실행 시 모델 ~150MB) — 저사양/ARM
#   none  메타데이터/OCR/얼굴 검색만
# 미지정 시 기기 사양(아키텍처·RAM)을 보고 자동 제안한다.
#
# 사용: ./install.sh [--full | --lite | --no-ai]
set -e
cd "$(dirname "$0")"

echo "📷 PhotoNest AI 설치를 시작합니다"
echo

# Python 확인 (3.9+)
PY=""
for cand in python3.12 python3.11 python3.10 python3.9 python3; do
  if command -v "$cand" >/dev/null 2>&1; then
    ver=$("$cand" -c 'import sys; print(sys.version_info[0]*100+sys.version_info[1])')
    if [ "$ver" -ge 309 ]; then PY="$cand"; break; fi
  fi
done
if [ -z "$PY" ]; then
  echo "❌ Python 3.9 이상이 필요합니다. https://www.python.org/downloads/ 에서 설치 후 다시 실행하세요."
  exit 1
fi
echo "✅ Python: $($PY --version)"

# ---- AI 방식 결정 ----
ARCH=$(uname -m)
if [ -r /proc/meminfo ]; then                       # Linux / proot
  RAM_MB=$(awk '/MemTotal/{print int($2/1024)}' /proc/meminfo)
else                                                 # macOS
  RAM_MB=$(( $(sysctl -n hw.memsize 2>/dev/null || echo 0) / 1048576 ))
fi
RAM_MB=${RAM_MB:-0}

AI="full"
case "$1" in
  --no-ai) AI="none" ;;
  --lite)  AI="lite" ;;
  --full)  AI="full" ;;
  *)
    if [ "$ARCH" = "armv7l" ] || [ "$ARCH" = "armv6l" ]; then
      echo "⚠️  32비트 ARM 감지 — AI 검색 없이 설치합니다."
      AI="none"
    else
      # 저사양(ARM이거나 RAM<6GB)이면 경량을 기본 제안
      SUGGEST="1"
      if [ "$ARCH" = "aarch64" ] || { [ "$RAM_MB" -gt 0 ] && [ "$RAM_MB" -lt 6000 ]; }; then
        SUGGEST="2"
      fi
      echo "AI 자연어 검색 설치 방식을 고르세요:"
      echo "  1) 전체 — torch + SigLIP2 다국어 (PC/고성능, 다운로드 ~2GB)"
      echo "  2) 경량 — ONNX CLIP (저사양/태블릿/라즈베리파이, ~200MB. 한국어 질의는 자동 번역)"
      echo "  3) 없음 — 메타데이터/OCR/얼굴 검색만"
      printf "선택 [%s]: " "$SUGGEST"
      read -r ans
      ans=${ans:-$SUGGEST}
      case "$ans" in
        2) AI="lite" ;;
        3) AI="none" ;;
        *) AI="full" ;;
      esac
    fi
    ;;
esac
echo "→ AI 검색: $AI  (아키텍처 $ARCH, RAM ${RAM_MB}MB)"

# ---- venv + 의존성 ----
[ -d .venv ] || "$PY" -m venv .venv
.venv/bin/pip install --upgrade pip -q
echo "📦 기본 패키지 설치 중…"
.venv/bin/pip install -q -r backend/requirements-base.txt
if [ "$AI" = "full" ]; then
  echo "🧠 AI 패키지(전체) 설치 중… (수 분 소요)"
  .venv/bin/pip install -q -r backend/requirements-ai.txt
elif [ "$AI" = "lite" ]; then
  echo "🧠 AI 패키지(경량 ONNX) 설치 중…"
  .venv/bin/pip install -q -r backend/requirements-ai-onnx.txt
fi

# ---- 실행 스크립트 생성 ----
AI_SEARCH=1
EMBED_BACKEND=""
case "$AI" in
  none) AI_SEARCH=0 ;;
  full) EMBED_BACKEND="siglip" ;;
  lite) EMBED_BACKEND="clip-onnx" ;;
esac

cat > run.sh <<EOF
#!/usr/bin/env bash
cd "\$(dirname "\$0")"
export AI_SEARCH=$AI_SEARCH
${EMBED_BACKEND:+export EMBED_BACKEND=$EMBED_BACKEND}
# 사진 폴더를 바꾸려면: export PHOTOS_DIR=/path/to/photos
# 같은 와이파이의 다른 기기(태블릿·폰)에서 접속하려면: HOST=0.0.0.0 ./run.sh
#   (LAN 전체에 노출되므로 신뢰하는 네트워크에서만)
# 대화형 검색 엔진(OpenRouter/로컬 LLM/Claude)은 앱 안 ⚙️ 설정에서 선택.
# 신규 사진 자동 캡션을 쓰려면 비전 모델 지정: export VISION_LLM_MODEL=qwen/qwen2.5-vl-7b
echo "📷 PhotoNest AI — http://localhost:8765 를 브라우저(Chrome 권장)로 여세요"
exec .venv/bin/uvicorn backend.main:app --host "\${HOST:-127.0.0.1}" --port 8765
EOF
chmod +x run.sh

mkdir -p photos
echo
echo "✅ 설치 완료!"
echo "   1) 사진/동영상을 $(pwd)/photos 폴더에 넣으세요"
echo "   2) ./run.sh 로 실행하세요"
echo "   3) 브라우저에서 http://localhost:8765 접속"
echo "      (다른 기기에서 접속하려면 HOST=0.0.0.0 ./run.sh 후 http://<이 컴퓨터 IP>:8765)"
