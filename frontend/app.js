/* PhotoNest AI — 코어: 상태, API, 뷰 라우터, 멀티선택, 앨범 모달 */

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => [...document.querySelectorAll(sel)];

const state = {
  view: "photos",          // photos|map|albums|albumDetail|favorites|trash|duplicates|search
  currentItems: [],        // 현재 그리드의 평면 배열 (라이트박스/슬라이드쇼/선택 기준)
  currentAlbum: null,      // albumDetail에서의 앨범 객체
  monthItems: {},          // 타임라인: ym → items (지연 로딩)
  months: [],              // [{ym, count}]
  selection: new Set(),
  searchQuery: "",
};

/* ---------------- API ---------------- */
const api = {
  get: (url) => fetch(url).then((r) => r.json()),
  post: (url, body) =>
    fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : undefined,
    }).then((r) => r.json()),
  put: (url, body) =>
    fetch(url, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then((r) => r.json()),
  del: (url) => fetch(url, { method: "DELETE" }).then((r) => r.json()),
};

/* ---------------- 상태 폴링 ---------------- */
let _pollTimer = null;
async function pollStatus() {
  clearTimeout(_pollTimer); // 중복 폴링 루프 방지 (설정 저장 후 수동 호출 대비)
  try {
    const s = await api.get("/api/status");
    const el = $("#index-status");
    if (s.error) el.textContent = "색인 오류: " + s.error;
    else if (s.indexing) el.textContent = `${s.phase || "색인"} 중… ${s.done}/${s.total}`;
    else if (s.ready) {
      const engineLabel = s.engine === "local-llm"
        ? ` · 🧠 ${s.llm_name || "로컬 LLM"}`
        : s.engine === "openrouter" ? ` · 🌐 ${s.llm_model || "OpenRouter"}`
        : s.engine === "claude" ? " · ☁️ Claude" : "";
      el.textContent =
        `${s.count}개 미디어` +
        (s.ai ? (s.ai_ready ? " · AI 검색 켜짐" : "") : " · AI 검색 꺼짐") +
        engineLabel;
      if (!state._loaded) {
        state._loaded = true;
        switchView("photos");
      }
      if (!s.indexing && s.ready && (!s.ai || s.ai_ready)) return; // 폴링 종료
    } else el.textContent = "색인 준비 중…";
  } catch (_) { /* 서버 기동 대기 */ }
  _pollTimer = setTimeout(pollStatus, 1500);
}

/* ---------------- 뷰 라우터 ---------------- */
const VIEW_TITLES = {
  photos: "사진", map: "지도", albums: "앨범", favorites: "즐겨찾기",
  trash: "휴지통", duplicates: "중복 정리", search: "검색 결과", albumDetail: "앨범",
  people: "인물", personDetail: "인물", phone: "폰 연결",
};

function switchView(view, opts = {}) {
  state.view = view;
  clearSelection();
  $$(".nav-btn[data-view]").forEach((b) =>
    b.classList.toggle("active", b.dataset.view === view ||
      (view === "albumDetail" && b.dataset.view === "albums") ||
      (view === "personDetail" && b.dataset.view === "people") ||
      (view === "search" && b.dataset.view === "photos"))
  );
  $("#view-title").textContent = opts.title || VIEW_TITLES[view] || view;
  $("#view-count").textContent = "";
  $("#topbar-actions").innerHTML = "";
  const c = $("#content");
  c.innerHTML = "";
  c.className = view === "map" ? "map-mode" : "";
  views[view] && views[view](opts);
}

$$(".nav-btn[data-view]").forEach((b) => {
  b.onclick = () => switchView(b.dataset.view);
});

$("#chat-toggle").onclick = () => {
  document.body.classList.toggle("chat-hidden");
};

