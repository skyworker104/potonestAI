/** 리모콘 말하기 — 폰에서 말한 내용을 서버 화면의 명령으로 보낸다.
 *
 * 폰이 음성을 글자로 바꾸고, 그 글자를 서버로 넘긴다. 서버 화면은 그것을
 * 마이크로 직접 들은 것과 똑같이 처리한다 — 검색뿐 아니라 슬라이드쇼·앨범
 * 같은 화면 조작까지 동일하다.
 *
 * 음성 인식은 네이티브 모듈이라 Expo Go 같은 곳에는 없을 수 있다. 없으면 앱이
 * 죽는 대신 '이 빌드에서는 못 쓴다'고 알려주도록 import를 감싼다.
 */
let SR = null;
try {
  SR = require("expo-speech-recognition");
} catch (_) {
  SR = null;  // 이 빌드에 음성 인식이 없다
}

const Module = SR?.ExpoSpeechRecognitionModule || null;

export function isSpeechAvailable() {
  if (!Module) return false;
  try {
    return Module.isRecognitionAvailable();
  } catch (_) {
    return false;
  }
}

/** 마이크·음성인식 권한 요청. 거부되면 false. */
export async function requestSpeechPermission() {
  if (!Module) return false;
  try {
    const res = await Module.requestPermissionsAsync();
    return !!res?.granted;
  } catch (_) {
    return false;
  }
}

/** 듣기 시작. 결과는 useSpeechEvent("result")로 받는다. */
export function startListening(lang = "ko-KR") {
  if (!Module) throw new Error("이 빌드에는 음성 인식이 들어 있지 않아요.");
  Module.start({
    lang,
    interimResults: true,
    continuous: false,   // 리모콘 명령은 짧다 — 한 마디씩 끊어 듣는다
    maxAlternatives: 1,
  });
}

export function stopListening() {
  try {
    Module?.stop();
  } catch (_) { /* 이미 멈춰 있으면 무시 */ }
}

/** 인식 이벤트 구독. 모듈이 없으면 아무 일도 하지 않는 훅으로 대체된다.
 *  (모듈 유무는 앱 실행 내내 바뀌지 않으므로 훅 순서는 안정적이다) */
export const useSpeechEvent = SR?.useSpeechRecognitionEvent || function noop() {};

/** result 이벤트에서 가장 그럴듯한 문장을 뽑는다. */
export function transcriptOf(event) {
  return (event?.results?.[0]?.transcript || "").trim();
}

/**
 * 인식한 문장을 서버로 전달.
 * @returns {{ok: boolean, viewer?: boolean, error?: string}}
 *   viewer=false면 서버는 받았지만 볼 화면이 열려 있지 않다는 뜻.
 */
export async function sendRemote(serverUrl, text) {
  if (!serverUrl) return { ok: false, error: "서버에 연결되어 있지 않아요." };
  const body = (text || "").trim();
  if (!body) return { ok: false, error: "들은 내용이 없어요." };
  try {
    const r = await fetch(`${serverUrl}/api/remote/say`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: body }),
    });
    if (!r.ok) return { ok: false, error: `서버 응답 오류(${r.status})` };
    return await r.json();
  } catch (_) {
    return { ok: false, error: "서버에 닿지 않아요. 같은 와이파이인지 확인해 주세요." };
  }
}
