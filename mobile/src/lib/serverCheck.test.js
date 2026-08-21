/* 서버 화면 진입 판정 검증 — node src/lib/serverCheck.test.js */
const { diagnose } = require("./serverCheck");

const URL = "http://192.168.0.10:8765";
const ok = async () => ({ ok: true });
const fail = async () => { throw new Error("unreachable"); };

const cases = [
  {
    name: "서버가 응답하면 그냥 연다",
    args: { serverUrl: URL, netState: { isConnected: true, isWifi: true }, ping: ok },
    expect: null,
  },
  {
    name: "와이파이 여부를 몰라도 응답하면 연다",
    args: { serverUrl: URL, netState: null, ping: ok },
    expect: null,
  },
  {
    name: "서버 주소가 없으면 연결부터 안내",
    args: { serverUrl: null, netState: { isConnected: true, isWifi: true }, ping: ok },
    expect: "서버에 연결되어 있지 않아요",
  },
  {
    name: "네트워크가 꺼져 있으면 그것부터 안내",
    args: { serverUrl: URL, netState: { isConnected: false, isWifi: false }, ping: fail },
    expect: "네트워크가 꺼져 있어요",
  },
  {
    name: "데이터망이면 집 와이파이 안내",
    args: { serverUrl: URL, netState: { isConnected: true, isWifi: false }, ping: fail },
    expect: "집 와이파이가 아니에요",
  },
  {
    name: "같은 와이파이인데 안 되면 서버 점검 안내",
    args: { serverUrl: URL, netState: { isConnected: true, isWifi: true }, ping: fail },
    expect: "서버에 연결하지 못했어요",
  },
  {
    name: "와이파이 여부를 모르면 단정하지 않고 일반 안내",
    args: { serverUrl: URL, netState: { isConnected: true, isWifi: null }, ping: fail },
    expect: "서버에 연결하지 못했어요",
  },
];

(async () => {
  let pass = 0;
  for (const c of cases) {
    const r = await diagnose(c.args);
    const got = r === null ? null : r.title;
    const good = got === c.expect;
    pass += good ? 1 : 0;
    console.log(`${good ? "✓" : "✗"} ${c.name}${good ? "" : ` → ${got} (기대 ${c.expect})`}`);
  }

  // 안내문에 실제 주소가 들어가야 사용자가 확인할 수 있다
  const r = await diagnose({
    serverUrl: URL, netState: { isConnected: true, isWifi: true }, ping: fail,
  });
  const hasUrl = r.body.includes(URL);
  pass += hasUrl ? 1 : 0;
  console.log(`${hasUrl ? "✓" : "✗"} 안내문에 서버 주소가 들어간다`);

  const total = cases.length + 1;
  console.log(`\n${pass}/${total} 통과`);
  process.exit(pass === total ? 0 : 1);
})();