/* ---------------- 그리드 타일 ---------------- */
function makeTile(it, index) {
  const div = document.createElement("div");
  div.className = "tile";
  div.dataset.id = it.id;
  const ar = it.width && it.height ? it.width / it.height : 1.33;
  div.style.setProperty("--ar", ar.toFixed(3));
  const badges =
    (it.type === "video" ? `<span class="badge">▶ 동영상</span>` : "") +
    (it.favorite ? `<span class="fav-badge">⭐</span>` : "") +
    (it.score != null ? `<span class="score">${Math.round(it.score * 100)}%</span>` : "");
  div.innerHTML =
    `<img src="/thumbs/${it.id}.jpg" loading="lazy" alt="">` +
    badges +
    `<span class="sel-check" title="선택">✓</span>`;
  div.querySelector(".sel-check").onclick = (e) => {
    e.stopPropagation();
    toggleSelect(it.id, div);
  };
  div.onclick = () => {
    if (state.selection.size > 0) toggleSelect(it.id, div);
    else openLightbox(index);
  };
  return div;
}

function renderFlatGrid(items, container) {
  const row = document.createElement("div");
  row.className = "group-grid";
  items.forEach((it, i) => row.appendChild(makeTile(it, i)));
  (container || $("#content")).appendChild(row);
}

/* ---------------- 멀티선택 ---------------- */
function toggleSelect(id, tileEl) {
  if (state.selection.has(id)) state.selection.delete(id);
  else state.selection.add(id);
  if (tileEl) tileEl.classList.toggle("selected", state.selection.has(id));
  // 타임라인이면 해당 타일이 속한 월 헤더 체크 상태 갱신
  if (tileEl && typeof updateMonthCheck === "function") {
    const sec = tileEl.closest(".date-group");
    if (sec) updateMonthCheck(sec.dataset.ym);
  }
  updateSelectionBar();
}

function clearSelection() {
  state.selection.clear();
  $$(".tile.selected").forEach((t) => t.classList.remove("selected"));
  $$(".month-check").forEach((b) => { b.textContent = "◻"; b.classList.remove("on"); });
  updateSelectionBar();
}

function updateSelectionBar() {
  const n = state.selection.size;
  $("#selection-bar").hidden = n === 0;
  $("#sel-count").textContent = `${n}개 선택됨`;
  const inTrash = state.view === "trash";
  const inAlbum = state.view === "albumDetail";
  $("#sel-album").hidden = inTrash;
  $("#sel-fav").hidden = inTrash;
  $("#sel-trash").hidden = inTrash;
  $("#sel-album-remove").hidden = !inAlbum;
  $("#sel-restore").hidden = !inTrash;
  $("#sel-delete").hidden = !inTrash;
}

$("#sel-cancel").onclick = clearSelection;

$("#sel-fav").onclick = async () => {
  for (const id of state.selection) await api.post(`/api/media/${id}/favorite`, { value: true });
  speak(`${state.selection.size}장을 즐겨찾기에 추가했어요.`);
  refreshView();
};

$("#sel-trash").onclick = async () => {
  for (const id of state.selection) await api.post(`/api/media/${id}/trash`);
  speak(`${state.selection.size}장을 휴지통으로 옮겼어요.`);
  refreshView();
};

$("#sel-album").onclick = () => openAlbumModal([...state.selection]);

$("#sel-album-remove").onclick = async () => {
  if (!state.currentAlbum) return;
  await api.post(`/api/albums/${state.currentAlbum.id}/items/remove`, {
    media_ids: [...state.selection],
  });
  speak("앨범에서 제거했어요.");
  refreshView();
};

$("#sel-restore").onclick = async () => {
  for (const id of state.selection) await api.post(`/api/media/${id}/restore`);
  speak("복원했어요.");
  refreshView();
};

$("#sel-delete").onclick = async () => {
  if (!confirm(`${state.selection.size}개 항목을 영구 삭제할까요? 되돌릴 수 없습니다.`)) return;
  for (const id of state.selection) await api.del(`/api/media/${id}`);
  speak("영구 삭제했어요.");
  refreshView();
};

function refreshView() {
  const v = state.view;
  if (v === "albumDetail") switchView(v, { albumId: state.currentAlbum?.id });
  else if (v === "search") { /* 검색 결과는 재검색 없이 유지 */ clearSelection(); }
  else switchView(v);
}

/* ---------------- 앨범 모달 ---------------- */
let albumModalIds = [];

