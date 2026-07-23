/* 어시스턴트 의미 분석 검증 — node src/lib/assistant.test.js */
const { analyze } = require("./assistant");

const cases = [
  // [발화, 기대 intent, ctx]
  ["안녕", "greeting", {}],
  ["서버 연결해줘", "connect", {}],
  ["192.168.0.10:8765", "connect", {}],
  ["주소는 192.168.1.5 야", "connect", {}],
  ["http://192.168.0.7:8765 로 연결", "connect", {}],
  ["QR 찍을게", "connect", {}],
  ["폴더 선택", "pick_albums", { connected: true }],
  ["어느 앨범 백업할지 정할래", "pick_albums", { connected: true }],
  ["백업 폴더 바꿔줘", "pick_albums", { connected: true }],
  ["백업 시작", "backup_now", { connected: true }],
  ["사진 지금 올려줘", "backup_now", { connected: true }],
  ["최근 사진만 올려줘", "backup_now", { connected: true }],
  ["동영상도 백업해줘", "backup_now", { connected: true }],
  ["전체 다 업로드 해줘", "backup_now", { connected: true }],
  ["백업할래", "backup_now", { connected: false }], // 미연결 → 연결 유도
  ["와이파이에서 자동으로 올려줘", "auto_on", { connected: true }],
  ["알아서 백업해둬", "auto_on", { connected: true }],
  ["자동백업 꺼줘", "auto_off", { connected: true }],
  ["얼마나 했어?", "status", { connected: true, backing: true, done: 12, total: 40 }],
  ["몇 장 올라갔어", "status", { connected: true }],
  ["멈춰", "pause", { connected: true, backing: true }],
  ["그만해", "pause", {}],
  ["최근 30장만", "set_scope", { connected: true }],
  ["서버 사진 보기", "server_photos", { connected: true }],
  ["서버에 올린 사진 보여줘", "server_photos", { connected: true }], // '올려'가 백업으로 새면 안 됨
  ["백업된 사진 구경할래", "server_photos", { connected: true }],
  ["서버 사진 보고 싶어", "server_photos", { connected: false }],   // 미연결 → 연결 유도
  ["최근 사진만 올려줘", "backup_now", { connected: true }],        // 회귀: 백업이 가로채이면 안 됨
  ["도움말", "help", {}],
  ["뭐 할 수 있어?", "help", {}],
  ["고마워", "thanks", {}],
  ["오늘 날씨 어때", "unknown", {}],
];

let pass = 0;
for (const [text, expect, ctx] of cases) {
  const r = analyze(text, ctx);
  const ok = r.intent === expect;
  pass += ok ? 1 : 0;
  console.log(`${ok ? "✓" : "✗"} "${text}" → ${r.intent}${ok ? "" : ` (기대 ${expect})`}`);
  if (!ok) console.log(`    reply: ${r.reply}`);
}
console.log(`\n${pass}/${cases.length} 통과`);

// 슬롯 추출 확인
const s1 = analyze("192.168.0.10:8765 연결", {});
console.log("\n서버주소 추출:", s1.slots.serverUrl, "| action:", s1.action.type);
const s2 = analyze("최근 30장만 올려줘", { connected: true });
console.log("범위 추출:", JSON.stringify(s2.slots), "| action:", s2.action.type);
process.exit(pass === cases.length ? 0 : 1);
