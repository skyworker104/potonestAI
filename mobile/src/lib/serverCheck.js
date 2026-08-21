/** 서버 화면을 열 수 있는지 판정.
 *
 * 서버는 집 와이파이 안에만 있어서, 밖에 나가 있거나 서버가 꺼져 있으면
 * 웹뷰가 흰 화면이 된다. 그 전에 무엇이 문제인지 가려내 사람이 읽을 안내를
 * 만든다. 네이티브 모듈을 직접 부르지 않고 받은 값으로만 판단해 테스트가 된다.
 *
 * @param serverUrl 저장된 서버 주소 (없을 수 있음)
 * @param netState  {isConnected, isWifi} — isWifi는 모르면 null
 * @param ping      서버 확인 함수 (실패 시 throw)
 * @returns null이면 열어도 된다. 아니면 {title, body}.
 */
async function diagnose({ serverUrl, netState, ping }) {
  if (!serverUrl) {
    return {
      title: "서버에 연결되어 있지 않아요",
      body: "대화창에서 ‘QR 스캔’을 눌러 PC 화면의 QR을 찍거나, 서버 주소를 입력해 주세요.",
    };
  }
  if (netState && netState.isConnected === false) {
    return {
      title: "네트워크가 꺼져 있어요",
      body: "와이파이를 켜고 집 네트워크에 연결한 뒤 다시 시도해 주세요.",
    };
  }
  try {
    await ping(serverUrl);
    return null;
  } catch (_) {
    // 와이파이가 아님이 확실할 때만 그렇게 말한다. 모르면(null) 일반 안내.
    if (netState && netState.isWifi === false) {
      return {
        title: "집 와이파이가 아니에요",
        body:
          "PhotoNest 서버는 집 네트워크 안에서만 열려 있어요. 모바일 데이터로는 연결되지 않습니다.\n" +
          "집 와이파이에 연결한 뒤 다시 눌러 주세요.",
      };
    }
    return {
      title: "서버에 연결하지 못했어요",
      body:
        `‘${serverUrl}’에 닿지 않아요. 확인해 주세요:\n` +
        "① 폰과 서버가 같은 와이파이인지\n" +
        "② 서버(태블릿·PC)에서 PhotoNest가 켜져 있는지\n" +
        "③ 주소가 바뀌지 않았는지 (공유기를 다시 켜면 IP가 바뀔 수 있어요)",
    };
  }
}

module.exports = { diagnose };