async function openAlbumModal(mediaIds) {
  albumModalIds = mediaIds;
  const { albums } = await api.get("/api/albums");
  const list = $("#album-modal-list");
  list.innerHTML = "";
  if (!albums.length) list.innerHTML = `<p class="muted">아직 앨범이 없어요. 아래에서 만들어 보세요.</p>`;
  albums.forEach((a) => {
    const b = document.createElement("button");
    b.className = "album-modal-item";
    b.innerHTML =
      (a.cover_id ? `<img src="/thumbs/${a.cover_id}.jpg">` : `<span class="cover-ph">📁</span>`) +
      `<span>${a.name}</span><small>${a.count}장</small>`;
    b.onclick = () => addToAlbum(a.id, a.name);
    list.appendChild(b);
  });
  $("#album-modal").hidden = false;
}

async function addToAlbum(albumId, name) {
  await api.post(`/api/albums/${albumId}/items`, { media_ids: albumModalIds });
  $("#album-modal").hidden = true;
  speak(`${albumModalIds.length}장을 '${name}' 앨범에 추가했어요.`);
  clearSelection();
}

$("#album-modal-create").onclick = async () => {
  const name = $("#album-modal-name").value.trim();
  if (!name) return;
  const r = await api.post("/api/albums", { name });
  $("#album-modal-name").value = "";
  await addToAlbum(r.id, name);
};

$("#album-modal-close").onclick = () => ($("#album-modal").hidden = true);

/* ---------------- 설정 (AI 엔진 / OpenRouter) ---------------- */
function syncSettingsUI() {
  const mode = ($("input[name=engine_mode]:checked") || {}).value;
  // OpenRouter 필드는 auto/openrouter일 때만 강조(항상 편집은 가능)
  $("#openrouter-fields").classList.toggle("dimmed", !(mode === "auto" || mode === "openrouter"));
}

async function openSettings() {
  const s = await api.get("/api/settings");
  // 프리셋 datalist 채우기
  const dl = $("#or-model-presets");
  dl.innerHTML = (s.openrouter_presets || [])
    .map((m) => `<option value="${m.id}">${m.label}</option>`).join("");
  // 엔진 라디오
  const radio = $(`input[name=engine_mode][value="${s.engine_mode}"]`);
  if (radio) radio.checked = true;
  // 키: 마스킹된 표시값(placeholder처럼) — 비우면 유지, 새로 입력하면 교체
  $("#or-key").value = "";
  $("#or-key").placeholder = s.openrouter_key_set ? s.openrouter_key_masked : "sk-or-v1-...";
  $("#or-model").value = s.openrouter_model || s.default_model || "";
  $("#or-test-status").textContent = "";
  syncSettingsUI();
  $("#settings-modal").hidden = false;
}

$("#settings-btn").onclick = openSettings;
$("#settings-close").onclick = () => ($("#settings-modal").hidden = true);
$("#engine-options").addEventListener("change", syncSettingsUI);

$("#or-test").onclick = async () => {
  const status = $("#or-test-status");
  status.className = "";
  status.textContent = "테스트 중…";
  const r = await api.post("/api/settings/test", {
    openrouter_api_key: $("#or-key").value || undefined,
    openrouter_model: $("#or-model").value || undefined,
  });
  status.className = r.ok ? "ok" : "err";
  status.textContent = r.message || (r.ok ? "성공" : "실패");
};

$("#settings-save").onclick = async () => {
  const patch = {
    engine_mode: ($("input[name=engine_mode]:checked") || {}).value,
    openrouter_model: $("#or-model").value.trim() || undefined,
  };
  const key = $("#or-key").value.trim();
  if (key) patch.openrouter_api_key = key; // 비어있으면 기존 키 유지
  const r = await api.post("/api/settings", patch);
  if (r.error) { $("#or-test-status").className = "err"; $("#or-test-status").textContent = r.error; return; }
  $("#settings-modal").hidden = true;
  pollStatus(); // 상태줄 엔진 라벨 즉시 갱신
  if (typeof speak === "function") speak("설정을 저장했어요.");
};

/* ---------------- 시작 ---------------- */
window.addEventListener("DOMContentLoaded", pollStatus);
