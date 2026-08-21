#!/data/data/com.termux/files/usr/bin/bash
# PhotoNest 재시작 — 코드를 받은 뒤 새 코드로 서버를 다시 띄운다.
#
#   cd ~/photonest && git pull && bash scripts/restart.sh
#
# Termux에서 실행하세요(proot 안이 아니라). 서버는 Termux의 tmux 세션에서 돕니다.
#
# 시작 명령에는 배포판 이름과 바인드 마운트 경로가 들어가는데, 그건 설치할 때
# ~/run-photonest.sh에 구워졌다. 여기서 다시 적으면 둘이 어긋나므로 그대로 쓴다.
set -e

SESSION=photonest
PORT="${PORT:-8765}"
RUN="$HOME/run-photonest.sh"
WAIT_SECONDS="${WAIT_SECONDS:-90}"  # 느린 기기에서 기동이 더 걸리면 늘리세요

alive() { curl -s -o /dev/null --max-time 2 "http://localhost:$PORT/"; }

if ! command -v tmux >/dev/null 2>&1; then
  echo "❌ tmux가 없습니다 — proot 안이 아니라 Termux에서 실행하세요."
  exit 1
fi
if [ ! -f "$RUN" ]; then
  echo "❌ $RUN 이 없습니다 — 설치를 먼저 끝내세요:"
  echo "   bash scripts/install-termux.sh"
  exit 1
fi

if tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "→ 실행 중인 서버를 멈춥니다"
  tmux kill-session -t "$SESSION"
  # 포트를 놓을 때까지 기다린다 — 곧바로 띄우면 'address already in use'로 죽는다
  for _ in $(seq 40); do
    alive || break
    sleep 0.5
  done
else
  echo "→ 실행 중인 서버가 없습니다"
fi

echo "→ 서버를 시작합니다"
bash "$RUN"

# 색인·모델 로딩 때문에 첫 응답까지 시간이 걸릴 수 있다
printf "→ 기동 확인 중"
for _ in $(seq "$WAIT_SECONDS"); do
  if alive; then
    echo
    echo "✅ 재시작 완료 — http://localhost:$PORT"
    echo "   '폰 연결' 탭을 열면 새 앱(APK)이 있는지 확인해 자동으로 받아옵니다."
    exit 0
  fi
  printf "."
  sleep 1
done

echo
echo "⚠️  아직 응답이 없습니다. 계속 뜨는 중일 수도 있으니 로그를 보세요:"
echo "   tmux attach -t $SESSION      (빠져나오기: Ctrl+b 누른 뒤 d)"
exit 1
