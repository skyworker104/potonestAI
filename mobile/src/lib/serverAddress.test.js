/* 서버 주소 해석 검증 — node src/lib/serverAddress.test.js */
const { parseServerQr, extractServerUrl, toServerBase } = require("./serverAddress");

const cur = "http://192.168.45.232:8765";
const cases = [
  // [설명, 실제, 기대]
  ["전용 앱 QR", parseServerQr("http://192.168.45.232:8765/download/app"), "http://192.168.45.232:8765"],
  ["1번 업로드 QR", parseServerQr("http://192.168.0.153:8765/upload"), "http://192.168.0.153:8765"],
  ["호스트 이름", parseServerQr("http://photonest.local:8765/upload"), "http://photonest.local:8765"],
  ["QR 아님", parseServerQr("WIFI:S:home;T:WPA;P:1234;;"), null],
  ["잘못된 IP", parseServerQr("http://999.1.1.1:8765"), null],
  ["포트 없는 URL", toServerBase("https://example.com/x"), "https://example.com"],
  ["IP만", extractServerUrl("192.168.45.233"), "http://192.168.45.233:8765"],
  ["IP:포트", extractServerUrl("192.168.45.233:8000 으로"), "http://192.168.45.233:8000"],
  ["조사 붙음", extractServerUrl("주소는 192.168.1.5야"), "http://192.168.1.5:8765"],
  ["받아쓰기 점", extractServerUrl("192 점 168 점 45 점 9"), "http://192.168.45.9:8765"],
  ["받아쓰기 공백 + 포트", extractServerUrl("192 168 45 9 포트 8000"), "http://192.168.45.9:8000"],
  ["범위 밖", extractServerUrl("300.1.1.1"), null],
  ["끝자리만 (문맥 있음)", extractServerUrl("233", { current: cur, lastOnly: true }), "http://192.168.45.233:8765"],
  ["끝자리 + 번", extractServerUrl("233번으로", { current: cur, lastOnly: true }), "http://192.168.45.233:8765"],
  ["끝자리만 (문맥 없음)", extractServerUrl("233", { current: cur }), null],
  ["숫자 섞인 명령", extractServerUrl("최근 30장만", { current: cur, lastOnly: true }), null],
];

let pass = 0;
for (const [name, got, want] of cases) {
  const ok = got === want;
  pass += ok ? 1 : 0;
  console.log(`${ok ? "✓" : "✗"} ${name} → ${got}${ok ? "" : ` (기대 ${want})`}`);
}
console.log(`\n${pass}/${cases.length} 통과`);
process.exit(pass === cases.length ? 0 : 1);
