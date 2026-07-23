/**
 * 대화형 어시스턴트 의미 분석 엔진 (오프라인, AI 서버 불필요).
 *
 * 사용자의 자연어를 의도(intent)+슬롯(slot)으로 분석해 응답과 동작을 만든다.
 * 동의어·패턴 기반이라 다양한 표현을 흡수한다. 실제 LLM 연동 없이도
 * "예상되는 사용자 요청 문장"을 의미적으로 처리한다.
 *
 * analyze(text, ctx) → { intent, slots, reply, action }
 *   action: 앱이 실행할 명령 { type, ...payload } 또는 null
 *   ctx:    현재 상태 { connected, serverUrl, autoBackup, backing, wifi, scope }
 */

const SYNONYMS = {
  // 의도별 트리거 표현 (부분 문자열 매칭)
  connect: ["서버", "연결", "접속", "주소", "아이피", "ip", "qr", "큐알", "코드", "스캔", "등록"],
  albums: ["폴더", "앨범", "디렉토리", "어느폴더", "어디서", "어디를"],
  album_action: ["선택", "고르", "정하", "바꾸", "변경", "지정", "설정", "관리"],
  backup_now: ["백업", "올려", "업로드", "전송", "보내", "시작", "지금", "올리기"],
  auto: ["자동", "알아서", "자동으로", "자동백업"],
  off_signal: ["꺼", "끄", "off", "수동", "해제", "중지", "그만"],
  wifi: ["와이파이", "wifi", "wi-fi", "무선", "집에서", "데이터아낄"],
  scope_recent: ["최근", "새로", "오늘", "어제", "이번주", "방금"],
  scope_all: ["전체", "모두", "다", "전부", "처음부터"],
  videos: ["동영상", "영상", "비디오"],
  photos_only: ["사진만", "이미지만"],
  status: ["얼마나", "진행", "몇장", "몇 장", "상태", "현황", "어디까지", "다됐", "끝났"],
  view_words: ["보여", "보고", "볼래", "볼수", "볼 수", "보기", "찾아", "검색", "구경", "갤러리"],
  server_photos_subject: ["서버사진", "서버 사진", "서버에", "올린 사진", "올라간", "백업된", "백업한 사진", "갤러리", "사진"],
  pause: ["멈춰", "중지", "그만", "정지", "취소", "스톱", "stop", "일시정지"],
  help: ["도움", "도와", "뭐할", "뭐", "어떻게", "사용법", "기능", "help", "설명"],
  thanks: ["고마", "감사", "ㄱㅅ", "thanks"],
  greeting: ["안녕", "하이", "헬로", "hi", "hello", "반가"],
};

const IP_RE = /\b(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})(?::(\d{2,5}))?\b/;
const URL_RE = /https?:\/\/[^\s]+/i;
const N_RE = /(\d+)\s*(장|개)/;

function has(text, key) {
  return SYNONYMS[key].some((w) => text.includes(w));
}

function detectScope(text) {
  if (has(text, "videos")) return { media: "video" };
  if (has(text, "photos_only")) return { media: "image" };
  if (has(text, "scope_all")) return { range: "all" };
  if (has(text, "scope_recent")) {
    const m = text.match(N_RE);
    return { range: "recent", count: m ? parseInt(m[1], 10) : 50 };
  }
  return null;
}

