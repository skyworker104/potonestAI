/* 어시스턴트 의미 분석 검증 — node src/lib/assistant.test.js */
const { analyze } = require("./assistant");

const CUR = { connected: true, serverUrl: "http://192.168.45.232:8765" };

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
  ["리모콘", "remote_speak", { connected: true }],
  ["리모컨으로 말할래", "remote_speak", { connected: true }],
  ["말로 조종하고 싶어", "remote_speak", { connected: true }],
  ["리모콘 켜줘", "remote_speak", { connected: false }],            // 미연결 → 연결 유도
  ["설정", "settings", { connected: true }],
  ["앱 버전 알려줘", "settings", { connected: true }],
  ["와이파이 자동백업 설정", "auto_on", { connected: true }],       // '설정'이 자동백업을 가로채면 안 됨
  ["폴더 설정 바꿀래", "pick_albums", { connected: true }],         // '설정'이 폴더를 가로채면 안 됨
  ["도움말", "help", {}],
  ["뭐 할 수 있어?", "help", {}],
  ["고마워", "thanks", {}],
  ["오늘 날씨 어때", "unknown", {}],
  // 서버 IP 변경 — 연결된 상태에서도 대화로 바꿀 수 있어야 한다
  ["서버IP 변경해줘", "change_server", CUR],
  ["서버 아이피 바꿔줘", "change_server", CUR],
  ["서버 주소가 바뀌었어", "change_server", CUR],
  ["다른 서버로 연결할래", "change_server", CUR],
  ["서버 연결해줘", "change_server", CUR],                        // 이미 연결됨 → 변경으로
  ["QR 찍을게", "change_server", CUR],
  ["192.168.45.233", "change_server", CUR],                       // 연결 중 새 주소 → 변경
  ["192.168.45.232", "connect", CUR],                             // 같은 주소면 재연결
  ["233", "change_server", { ...CUR, awaiting: "server_ip" }],     // 끝자리만
  ["192 점 168 점 0 점 9", "change_server", { ...CUR, awaiting: "server_ip" }],
  ["999.1.1.1", "change_server", { ...CUR, awaiting: "server_ip" }], // 잘못된 주소 → 다시 묻기
  ["QR로 할게", "change_server", { ...CUR, awaiting: "server_ip" }],
  ["취소", "change_server_cancel", { ...CUR, awaiting: "server_ip" }],
  ["백업 시작", "backup_now", { ...CUR, awaiting: "server_ip" }],  // 딴 얘기면 평소대로
  ["최근 30장만", "set_scope", { ...CUR, awaiting: "server_ip" }],
  ["233", "unknown", CUR],                                        // 묻지 않았으면 끝자리로 안 봄
  ["백업 폴더 바꿔줘", "pick_albums", CUR],                        // '바꿔'가 서버 변경으로 새면 안 됨
  ["자동백업 설정 변경", "auto_on", CUR],
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
// 서버 변경 슬롯
const checks = [
  [analyze("233", { ...CUR, awaiting: "server_ip" }).slots.serverUrl, "http://192.168.45.233:8765"],
  [analyze("192 점 168 점 0 점 9", { ...CUR, awaiting: "server_ip" }).slots.serverUrl, "http://192.168.0.9:8765"],
  [analyze("서버 IP 변경해줘", CUR).awaiting, "server_ip"],
  [analyze("QR 찍을게", CUR).action.type, "open_qr_scanner"],
  [analyze("http://192.168.0.7:8765/upload 로 연결", {}).slots.serverUrl, "http://192.168.0.7:8765"],
];
let slotOk = 0;
for (const [got, want] of checks) {
  const ok = got === want;
  slotOk += ok ? 1 : 0;
  console.log(`${ok ? "✓" : "✗"} 슬롯 ${got}${ok ? "" : ` (기대 ${want})`}`);
}
if (slotOk !== checks.length) pass -= 1;

process.exit(pass === cases.length ? 0 : 1);
