/* 폰 리모콘 수신 — 폰에서 말한 내용을 이 화면에서 실행한다.
   서버가 롱폴링으로 명령을 넘겨주면 handleUtterance()에 그대로 태운다.
   즉 마이크를 눌러 말한 것과 같은 경로라, 슬라이드쇼·앨범 같은 화면 조작도
   똑같이 동작한다. voice.js 다음에 로드돼야 한다(handleUtterance 사용). */
(function () {
  let seq = null;          // null = 아직 동기화 전
  let backoff = 1000;      // 서버가 죽었을 때 재연결 간격

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  async function tick() {
    // 최초 1회는 현재 위치만 받아온다 — 화면을 켜기 전에 쌓인 명령은 흘려보낸다
    const url = seq === null
      ? "/api/remote/poll"
      : `/api/remote/poll?after=${seq}&wait=25`;
    const data = await api.get(url);
    if (typeof data.seq === "number") seq = data.seq;
    for (const c of data.commands || []) {
      handleUtterance(c.text, "phone");
    }
  }

  async function loop() {
    for (;;) {
      try {
        await tick();
        backoff = 1000;
      } catch (_) {
        // 서버 재시작·와이파이 끊김 — 천천히 재시도(최대 15초 간격)
        await sleep(backoff);
        backoff = Math.min(backoff * 2, 15000);
      }
    }
  }

  loop();
})();
