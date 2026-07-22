/* PhotoNest AI — AI 대화(텍스트+음성) 및 음성 UI 명령 */

const chatLog = $("#chat-log");
let voiceMode = false;
let recognizing = false;
let speaking = false;

// 대화 맥락(후속 질문용) — 최근 턴만 유지
const chatHistory = [];
const MAX_HISTORY_TURNS = 6;

/* ---------------- 채팅 ---------------- */
function addMsg(text, who) {
  const div = document.createElement("div");
  div.className = `msg ${who}`;
  div.textContent = text;
  chatLog.appendChild(div);
  chatLog.scrollTop = chatLog.scrollHeight;
  return div;
}

const ENGINE_BADGE = {
  "skill": "⚡ 스킬", "local-llm": "🧠 로컬 LLM",
  "claude": "☁️ Claude", "heuristic": "🔤 규칙", "instant": "",
};

async function sendToAI(message) {
  const thinking = addMsg("생각 중…", "ai thinking");
  try {
    const data = await api.post("/api/chat", {
      message,
      history: chatHistory.slice(-MAX_HISTORY_TURNS),
    });
    thinking.remove();
    const aiMsg = addMsg(data.reply, "ai");
    // 처리 방식 배지 (스킬 즉시 처리/LLM 등)
    const badge = ENGINE_BADGE[data.engine];
    if (badge) {
      const b = document.createElement("span");
      b.className = "engine-badge";
      b.textContent = badge + (data.engine === "local-llm" ? " · 스킬로 저장됨" : "");
      aiMsg.appendChild(b);
    }
    chatHistory.push({ role: "user", content: message });
    chatHistory.push({ role: "assistant", content: data.reply });
    if (chatHistory.length > MAX_HISTORY_TURNS * 2) {
      chatHistory.splice(0, chatHistory.length - MAX_HISTORY_TURNS * 2);
    }
    if (data.intent === "search") {
      switchView("search", { items: data.results, query: message });
    }
    speak(data.reply);
  } catch (e) {
    thinking.remove();
    const msg = "서버와 통신할 수 없어요. 잠시 후 다시 시도해 주세요.";
    addMsg(msg, "ai");
    speak(msg);
  }
}

/* ---------------- 도움말 · 배운 스킬 ---------------- */
async function showHelp() {
  let data;
  try {
    data = await api.get("/api/help");
  } catch (_) {
    addMsg("도움말을 불러오지 못했어요.", "ai");
    return;
  }
  const wrap = document.createElement("div");
  wrap.className = "msg ai help-msg";

  const exHtml = data.examples.map(
    (q) => `<button class="chip ex-chip">${q}</button>`
  ).join("");

  const skillsHtml = (data.skills || []).length
    ? data.skills.map((s) =>
        `<span class="chip skill-chip" data-id="${s.id}" data-q="${s.examples[0]}">` +
        `⚡ ${s.label}${s.uses ? ` <small>${s.uses}회</small>` : ""}` +
        `<button class="chip-x" data-id="${s.id}" title="삭제">✕</button></span>`
      ).join("")
    : `<span class="muted">아직 배운 검색이 없어요. 자연어로 검색하면 자동으로 학습돼요.</span>`;

  const tipsHtml = data.tips.map((t) => `<li>${t}</li>`).join("");

  wrap.innerHTML =
    `<div class="help-title">💡 이렇게 찾아보세요 (눌러서 바로 검색)</div>` +
    `<div class="chip-row">${exHtml}</div>` +
    `<div class="help-title">⚡ 배운 검색 스킬 <small>(비슷한 질문은 즉시 처리)</small></div>` +
    `<div class="chip-row">${skillsHtml}</div>` +
    `<ul class="help-tips">${tipsHtml}</ul>`;

  chatLog.appendChild(wrap);
  chatLog.scrollTop = chatLog.scrollHeight;

  // 예시·스킬 칩 클릭 → 바로 검색
  wrap.querySelectorAll(".ex-chip").forEach((b) => {
    b.onclick = () => handleUtterance(b.textContent);
  });
  wrap.querySelectorAll(".skill-chip").forEach((c) => {
    c.onclick = (e) => {
      if (e.target.classList.contains("chip-x")) return;
      handleUtterance(c.dataset.q);
    };
  });
  wrap.querySelectorAll(".chip-x").forEach((x) => {
    x.onclick = async (e) => {
      e.stopPropagation();
      await api.del(`/api/skills/${x.dataset.id}`);
      x.closest(".skill-chip").remove();
    };
  });
}

