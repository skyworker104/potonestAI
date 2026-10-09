/** 서버 주소 해석 — QR 내용, 입력·받아쓰기한 말에서 "http://IP:포트"를 뽑는다.
 *
 * 서버 화면의 ‘폰 연결’ 탭에는 QR이 둘 있다. 전용 앱 QR은 http://IP:포트/download/app,
 * 1번 QR은 http://IP:포트/upload — 어느 쪽을 찍어도 경로를 떼면 서버 주소가 된다.
 * 네이티브 모듈을 부르지 않아 node로 바로 테스트한다.
 */

const DEFAULT_PORT = "8765";

function validOctets(parts) {
  return parts.every((p) => /^\d{1,3}$/.test(p) && Number(p) <= 255);
}

/** "http://host:port/anything" → "http://host:port". 주소가 아니면 null. */
function toServerBase(raw) {
  const m = String(raw || "").trim().match(/^(https?):\/\/([^\s/?#:]+)(?::(\d{2,5}))?/i);
  if (!m) return null;
  const host = m[2];
  if (/^[\d.]+$/.test(host) && !(host.split(".").length === 4 && validOctets(host.split(".")))) {
    return null;
  }
  return `${m[1].toLowerCase()}://${host}${m[3] ? `:${m[3]}` : ""}`;
}

/** QR 내용 → 서버 주소. PhotoNest QR이 아니면 null. */
function parseServerQr(data) {
  return toServerBase(data);
}

/** 받아쓰기 표현을 숫자 주소로 — "192 점 168 점 45 점 232" → "192.168.45.232". */
function normalizeSpoken(text) {
  return String(text || "")
    .replace(/\s*(?:점|쩜|닷|dot)\s*/gi, ".")
    .replace(/\s*콜론\s*/g, ":");
}

/**
 * 문장에서 서버 주소를 찾는다.
 *   "http://192.168.0.7:8765 로 연결" → 그대로(경로 제거)
 *   "192.168.45.232", "192.168.45.232:8000", "192 168 45 232 포트 8000"
 *   lastOnly 문맥(주소를 물어본 직후)에서 "233" / "233번" → 현재 주소의 끝자리만 교체
 * @param text   사용자가 한 말
 * @param opts   { current: 현재 서버 주소, lastOnly: 끝자리만 말해도 되는 문맥인지 }
 */
function extractServerUrl(text, opts = {}) {
  const t = normalizeSpoken(text);

  const url = t.match(/https?:\/\/[^\s]+/i);
  if (url) return toServerBase(url[0]);

  // 숫자 넷을 점·공백·쉼표로 이은 것 (받아쓰기는 점 대신 공백이 들어오기도 한다)
  const ip = t.match(/(?:^|[^\d])(\d{1,3})[.\s,]+(\d{1,3})[.\s,]+(\d{1,3})[.\s,]+(\d{1,3})(?![\d])/);
  if (ip) {
    const parts = ip.slice(1, 5);
    if (!validOctets(parts)) return null;
    const after = t.slice(ip.index + ip[0].length);
    const port = (after.match(/^\s*:\s*(\d{2,5})/) || t.match(/포트\s*(\d{2,5})/) || [])[1];
    return `http://${parts.join(".")}:${port || DEFAULT_PORT}`;
  }

  if (opts.lastOnly && opts.current) {
    const last = t.trim().match(/^(\d{1,3})\s*(?:번|번으로|으로|로|이야|야|요)?\s*[.!]?$/);
    const cur = String(opts.current).match(/^(https?:\/\/\d{1,3}\.\d{1,3}\.\d{1,3}\.)\d{1,3}(:\d+)?/);
    if (last && cur && Number(last[1]) <= 255) return `${cur[1]}${last[1]}${cur[2] || ""}`;
  }
  return null;
}

/** "http://192.168.45.232:8765" → "192.168.45.232:8765" (화면 표시용). */
function displayAddress(serverUrl) {
  return String(serverUrl || "").replace(/^https?:\/\//, "");
}

module.exports = { toServerBase, parseServerQr, extractServerUrl, displayAddress, DEFAULT_PORT };
