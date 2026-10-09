/**
 * 대화형 어시스턴트 의미 분석 엔진 (오프라인, AI 서버 불필요).
 *
 * 사용자의 자연어를 의도(intent)+슬롯(slot)으로 분석해 응답과 동작을 만든다.
 * 동의어·패턴 기반이라 다양한 표현을 흡수한다. 실제 LLM 연동 없이도
 * "예상되는 사용자 요청 문장"을 의미적으로 처리한다.
 *
 * analyze(text, ctx) → { intent, slots, reply, action }
 *   action: 앱이 실행할 명령 { type, ...payload } 또는 null
 *   ctx:    현재 상태 { connected, serverUrl, autoBackup, backing, wifi, scope, awaiting }
 *           awaiting === "server_ip" — 직전에 새 서버 주소를 물어본 상태
 *   반환에 awaiting이 있으면 앱이 다음 말까지 그 문맥을 기억한다.
 */

const { extractServerUrl, displayAddress } = require("./serverAddress");

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
  remote: ["리모콘", "리모컨", "리모트", "말로 조종", "음성으로 조종", "말로 시켜"],
  settings: ["설정", "환경설정", "앱버전", "앱 버전", "버전", "정보"],
  // 서버 주소 변경 — 대상(서버/IP/주소)과 바꾸는 말이 함께 있어야 한다.
  server_subject: ["서버", "아이피", "ip", "주소", "연결", "접속"],
  server_change: ["변경", "바꾸", "바꿔", "바꿀", "바뀌", "바뀐", "수정", "재연결", "다시 연결", "다시연결", "다른 서버", "새 서버", "새로운 서버", "새 주소", "새 아이피", "새 ip"],
  qr_words: ["qr", "큐알", "스캔", "찍"],
  cancel: ["취소", "됐어", "그대로", "안 바꿔", "안바꿔", "그만", "아니"],
};

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

  const awaitingIp = ctx.awaiting === "server_ip";

  // 1) 서버 주소가 문장에 있으면 우선 연결 (주소를 물어본 직후엔 끝자리만 말해도 된다)
  const server = extractServerUrl(text, { current: ctx.serverUrl, lastOnly: awaitingIp });
  if (server) {
    slots.serverUrl = server;
    const changing = ctx.connected && ctx.serverUrl && ctx.serverUrl !== server;
    return {
      intent: changing ? "change_server" : "connect",
      slots,
      reply: changing
        ? `서버 주소를 ${displayAddress(ctx.serverUrl)} → ${displayAddress(server)}(으)로 바꿔 볼게요. 연결되는지 확인하는 중…`
        : `좋아요! ${server} 서버에 연결해 볼게요. 잠시만요…`,
      action: { type: "connect", serverUrl: server },
    };
  }

  // 1.1) 새 주소를 기다리는 중 — QR로 하겠다 / 취소 / 못 알아들음
  if (awaitingIp) {
    if (has(text, "qr_words")) {
      return {
        intent: "change_server",
        slots,
        reply: "카메라를 열게요. 서버 화면의 ‘폰 연결’ 탭에 있는 QR(전용 앱 QR이나 1번 QR)을 비춰주세요.",
        action: { type: "open_qr_scanner" },
      };
    }
    if (has(text, "cancel")) {
      return {
        intent: "change_server_cancel",
        slots,
        reply: ctx.serverUrl
          ? `알겠어요, 지금 주소(${displayAddress(ctx.serverUrl)})를 그대로 쓸게요.`
          : "알겠어요. 연결하고 싶을 때 ‘QR 스캔’을 누르거나 주소를 알려주세요.",
        action: null,
      };
    }
    // 숫자로만 된 말(주소를 말하려다 틀린 것)은 다시 묻는다. "최근 30장만" 같은 명령은 아래로.
    if (/\d/.test(text) && !text.replace(/[\d\s.,:]|점|쩜|포트|번|이야|야|요|으로|로/g, "")) {
      return {
        intent: "change_server",
        slots,
        reply: "주소를 알아듣지 못했어요. 192.168.45.232 처럼 숫자 네 개를 점으로 이어 말씀해 주세요. (포트가 8765가 아니면 192.168.45.232:8000 처럼 붙여 주세요.) ‘QR’이라고 하면 카메라를 열고, ‘취소’라고 하면 그대로 둘게요.",
        action: null,
        awaiting: "server_ip",
      };
    }
    // 그 밖의 말은 주소 변경을 그만두고 평소처럼 처리한다.
  }

  // 1.2) 서버 IP 변경 요청 — "서버 IP 변경해줘", "주소 바꿔줘", "다른 서버로 연결"
  if (has(text, "server_subject") && has(text, "server_change") && !has(text, "albums")) {
    return changeServerPrompt(text, ctx);
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
      reply: "서버에 백업된 사진을 열어드릴게요! PC와 똑같이 검색·타임라인·앨범을 쓸 수 있고, 위쪽 ‘‹ 대화창’으로 언제든 돌아오실 수 있어요.",
      action: { type: "open_server_photos" },
    };
  }

  // 1.8) 리모콘 말하기 — 폰에서 말하면 서버 화면이 그대로 실행한다.
  if (has(text, "remote")) {
    if (!ctx.connected) {
      return {
        intent: "remote_speak",
        slots,
        reply: "리모콘을 쓰려면 먼저 서버 연결이 필요해요. PC 화면의 QR을 찍어주세요.",
        action: { type: "open_qr_scanner" },
      };
    }
    return {
      intent: "remote_speak",
      slots,
      reply: "네! 마이크를 켤게요. 말씀하시면 서버 화면이 그대로 실행해요 — 예: “바닷가 사진 찾아줘”, “슬라이드쇼 시작”, “다음 사진”.",
      action: { type: "remote_speak" },
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

  // 7.5) 설정 화면 (앱 버전 확인 포함) — 더 구체적인 의도들 뒤에 둔다.
  //      "와이파이 자동백업 설정"처럼 '설정'이 곁들여진 말은 위에서 처리된다.
  if (has(text, "settings")) {
    return {
      intent: "settings",
      slots,
      reply: "설정 화면을 열게요. 앱 버전과 연결 상태, 백업할 폴더를 볼 수 있어요.",
      action: { type: "open_settings" },
    };
  }

  // 7.8) 이미 연결된 상태에서 "서버 연결해줘"/"QR 찍을게" — 주소를 바꾸려는 뜻으로 본다.
  if (ctx.connected && has(text, "connect")) {
    return changeServerPrompt(text, ctx);
  }

  // 8) 도움말 / 인사 / 감사
  if (has(text, "help")) {
    return {
      intent: "help",
      slots,
      reply:
        "이렇게 말씀하시면 돼요:\n• “서버 연결해줘” 또는 주소 입력\n• “서버 IP 변경해줘” — 새 주소를 말하거나 QR을 다시 찍어 바꿔요\n• “백업 시작” / “최근 사진만 올려줘”\n• “와이파이에서 자동으로 올려줘”\n• “서버 사진 보기” — 백업된 사진을 앱 안에서 검색·구경\n• “리모콘” — 폰에서 말하면 서버 화면이 그대로 실행해요\n• “설정” — 앱 버전·연결 상태 확인\n• “얼마나 했어?” / “멈춰”\n사진은 원본 그대로(위치정보 포함) 회원님 서버로만 전송돼요.",
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

/** 새 서버 주소를 묻는다. QR로 하겠다는 말이 있으면 바로 카메라를 연다. */
function changeServerPrompt(text, ctx) {
  const cur = ctx.serverUrl ? `지금은 ${displayAddress(ctx.serverUrl)}에 연결돼 있어요.\n` : "";
  if (has(text, "qr_words")) {
    return {
      intent: "change_server",
      slots: {},
      reply: `${cur}카메라를 열게요. 서버 화면의 ‘폰 연결’ 탭에 있는 QR(전용 앱 QR이나 1번 QR)을 비춰주세요.`,
      action: { type: "open_qr_scanner" },
    };
  }
  return {
    intent: "change_server",
    slots: {},
    reply:
      `${cur}새 서버 주소를 말씀해 주세요. 예: 192.168.45.232` +
      (ctx.serverUrl ? " — 끝자리만 바뀌었으면 ‘233’처럼 끝자리만 말해도 돼요." : "") +
      "\n또는 아래 ‘QR 스캔’을 눌러 서버 화면 ‘폰 연결’ 탭의 QR을 찍어도 바뀌어요. 그대로 두려면 ‘취소’라고 해주세요.",
    action: null,
    awaiting: "server_ip",
  };
}

module.exports = { analyze, SYNONYMS };