$("#help-btn").onclick = showHelp;

/* ---------------- 음성 UI 명령 ---------------- */
const ORDINALS = {
  "첫": 1, "한": 1, "두": 2, "세": 3, "네": 4, "다섯": 5,
  "여섯": 6, "일곱": 7, "여덟": 8, "아홉": 9, "열": 10,
};

function tryUICommand(text) {
  const t = text.replace(/\s/g, "");

  // --- 도움말 ---
  if (/^(도움말|도움|help|뭐할수있|어떻게|사용법|예시)/.test(t)) {
    showHelp();
    speak("이렇게 찾을 수 있어요. 예시를 보여드릴게요.");
    return true;
  }

  // --- 슬라이드쇼 ---
  // 중지어에 "종료/끝/스톱"류 포함 — "슬라이드쇼 종료"가 시작으로 오인되던 버그
  const SS_STOP = /(멈춰|멈춤|중지|중단|꺼|끄|정지|그만|종료|끝내|끝|스톱|스탑|나가)/;
  if (/(슬라이드쇼|슬라이드)/.test(t)) {
    if (SS_STOP.test(t)) { stopSlideshow(); speak("슬라이드쇼를 멈췄어요."); }
    else { startSlideshow(Math.max(lbIndex, 0)); speak("슬라이드쇼를 시작할게요."); }
    return true;
  }
  if (!$("#slideshow").hidden && (SS_STOP.test(t) || /닫아/.test(t))) {
    stopSlideshow(); speak("슬라이드쇼를 멈췄어요."); return true;
  }

  // --- 라이트박스 제어 ---
  if (/(닫아|닫기|취소)/.test(t) && lbIndex >= 0) {
    closeLightbox(); speak("닫았어요."); return true;
  }
  if (/(다음)/.test(t) && lbIndex >= 0) { nextPhoto(); speak("다음 사진이에요."); return true; }
  if (/(이전|전사진|앞사진)/.test(t) && lbIndex >= 0) { prevPhoto(); speak("이전 사진이에요."); return true; }

  // --- 즐겨찾기 ---
  if (/즐겨찾기/.test(t)) {
    if (/(추가|해줘|등록)/.test(t) && lbIndex >= 0) {
      toggleFavoriteCurrent().then((on) =>
        speak(on ? "즐겨찾기에 추가했어요." : "즐겨찾기에서 해제했어요."));
      return true;
    }
    if (/(보여|열어|목록)/.test(t)) { switchView("favorites"); speak("즐겨찾기를 보여드릴게요."); return true; }
  }

  // --- 삭제/휴지통 ---
  if (lbIndex >= 0 && /(삭제|지워|휴지통에|버려)/.test(t)) {
    trashCurrent().then(() => speak("휴지통으로 옮겼어요."));
    return true;
  }
  if (/휴지통.*(보여|열어)/.test(t)) { switchView("trash"); speak("휴지통이에요."); return true; }

  // --- 앨범 ---
  const mkAlbum = text.match(/(.+?)\s*(이라는|라는)?\s*앨범\s*(을|를)?\s*만들/);
  if (mkAlbum) {
    const name = mkAlbum[1].replace(/['"“”]/g, "").trim();
    if (name) {
      api.post("/api/albums", { name }).then(() => {
        speak(`'${name}' 앨범을 만들었어요.`);
        if (state.view === "albums") switchView("albums");
      });
      return true;
    }
  }
  const addAlbum = text.match(/(.+?)\s*앨범에\s*(넣어|추가|저장)/);
  if (addAlbum && lbIndex >= 0) {
    const name = addAlbum[1].replace(/(이\s*사진\s*)/, "").trim();
    api.get("/api/albums").then(({ albums }) => {
      const found = albums.find((a) => a.name.includes(name));
      const it = currentLbItem();
      if (found && it) {
        api.post(`/api/albums/${found.id}/items`, { media_ids: [it.id] })
          .then(() => speak(`'${found.name}' 앨범에 추가했어요.`));
      } else {
        speak(`'${name}' 앨범을 찾지 못했어요. 먼저 앨범을 만들어 주세요.`);
      }
    });
    return true;
  }
  if (/앨범.*(보여|열어|목록)/.test(t)) { switchView("albums"); speak("앨범 목록이에요."); return true; }

  // --- 상세정보/위치/지도 ---
  if (lbIndex >= 0 && /(정보|상세)/.test(t)) {
    setLbInfo(!/(닫|꺼|숨)/.test(t));
    speak(lbInfoVisible ? "상세 정보를 보여드릴게요." : "정보를 닫았어요.");
    return true;
  }
  if (/(어디서찍|위치|장소).*(알려|보여|찍)/.test(t) || /(지도)/.test(t)) {
    if (lbIndex >= 0) {
      const it = currentLbItem();
      setLbInfo(true);
      speak(it.lat != null
        ? "지도에 촬영 위치를 표시해 두었어요."
        : "이 사진에는 위치 정보가 없어요.");
      return true;
    }
    switchView("map");
    speak("전체 사진을 지도에서 보여드릴게요.");
    return true;
  }

  // --- 인물 ---
  if (/(인물|사람|얼굴).*(보여|목록|분류|모아)/.test(t)) {
    switchView("people");
    speak("인물별로 모아 보여드릴게요. 이름을 붙이면 음성으로도 찾을 수 있어요.");
    return true;
  }

  // --- 폰 연결 ---
  if (/(폰|핸드폰|휴대폰|업로드).*(연결|올리|백업|전송)/.test(t)) {
    switchView("phone");
    speak("폰 연결 화면이에요. QR 코드를 폰으로 찍으면 업로드 페이지가 열려요.");
    return true;
  }

  // --- 중복 ---
  if (/중복.*(모두|전부|다)?.*(지워|삭제|없애|치워)/.test(t)) {
    cleanAllDuplicates();
    return true;
  }
  if (/중복.*(보여|정리|찾아)/.test(t)) { switchView("duplicates"); speak("중복 사진을 확인해 볼게요."); return true; }
  if (/(전체|모든|모두).*(사진|보여)/.test(t) || /^사진(보여줘|탭)?$/.test(t)) {
    closeLightbox(); switchView("photos"); speak("전체 사진이에요."); return true;
  }

  // --- "N번째 사진 보여줘" ---
  const m = t.match(/(첫|한|두|세|네|다섯|여섯|일곱|여덟|아홉|열|\d+)\s*번(째)?/);
  if (m && /(보여|열어|크게|선택)/.test(t)) {
    const n = ORDINALS[m[1]] || parseInt(m[1], 10);
    if (n >= 1 && n <= state.currentItems.length) {
      openLightbox(n - 1);
      speak(`${n}번째 사진이에요.`);
      return true;
    }
  }
  return false;
}

function handleUtterance(text) {
  addMsg(text, "user");
  if (!tryUICommand(text)) sendToAI(text);
}

/* ---------------- 음성 인식 (STT) ---------------- */
const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
const IS_MOBILE = /Android|iPhone|iPad|iPod/i.test(navigator.userAgent);
let recog = null;
let permissionDenied = false;
// 같은 발화가 여러 번 최종 인식되는 것을 막는 중복 가드
let lastFinalText = "";
let lastFinalAt = 0;

function initRecognition() {
  if (!SR) return null;
  const r = new SR();
  r.lang = "ko-KR";
  r.interimResults = true;
  // 모바일/태블릿 크롬은 continuous=true에서 같은 발화를 3~4번 재인식하는 버그가 있다.
  // 모바일은 세션당 1발화(continuous=false)로 처리하고, 연속 대화는 onend→재시작이 담당.
  r.continuous = !IS_MOBILE;

  r.onstart = () => {
    recognizing = true;
    $("#voice-orb").classList.add("listening");
    $("#voice-state").textContent = "듣고 있어요… 말씀하세요";
  };
  r.onresult = (e) => {
    for (let i = e.resultIndex; i < e.results.length; i++) {
      const res = e.results[i];
      if (res.isFinal) {
        const text = res[0].transcript.trim();
        if (!text) continue;
        // 동일 발화가 2.5초 안에 다시 최종 인식되면 무시(모바일 중복 버그 방지)
        const now = Date.now();
        if (text === lastFinalText && now - lastFinalAt < 2500) continue;
        lastFinalText = text;
        lastFinalAt = now;
        handleUtterance(text);
      } else {
        $("#voice-state").textContent = res[0].transcript;
      }
    }
  };
  r.onerror = (e) => {
    if (e.error === "not-allowed" || e.error === "service-not-allowed") {
      voiceMode = false;
      permissionDenied = true;
      stopKeepAlive();
      $("#voice-orb").classList.remove("listening");
      $("#voice-state").textContent = "마이크 권한이 거부되었습니다. 브라우저 설정에서 허용해 주세요.";
    }
    // no-speech · network · aborted 등은 무시 — onend가 이어서 재시작한다.
  };
  r.onend = () => {
    recognizing = false;
    $("#voice-orb").classList.remove("listening");
    // 모바일/태블릿 크롬은 continuous를 무시해 매 발화·침묵 후 세션이 끊긴다.
    // voiceMode가 켜져 있으면 즉시 다시 듣기 시작(연속 대화 유지).
    if (voiceMode && !speaking) setTimeout(startListening, 250);
    else if (!voiceMode && !permissionDenied) $("#voice-state").textContent = "마이크를 눌러 음성 대화를 시작하세요";
  };
  return r;
}

function startListening() {
  if (!voiceMode || recognizing || speaking) return;
  try {
    recog.start();
  } catch (_) {
    setTimeout(startListening, 400); // 이전 세션 정리 중이면 재시도
  }
}

/* ---------------- 듣기 유지 watchdog ----------------
   모바일/태블릿에서 onend/onerror를 놓치거나 재시작이 조용히 실패해도
   주기적으로 듣기 상태를 복구한다(마이크 계속 켜짐 보장). */
let keepAliveTimer = null;
function startKeepAlive() {
  stopKeepAlive();
  keepAliveTimer = setInterval(() => {
    if (voiceMode && !recognizing && !speaking) startListening();
  }, 2500);
}
function stopKeepAlive() {
  if (keepAliveTimer) { clearInterval(keepAliveTimer); keepAliveTimer = null; }
}

// 탭 복귀 시 듣기 재개 (백그라운드 전환 중 세션이 끊긴 경우 복구)
document.addEventListener("visibilitychange", () => {
  if (!document.hidden && voiceMode && !recognizing && !speaking) startListening();
});

/* ---------------- 음성 합성 (TTS) ---------------- */
function speak(text) {
  if (!("speechSynthesis" in window)) return;
  speaking = true; // onstart보다 먼저 설정해 인식 자동 재시작과의 경쟁 방지
  speechSynthesis.cancel();
  if (recognizing) { try { recog.stop(); } catch (_) {} }
  const u = new SpeechSynthesisUtterance(text);
  u.lang = "ko-KR";
  const ko = speechSynthesis.getVoices().find((v) => v.lang.startsWith("ko"));
  if (ko) u.voice = ko;
  u.rate = 1.05;
  u.onstart = () => {
    $("#voice-orb").classList.add("speaking");
    $("#voice-state").textContent = "답변 중…";
  };
  const done = () => {
    speaking = false;
    $("#voice-orb").classList.remove("speaking");
    if (voiceMode) setTimeout(startListening, 300);
  };
  u.onend = done;
  u.onerror = done;
  speechSynthesis.speak(u);
}
if ("speechSynthesis" in window) speechSynthesis.getVoices();

/* ---------------- UI 이벤트 ---------------- */
$("#voice-orb").onclick = () => {
  if (!SR) {
    $("#voice-state").textContent = "이 브라우저는 음성 인식을 지원하지 않아요. Chrome을 사용해 주세요.";
    return;
  }
  voiceMode = !voiceMode;
  if (voiceMode) {
    permissionDenied = false;
    if (!recog) recog = initRecognition();
    addMsg("음성 대화 모드 시작. 예: \"바닷가 사진 찾아줘\", \"슬라이드쇼 시작\", \"가족 앨범 만들어줘\", \"즐겨찾기에 추가해줘\"", "ai");
    startListening();
    startKeepAlive(); // 태블릿에서 마이크가 계속 켜져 있도록 유지
  } else {
    stopKeepAlive();
    speechSynthesis.cancel();
    if (recognizing) try { recog.stop(); } catch (_) {}
    speaking = false;
    $("#voice-orb").classList.remove("listening");
    $("#voice-state").textContent = "마이크를 눌러 음성 대화를 시작하세요";
  }
};

$("#chat-form").onsubmit = (e) => {
  e.preventDefault();
  const input = $("#chat-input");
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  handleUtterance(text);
};

addMsg("안녕하세요! 사진을 자연어로 찾고, 음성으로 앨범·슬라이드쇼까지 조작할 수 있는 PhotoNest AI입니다.", "ai");