function analyze(rawText, ctx = {}) {
  const text = (rawText || "").toLowerCase().replace(/\s+/g, " ").trim();
  const slots = {};

  // 1) 서버 주소가 문장에 있으면 우선 연결
  const url = text.match(URL_RE);
  const ip = text.match(IP_RE);
  if (url || ip) {
    let server;
    if (url) server = url[0];
    else {
      const port = ip[5] || "8765";
      server = `http://${ip[1]}.${ip[2]}.${ip[3]}.${ip[4]}:${port}`;
    }
    slots.serverUrl = server;
    return {
      intent: "connect",
      slots,
      reply: `좋아요! ${server} 서버에 연결해 볼게요. 잠시만요…`,
      action: { type: "connect", serverUrl: server },
    };
  }

  // 1.5) 백업할 폴더(앨범) 선택/변경
  if (has(text, "albums")) {
    return {
      intent: "pick_albums",
      slots,
      reply: "백업할 폴더(앨범)를 골라주세요. 여러 개 선택할 수 있고, 언제든 바꿀 수 있어요.",
      action: { type: "pick_albums" },
    };
  }

  // 1.7) 서버 사진 보기 — 폰 브라우저로 서버의 검색·타임라인 UI를 그대로 연다.
  //      connect("서버"), backup_now("올려")가 가로채지 않게 그보다 먼저 검사.
  if (has(text, "view_words") && has(text, "server_photos_subject")) {
    if (!ctx.connected) {
      return {
        intent: "server_photos",
        slots,
        reply: "서버 사진을 보려면 먼저 연결이 필요해요. PC 화면의 QR을 찍거나 서버 주소를 알려주세요.",
        action: { type: "open_qr_scanner" },
      };
    }
    return {
      intent: "server_photos",
      slots,
      reply: "서버에 백업된 사진을 열어드릴게요! 브라우저에서 PC와 똑같이 자연어 검색·타임라인·앨범을 쓸 수 있어요.",
      action: { type: "open_server_photos" },
    };
  }

  // 2) QR/연결 요청 (주소 없이)
  if (has(text, "connect") && !ctx.connected) {
    return {
      intent: "connect",
      slots,
      reply: "PC의 PhotoNest 화면에서 ‘폰 연결’ 탭을 열어 QR을 보여주세요. 아래 ‘QR 스캔’ 버튼을 누르거나, 서버 주소(예: 192.168.0.10:8765)를 입력해 주셔도 돼요.",
      action: { type: "open_qr_scanner" },
    };
  }

  // 3) 진행상황
  if (has(text, "status")) {
    return {
      intent: "status",
      slots,
      reply: ctx.backing
        ? `지금 백업 중이에요. ${ctx.done ?? 0}/${ctx.total ?? 0}장 올렸어요.`
        : ctx.connected
        ? "지금은 백업이 멈춰 있어요. ‘백업 시작’이라고 말씀해 주세요."
        : "먼저 서버에 연결해야 해요. 서버 주소를 알려주시겠어요?",
      action: { type: "report_status" },
    };
  }

  // 4) 중지
  if (has(text, "pause")) {
    return {
      intent: "pause",
      slots,
      reply: "백업을 멈출게요. 다시 시작하려면 ‘백업 시작’이라고 말씀해 주세요.",
      action: { type: "pause_backup" },
    };
  }

  // 5) 자동 백업 (자동/와이파이 언급 시) — 끄기 신호가 있으면 off
  if (has(text, "auto") || (has(text, "wifi") && !has(text, "backup_now"))) {
    if (has(text, "off_signal")) {
      return {
        intent: "auto_off",
        slots,
        reply: "자동 백업을 껐어요. 이제 직접 ‘백업 시작’이라고 하실 때만 올려요.",
        action: { type: "set_auto", value: false },
      };
    }
    const wifiOnly = has(text, "wifi");
    return {
      intent: "auto_on",
      slots: { wifiOnly },
      reply: wifiOnly
        ? "네! 지정한 와이파이에 연결될 때마다 새 사진을 자동으로 백업할게요. (휴대폰 설정에서 백그라운드 새로고침을 켜두면 더 잘 동작해요.)"
        : "자동 백업을 켰어요. 새 사진이 생기면 알아서 올려둘게요.",
      action: { type: "set_auto", value: true, wifiOnly },
    };
  }

  // 6) 백업 시작 (+ 범위 옵션)
  if (has(text, "backup_now")) {
    if (!ctx.connected) {
      return {
        intent: "backup_now",
        slots,
        reply: "백업하려면 먼저 서버 연결이 필요해요. 서버 주소나 QR을 알려주세요.",
        action: { type: "open_qr_scanner" },
      };
    }
    const scope = detectScope(text) || {};
    Object.assign(slots, scope);
    const desc = scope.media === "video" ? "동영상" : scope.range === "all" ? "전체 사진" : "새 사진";
    return {
      intent: "backup_now",
      slots,
      reply: `${desc}을(를) 원본 화질·위치정보 그대로 백업할게요. 시작합니다!`,
      action: { type: "start_backup", ...scope },
    };
  }

  // 7) 범위만 말한 경우 (예: "최근 사진만", "동영상도") — 사진/백업 맥락이 있을 때만
  const scopeOnly = detectScope(text);
  if (scopeOnly && /사진|영상|동영상|장|개|올려|백업|업로드/.test(text)) {
    Object.assign(slots, scopeOnly);
    return {
      intent: "set_scope",
      slots,
      reply: "알겠어요, 그 범위로 맞춰둘게요. ‘백업 시작’이라고 하면 그대로 올려요.",
      action: { type: "set_scope", ...scopeOnly },
    };
  }

  // 8) 도움말 / 인사 / 감사
  if (has(text, "help")) {
    return {
      intent: "help",
      slots,
      reply:
        "이렇게 말씀하시면 돼요:\n• “서버 연결해줘” 또는 주소 입력\n• “백업 시작” / “최근 사진만 올려줘”\n• “와이파이에서 자동으로 올려줘”\n• “서버 사진 보기” — 백업된 사진을 폰에서 검색·구경\n• “얼마나 했어?” / “멈춰”\n사진은 원본 그대로(위치정보 포함) 회원님 서버로만 전송돼요.",
      action: { type: "show_help" },
    };
  }
  if (has(text, "greeting")) {
    return {
      intent: "greeting",
      slots,
      reply: ctx.connected
        ? "안녕하세요! 백업을 도와드릴게요. ‘백업 시작’이라고 해보세요."
        : "안녕하세요! 사진 백업을 도와드릴 PhotoNest예요. 먼저 PC 화면의 QR을 찍거나 서버 주소를 알려주세요.",
      action: null,
    };
  }
  if (has(text, "thanks")) {
    return { intent: "thanks", slots, reply: "천만에요! 더 도와드릴 게 있으면 말씀해 주세요.", action: null };
  }

  // 9) 미해석 → 안내로 유도
  return {
    intent: "unknown",
    slots,
    reply: "음, 잘 이해하지 못했어요. ‘백업 시작’, ‘자동으로 올려줘’, ‘얼마나 했어?’처럼 말씀해 주시겠어요? (‘도움말’이라고 하면 사용법을 보여드려요.)",
    action: null,
  };
}

module.exports = { analyze, SYNONYMS };
