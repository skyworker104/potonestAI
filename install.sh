#!/usr/bin/env bash
# PhotoNest AI 설치 스크립트 — macOS / Linux / Raspberry Pi
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

# AI 검색 여부 (저사양 기기는 끄기 권장)
AI=1
ARCH=$(uname -m)
if [ "$1" = "--no-ai" ]; then
  AI=0
elif [ "$ARCH" = "armv7l" ] || [ "$ARCH" = "armv6l" ]; then
  echo "⚠️  32비트 ARM(라즈베리파이 구형) 감지 — AI 검색을 끄고 설치합니다."
  AI=0
else
  printf "AI 자연어 검색을 설치할까요? 약 2GB 다운로드, 저사양 기기는 n 권장 [Y/n] "
  read -r ans
  case "$ans" in n|N|no|NO) AI=0 ;; esac
fi

# venv + 의존성
[ -d .venv ] || "$PY" -m venv .venv
.venv/bin/pip install --upgrade pip -q
echo "📦 기본 패키지 설치 중…"
.venv/bin/pip install -q -r backend/requirements-base.txt
if [ "$AI" = "1" ]; then
  echo "🧠 AI 패키지 설치 중… (수 분 소요)"
  .venv/bin/pip install -q -r backend/requirements-ai.txt
fi

# 실행 스크립트 생성
cat > run.sh <<EOF
#!/usr/bin/env bash
cd "\$(dirname "\$0")"
export AI_SEARCH=$AI
# 사진 폴더를 바꾸려면: export PHOTOS_DIR=/path/to/photos
# 대화형 검색에 LM Studio 등 로컬 LLM을 쓰는 경우, 추론(reasoning) 모델은
# 응답이 느려(수십~100초) 가벼운 instruct 모델을 권장한다 (예: qwen2.5-3b-instruct, 평균 ~4초).
# export LOCAL_LLM_MODEL=qwen2.5-3b-instruct
# 신규 사진 자동 캡션(상황·관계 질의 보강)을 쓰려면 비전 모델을 지정한다.
# 비용이 커(장당 수십초) 기존 라이브러리는 소급하지 않고 새로 추가되는 사진에만 적용된다.
# export VISION_LLM_MODEL=qwen/qwen2.5-vl-7b
echo "📷 PhotoNest AI — http://localhost:8765 를 브라우저(Chrome 권장)로 여세요"
exec .venv/bin/uvicorn backend.main:app --host 0.0.0.0 --port 8765
EOF
chmod +x run.sh

mkdir -p photos
echo
echo "✅ 설치 완료!"
echo "   1) 사진/동영상을 $(pwd)/photos 폴더에 넣으세요"
echo "   2) ./run.sh 로 실행하세요"
echo "   3) 브라우저에서 http://localhost:8765 접속 (같은 와이파이의 태블릿에서는 http://<이 컴퓨터 IP>:8765)"
