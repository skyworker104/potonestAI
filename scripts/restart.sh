#!/data/data/com.termux/files/usr/bin/bash
# PhotoNest 재시작 — 코드를 받은 뒤 새 코드로 서버를 다시 띄운다.
#
#   cd ~/photonest && git pull && bash scripts/restart.sh
#
# Termux에서 실행하세요(proot 안이 아니라). 서버는 Termux의 tmux 세션에서 돕니다.
#
# 시작 명령에는 배포판 이름과 바인드 마운트 경로가 들어가는데, 그건 설치할 때
# ~/run-photonest.sh에 구워졌다. 여기서 다시 적으면 둘이 어긋나므로 그대로 쓴다.
#
# 왜 고아 프로세스까지 챙기는가:
#   tmux 세션을 죽여도 proot 안의 uvicorn은 고아로 살아남는 일이 흔하다. 그러면
#   새 서버는 포트를 못 잡아 죽고 옛 서버가 계속 응답하는데, "살아있는지"만 보고
#   판정하면 재시작 완료라고 거짓 보고를 한다 — 실제로 새 코드를 배포한 뒤 옛
#   코드가 계속 돌아 한참 헤맸다. 그래서 포트가 **실제로 비는 것**을 확인한 뒤에만
#   새 서버를 띄우고, 비우지 못하면 성공이라고 말하지 않는다.
set -e

SESSION=photonest
PORT="${PORT:-8765}"
RUN="${RUN:-$HOME/run-photonest.sh}"
WAIT_SECONDS="${WAIT_SECONDS:-90}"  # 기동(색인·모델 로딩) 대기 — 느린 기기면 늘리세요
STOP_SECONDS="${STOP_SECONDS:-20}"  # 포트가 풀릴 때까지의 단계별 대기
SERVER_MATCH="uvicorn backend.main:app"
PROJ=$(cd "$(dirname "$0")/.." && pwd)

# 응답이 오면 = 누군가 이 포트를 쥐고 있다 (HTTP 상태 코드는 보지 않는다)
alive() { curl -s -o /dev/null --max-time 2 "http://localhost:$PORT/"; }

wait_until_free() {
  _i=0
  while [ "$_i" -lt "$STOP_SECONDS" ]; do
    alive || return 0
    printf "."
    sleep 1
    _i=$((_i + 1))
  done
  return 1
}

# 남은 서버 프로세스를 신호로 정리한다.
# pkill(procps)이 없는 Termux도 있는데, 그때 ps 출력에서 PID를 추측해 죽이면
# 형식이 조금만 달라도 엉뚱한 프로세스를 잡는다 — 못 하면 못 한다고 알린다.
kill_orphans() {
  if command -v pkill >/dev/null 2>&1; then
    pkill "-$1" -f "$SERVER_MATCH" 2>/dev/null || true
    return 0
  fi
  return 1
}

if ! command -v tmux >/dev/null 2>&1; then
  echo "❌ tmux가 없습니다 — proot 안이 아니라 Termux에서 실행하세요."
  exit 1
fi
if [ ! -f "$RUN" ]; then
  echo "❌ $RUN 이 없습니다 — 설치를 먼저 끝내세요:"
  echo "   bash scripts/install-termux.sh"
  exit 1
fi

# 설치할 때 구워진 bind 경로가 지금 폴더와 다르면, 여기서 git pull한 코드는
# 서버에 전혀 반영되지 않는다. 조용히 넘어가면 아무도 눈치채지 못한다.
SERVED=$(grep -o -- "--bind [^ ]*:/opt/photonest" "$RUN" 2>/dev/null | head -1 || true)
SERVED=${SERVED#--bind }
SERVED=${SERVED%:/opt/photonest}
MISMATCH=""
if [ -n "$SERVED" ] && [ "$SERVED" != "$PROJ" ]; then
  MISMATCH=1
  echo "⚠️  서버가 띄우는 폴더가 이 폴더와 다릅니다"
  echo "     서버가 띄움: $SERVED"
  echo "     지금 이 폴더: $PROJ"
  echo "   → 여기서 git pull한 코드는 서버에 반영되지 않습니다. 맞추려면:"
  echo "     sed -i 's#--bind [^ ]*:/opt/photonest#--bind $PROJ:/opt/photonest#' $RUN"
  echo
fi

if tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "→ 실행 중인 서버를 멈춥니다"
  tmux kill-session -t "$SESSION" 2>/dev/null || true
fi

# tmux 세션이 없어도 고아가 포트를 쥐고 있을 수 있으므로 항상 확인한다
if alive; then
  printf "→ 포트 %s가 풀리기를 기다립니다" "$PORT"
  if ! wait_until_free; then
    echo
    echo "→ 아직 쥐고 있습니다 — 남은 서버 프로세스를 정리합니다"
    if ! kill_orphans TERM; then
      echo "❌ pkill이 없어 자동 정리를 못 합니다:"
      echo "   pkg install procps        # 설치 후 이 스크립트를 다시 실행하세요"
      exit 1
    fi
    printf "   "
    if ! wait_until_free; then
      echo
      echo "→ 종료 신호를 무시합니다 — 강제 종료합니다"
      kill_orphans KILL || true
      printf "   "
      if ! wait_until_free; then
        echo
        echo "❌ 포트 ${PORT}를 여전히 누군가 쥐고 있습니다."
        echo "   이대로 새 서버를 띄우면 포트 충돌로 죽고 옛 서버가 계속 응답해서,"
        echo "   재시작이 된 것처럼 보이지만 옛 코드가 돕니다. 그래서 여기서 멈춥니다."
        echo "   확인: pgrep -af '$SERVER_MATCH'"
        exit 1
      fi
    fi
  fi
  echo
  echo "   ✓ 포트가 비었습니다"
else
  echo "→ 실행 중인 서버가 없습니다"
fi

echo "→ 서버를 시작합니다"
bash "$RUN"

# 색인·모델 로딩 때문에 첫 응답까지 시간이 걸릴 수 있다
printf "→ 기동 확인 중"
_i=0
while [ "$_i" -lt "$WAIT_SECONDS" ]; do
  if alive; then
    # 포트가 비었던 것을 확인하고 띄웠으므로, 지금 응답하는 것은 새 프로세스다
    echo
    echo "✅ 재시작 완료 — http://localhost:$PORT"
    if [ -n "$MISMATCH" ]; then
      echo "   ⚠️  단, 서버가 띄운 폴더는 $SERVED 입니다 — 위 안내대로 맞추세요"
    else
      COMMIT=$(git -C "$PROJ" log --oneline -1 2>/dev/null || true)
      [ -n "$COMMIT" ] && echo "   코드: $COMMIT"
    fi
    echo "   '폰 연결' 탭을 열면 새 앱(APK)이 있는지 확인해 자동으로 받아옵니다."
    exit 0
  fi
  printf "."
  sleep 1
  _i=$((_i + 1))
done

echo
echo "⚠️  아직 응답이 없습니다. 계속 뜨는 중일 수도 있으니 로그를 보세요:"
echo "   tmux attach -t $SESSION      (빠져나오기: Ctrl+b 누른 뒤 d)"
echo "   포트 충돌로 죽었을 수도 있습니다: pgrep -af '$SERVER_MATCH'"
exit 1
