/* PhotoNest AI — 뷰 렌더러: 타임라인/지도/앨범/즐겨찾기/휴지통/중복/검색 */

const views = {};
let geoMap = null;

function dateLabel(iso) {
  if (!iso) return "날짜 없음";
  const d = new Date(iso);
  return `${d.getFullYear()}년 ${d.getMonth() + 1}월 ${d.getDate()}일`;
}

function monthLabel(ym) {
  const [y, m] = ym.split("-");
  return `${y}년 ${parseInt(m, 10)}월`;
}

/* 현재 화면의 평면 아이템 배열 재계산 (라이트박스 인덱스 일치용) */
function rebuildCurrentItems() {
  if (state.view !== "photos") return;
  const list = [];
  for (const m of state.months) {
    if (state.monthItems[m.ym]) list.push(...state.monthItems[m.ym]);
  }
  state.currentItems = list;
  // 타일 인덱스 재바인딩
  $$("#content .tile").forEach((t) => {
    const idx = list.findIndex((it) => it.id === t.dataset.id);
    t.onclick = () => {
      if (state.selection.size > 0) toggleSelect(t.dataset.id, t);
      else openLightbox(idx);
    };
  });
}

/* ---------------- 사진 (타임라인) ---------------- */
views.photos = async () => {
  const tl = await api.get("/api/timeline");
  state.months = tl.months;
  state.monthItems = {};
  state.currentItems = [];
  $("#view-count").textContent = tl.total ? `${tl.total}장` : "";

  const c = $("#content");
  if (!tl.total) {
    c.innerHTML = `<div class="empty">photos 폴더에 사진/동영상을 넣으면 자동으로 색인됩니다</div>`;
    return;
  }

  addTopbarBtn("▶ 슬라이드쇼", () => startSlideshow(0));

  const io = new IntersectionObserver(async (entries) => {
    for (const e of entries) {
      if (!e.isIntersecting) continue;
      const sec = e.target;
      io.unobserve(sec);
      const ym = sec.dataset.ym;
      const { items } = await api.get(`/api/photos?month=${ym}`);
      state.monthItems[ym] = items;
      const grid = sec.querySelector(".group-grid");
      grid.innerHTML = "";
      items.forEach((it) => grid.appendChild(makeTile(it, 0)));
      rebuildCurrentItems();
    }
  }, { rootMargin: "600px" });

  for (const m of tl.months) {
    const sec = document.createElement("div");
    sec.className = "date-group";
    sec.dataset.ym = m.ym;
    sec.innerHTML =
      `<h4><button class="month-check" title="이 달 전체 선택/해제" data-ym="${m.ym}">◻</button>` +
      `${monthLabel(m.ym)} <small>${m.count}장</small></h4>` +
      `<div class="group-grid" style="min-height:120px"><span class="loading-ph">불러오는 중…</span></div>`;
    sec.querySelector(".month-check").onclick = (e) => {
      e.stopPropagation();
      toggleMonthSelection(m.ym);
    };
    c.appendChild(sec);
    io.observe(sec);
  }
};

/* 해당 월 전체 선택/해제 토글 (지연 로딩된 월은 먼저 데이터 로드) */
async function toggleMonthSelection(ym) {
  let items = state.monthItems[ym];
  if (!items) {
    items = (await api.get(`/api/photos?month=${ym}`)).items;
    state.monthItems[ym] = items;
  }
  // 이 달 사진이 모두 선택돼 있으면 해제, 아니면 전체 선택
  const allSelected = items.length > 0 && items.every((it) => state.selection.has(it.id));
  items.forEach((it) => {
    if (allSelected) state.selection.delete(it.id);
    else state.selection.add(it.id);
  });
  // 화면에 보이는 타일 상태 갱신
  const sec = document.querySelector(`.date-group[data-ym="${ym}"]`);
  if (sec) {
    sec.querySelectorAll(".tile").forEach((t) =>
      t.classList.toggle("selected", state.selection.has(t.dataset.id))
    );
  }
  updateMonthCheck(ym);
  updateSelectionBar();
}

/* 월 헤더 체크 아이콘 상태(전체/일부/없음) 갱신 */
function updateMonthCheck(ym) {
  const btn = document.querySelector(`.month-check[data-ym="${ym}"]`);
  const items = state.monthItems[ym];
  if (!btn || !items || !items.length) return;
  const selN = items.filter((it) => state.selection.has(it.id)).length;
  btn.textContent = selN === 0 ? "◻" : selN === items.length ? "☑" : "◧";
  btn.classList.toggle("on", selN > 0);
}

/* ---------------- 지도 ---------------- */
// 묶음(클러스터)을 누르면 그 지역 사진만 옆 패널에 모아 보여 준다 — 상세보기의 앞뒤 이동,
// 슬라이드쇼, 앨범 추가가 모두 그 지역 사진 안에서만 움직인다.
let geoItems = [];      // 지도의 전체 사진
views.map = async () => {
  const { items } = await api.get("/api/geo");
  geoItems = items;
  $("#view-count").textContent = `위치 정보가 있는 미디어 ${items.length}장`;
  const c = $("#content");
  if (!items.length) {
    c.innerHTML = `<div class="empty">GPS 정보가 포함된 사진이 없습니다</div>`;
    return;
  }
  const mapDiv = document.createElement("div");
  mapDiv.id = "geo-map";
  c.appendChild(mapDiv);

  if (geoMap) { geoMap.remove(); geoMap = null; }
  geoMap = L.map(mapDiv);
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: "© OpenStreetMap",
  }).addTo(geoMap);

  state.currentItems = items;
  // 클릭은 확대 대신 지역 패널 — 확대는 핀치·휠·패널의 '지도 확대'로
  const cluster = L.markerClusterGroup({ zoomToBoundsOnClick: false });
  const bounds = [];
  items.forEach((it) => {
    const icon = L.divIcon({
      className: "photo-marker",
      html: `<img src="/thumbs/${it.id}.jpg">`,
      iconSize: [52, 52],
    });
    const mk = L.marker([it.lat, it.lon], { icon });
    mk.item = it;
    mk.on("click", () => {
      // 한 장짜리 핀 — 지금 화면에 보이는 사진들 사이에서 앞뒤로 넘긴다
      const visible = geoVisibleItems();
      closeRegionPanel();
      state.currentItems = visible;
      openLightbox(Math.max(0, visible.findIndex((v) => v.id === it.id)));
    });
    cluster.addLayer(mk);
    bounds.push([it.lat, it.lon]);
  });
  cluster.on("clusterclick", (e) => {
    const items = e.layer.getAllChildMarkers().map((m) => m.item);
    openRegionPanel(byNewest(items), e.layer.getBounds());
  });
  geoMap.addLayer(cluster);
  geoMap.fitBounds(bounds, { padding: [40, 40] });

  addTopbarBtn("📍 이 화면의 사진", () => {
    const visible = geoVisibleItems();
    if (!visible.length) { speak("지금 지도 화면에는 사진이 없어요."); return; }
    openRegionPanel(visible, null);
  });
};

function byNewest(items) {
  return [...items].sort((a, b) => (b.taken_at || "").localeCompare(a.taken_at || ""));
}

function geoVisibleItems() {
  const b = geoMap.getBounds();
  return byNewest(geoItems.filter((it) => b.contains([it.lat, it.lon])));
}

// 지명 문자열("상림골 Sangnim-gol 진관동 은평구 서울 …")에서 동네 이름 하나 — 가장 흔한 것
function regionName(items) {
  const count = {};
  items.forEach((it) => {
    const ko = (it.place_name || "").split(" ").filter((t) => /[가-힣]/.test(t));
    const name = ko.find((t) => /(동|읍|면|리|가)$/.test(t)) || ko[0];
    if (name) count[name] = (count[name] || 0) + 1;
  });
  const best = Object.entries(count).sort((a, b) => b[1] - a[1])[0];
  return best ? best[0] : "";
}

function regionPeriod(items) {
  const ym = items.map((it) => (it.taken_at || "").slice(0, 7)).filter(Boolean).sort();
  if (!ym.length) return "";
  const fmt = (s) => s.replace("-", ".");
  return ym[0] === ym[ym.length - 1] ? fmt(ym[0]) : `${fmt(ym[0])}~${fmt(ym[ym.length - 1])}`;
}

function openRegionPanel(items, bounds) {
  closeRegionPanel();
  clearSelection();
  state.currentItems = items;
  const place = regionName(items);
  const period = regionPeriod(items);
  const panel = document.createElement("aside");
  panel.id = "region-panel";
  panel.innerHTML =
    `<div class="region-head">
       <div><strong>${place || "이 지역"}</strong><small>${[period, `${items.length}장`].filter(Boolean).join(" · ")}</small></div>
       <button class="region-close" title="닫기">✕</button>
     </div>
     <div class="region-actions">
       <button data-act="album">📁 앨범에 추가</button>
       <button data-act="slideshow">▶ 슬라이드쇼</button>
       ${bounds ? `<button data-act="zoom">🔍 지도 확대</button>` : ""}
     </div>
     <div class="region-grid"></div>`;
  panel.querySelector(".region-close").onclick = closeRegionPanel;
  panel.querySelector('[data-act="album"]').onclick = () => {
    // 고른 사진이 있으면 그것만, 없으면 이 지역 전체
    const ids = state.selection.size ? [...state.selection] : items.map((it) => it.id);
    openAlbumModal(ids, [place, period].filter(Boolean).join(" "));
  };
  panel.querySelector('[data-act="slideshow"]').onclick = () => startSlideshow(0);
  if (bounds) {
    panel.querySelector('[data-act="zoom"]').onclick = () =>
      geoMap.fitBounds(bounds, { padding: [40, 40] });
  }
  renderFlatGrid(items, panel.querySelector(".region-grid"));
  $("#content").appendChild(panel);
}

function closeRegionPanel() {
  const p = document.getElementById("region-panel");
  if (!p) return;
  p.remove();
  clearSelection();
  state.currentItems = geoItems;
}

/* ---------------- 인물 ---------------- */
views.people = async () => {
  const { persons } = await api.get("/api/persons");
  $("#view-count").textContent = persons.length ? `${persons.length}명` : "";
  const c = $("#content");
  if (!persons.length) {
    c.innerHTML = `<div class="empty">아직 인식된 인물이 없습니다. 얼굴 분석이 진행 중이면 잠시 후 다시 확인해 주세요</div>`;
    return;
  }
  const grid = document.createElement("div");
  grid.className = "people-grid";
  persons.forEach((p) => {
    const card = document.createElement("div");
    card.className = "person-card";
    card.innerHTML =
      `<img src="/faces/${p.cover_face_id}.jpg" alt="" onerror="this.style.visibility='hidden'">` +
      `<strong>${p.name || "이름 추가"}</strong><span>${p.count}장</span>` +
      `<button class="person-rename" title="이름 입력/변경">✏️</button>`;
    card.querySelector(".person-rename").onclick = async (e) => {
      e.stopPropagation();
      const name = prompt("이 사람의 이름을 입력하세요 (같은 이름이 이미 있으면 자동으로 합쳐집니다)", p.name || "");
      if (name === null) return;
      const r = await api.put(`/api/persons/${p.id}`, { name: name.trim() });
      const nm = name.trim();
      if (r.merged) speak(`같은 이름 '${nm}'이 있어서 하나로 합쳤어요.`);
      else speak(nm ? `'${nm}'으로 저장했어요. 이제 "${nm} 사진 보여줘"라고 말해 보세요.` : "이름을 지웠어요.");
      switchView("people");
    };
    card.onclick = () => switchView("personDetail", { personId: p.id });
    grid.appendChild(card);
  });
  c.appendChild(grid);
};

views.personDetail = async ({ personId }) => {
  const data = await api.get(`/api/persons/${personId}`);
  if (data.error) return switchView("people");
  state.currentItems = data.items;
  $("#view-title").textContent = `👤 ${data.person.name || "이름 없는 인물"}`;
  $("#view-count").textContent = `${data.items.length}장`;
  addTopbarBtn("▶ 슬라이드쇼", () => startSlideshow(0));
  addTopbarBtn("✏️ 이름 입력", async () => {
    const name = prompt("이 사람의 이름 (같은 이름이 이미 있으면 자동으로 합쳐집니다)", data.person.name || "");
    if (name === null) return;
    const r = await api.put(`/api/persons/${personId}`, { name: name.trim() });
    if (r.merged) speak(`같은 이름이 있어서 하나로 합쳤어요.`);
    switchView("personDetail", { personId: r.id || personId });
  });
  addTopbarBtn("← 인물 목록", () => switchView("people"));
  if (!data.items.length) {
    $("#content").innerHTML = `<div class="empty">사진이 없습니다</div>`;
    return;
  }
  renderFlatGrid(data.items);
};

/* ---------------- 앨범 ---------------- */
views.albums = async () => {
  const { albums } = await api.get("/api/albums");
  $("#view-count").textContent = albums.length ? `${albums.length}개` : "";
  addTopbarBtn("➕ 새 앨범", async () => {
    const name = prompt("새 앨범 이름을 입력하세요");
    if (!name || !name.trim()) return;
    await api.post("/api/albums", { name: name.trim() });
    switchView("albums");
  });

  const c = $("#content");
  if (!albums.length) {
    c.innerHTML = `<div class="empty">앨범이 없습니다. 사진을 선택한 뒤 '앨범에 추가'를 누르거나, 음성으로 "가족 앨범 만들어줘"라고 말해 보세요</div>`;
    return;
  }
  const grid = document.createElement("div");
  grid.className = "album-grid";
  albums.forEach((a) => {
    const card = document.createElement("div");
    card.className = "album-card";
    card.innerHTML =
      (a.cover_id
        ? `<img src="/thumbs/${a.cover_id}.jpg" alt="">`
        : `<div class="cover-ph">📁</div>`) +
      `<div class="album-meta"><strong>${a.name}</strong><span>${a.count}장</span></div>`;
    card.onclick = () => switchView("albumDetail", { albumId: a.id });
    grid.appendChild(card);
  });
  c.appendChild(grid);
};

views.albumDetail = async ({ albumId }) => {
  const data = await api.get(`/api/albums/${albumId}`);
  if (data.error) return switchView("albums");
  state.currentAlbum = data.album;
  state.currentItems = data.items;
  $("#view-title").textContent = `📁 ${data.album.name}`;
  $("#view-count").textContent = `${data.items.length}장`;

  addTopbarBtn("▶ 슬라이드쇼", () => startSlideshow(0));
  addTopbarBtn("✏️ 이름 변경", async () => {
    const name = prompt("새 이름", data.album.name);
    if (!name || !name.trim()) return;
    await api.put(`/api/albums/${albumId}`, { name: name.trim() });
    switchView("albumDetail", { albumId });
  });
  addTopbarBtn("🗑️ 앨범 삭제", async () => {
    if (!confirm(`'${data.album.name}' 앨범을 삭제할까요? (사진은 삭제되지 않습니다)`)) return;
    await api.del(`/api/albums/${albumId}`);
    speak("앨범을 삭제했어요.");
    switchView("albums");
  });
  addTopbarBtn("← 앨범 목록", () => switchView("albums"));

  if (!data.items.length) {
    $("#content").innerHTML = `<div class="empty">빈 앨범입니다. 사진 탭에서 사진을 선택해 추가하세요</div>`;
    return;
  }
  renderFlatGrid(data.items);
};

/* ---------------- 즐겨찾기 ---------------- */
views.favorites = async () => {
  const { items } = await api.get("/api/photos?favorites=true");
  state.currentItems = items;
  $("#view-count").textContent = items.length ? `${items.length}장` : "";
  if (!items.length) {
    $("#content").innerHTML = `<div class="empty">즐겨찾기한 사진이 없습니다. 사진을 열고 ☆를 누르거나 "즐겨찾기에 추가해줘"라고 말해 보세요</div>`;
    return;
  }
  addTopbarBtn("▶ 슬라이드쇼", () => startSlideshow(0));
  renderFlatGrid(items);
};

/* ---------------- 휴지통 ---------------- */
views.trash = async () => {
  const { items } = await api.get("/api/trash");
  state.currentItems = items;
  $("#view-count").textContent = items.length ? `${items.length}장` : "";
  if (!items.length) {
    $("#content").innerHTML = `<div class="empty">휴지통이 비어 있습니다</div>`;
    return;
  }
  addTopbarBtn("🧹 휴지통 비우기", async () => {
    if (!confirm("휴지통의 모든 항목을 영구 삭제할까요? 되돌릴 수 없습니다.")) return;
    const r = await api.post("/api/trash/empty");
    speak(`${r.deleted}개 항목을 영구 삭제했어요.`);
    switchView("trash");
  });
  renderFlatGrid(items);
};

/* ---------------- 중복 · 정리 추천 ---------------- */
// 묶음마다 '추천대로 선택'이 지울 만한 사진을 골라 두고, 지우기는 아래 선택 막대의
// 🗑️ 휴지통으로 한다(휴지통에서 복원 가능). 묶음 사진은 맨 앞 한 장을 남길 것으로 추천.
const CLEANUP_SECTIONS = [
  { key: "duplicates", title: "똑같은 사진", grouped: true,
    desc: "같은 파일이 여러 번 들어온 사진이에요. 한 장만 남기면 돼요." },
  { key: "similar", title: "비슷한 연속 사진", grouped: true,
    desc: "몇 초 사이에 거의 같게 여러 장 찍은 사진이에요. 즐겨찾기·앨범 사진이나 가장 선명한 사진을 남기도록 추천해요." },
  { key: "screenshots", title: "스크린샷",
    desc: "휴대폰·태블릿 화면을 캡처한 이미지예요. 필요한 것만 골라 남기세요." },
  { key: "documents", title: "문서·화면을 찍은 사진",
    desc: "영수증·문서·화이트보드·모니터 화면처럼 기록용으로 찍은 사진이에요." },
];
const CLEANUP_PAGE = 60; // 한 번에 그리는 타일 수 — 수백 장이어도 화면이 버벅이지 않게

views.duplicates = async () => {
  const c = $("#content");
  c.innerHTML = `<div class="empty">지울 만한 사진을 찾고 있어요…</div>`;
  const data = await api.get("/api/cleanup");
  if (state.view !== "duplicates") return; // 그 사이 다른 화면으로 이동
  c.innerHTML = "";

  // 라이트박스 인덱스는 화면 전체에서 하나로 센다
  const flat = [];
  const sections = CLEANUP_SECTIONS.map((sec) => {
    const groups = sec.grouped ? data[sec.key] || [] : [data[sec.key] || []];
    const tiles = [];      // {it, index, keep}
    const picks = [];      // '추천대로 선택'할 id
    groups.forEach((g) => g.forEach((it, i) => {
      const keep = sec.grouped && i === 0;
      tiles.push({ it, index: flat.length, keep });
      flat.push(it);
      if (!keep) picks.push(it.id);
    }));
    return { ...sec, groups, tiles, picks };
  });
  state.currentItems = flat;

  const total = sections.reduce((n, s) => n + s.picks.length, 0);
  $("#view-count").textContent = total ? `정리 추천 ${total}장` : "";
  if (!total) {
    c.innerHTML = `<div class="empty">정리할 사진이 없어요 🎉</div>`;
    return;
  }
  if (sections[0].picks.length) addTopbarBtn("🧹 똑같은 사진 모두 지우기", cleanAllDuplicates);
  if (!data.ai) {
    c.insertAdjacentHTML("beforeend",
      `<p class="cleanup-note">AI 검색이 꺼져 있어 '비슷한 사진·스크린샷·문서' 판정은 파일 이름으로만 했어요.</p>`);
  }

  sections.forEach((sec) => {
    if (!sec.picks.length) return;
    const box = document.createElement("section");
    box.className = "dup-group";
    const unit = sec.grouped ? `${sec.groups.length}묶음 · ` : "";
    box.innerHTML =
      `<div class="dup-head"><h4>${sec.title} <small>${unit}지울 만한 사진 ${sec.picks.length}장</small></h4></div>` +
      `<p class="cleanup-desc">${sec.desc}</p>`;
    const pick = document.createElement("button");
    pick.textContent = `추천대로 선택 (${sec.picks.length}장)`;
    pick.onclick = () => {
      sec.picks.forEach((id) => state.selection.add(id));
      $$("#content .tile").forEach((t) => {
        if (state.selection.has(t.dataset.id)) t.classList.add("selected");
      });
      updateSelectionBar();
      speak(`${sec.title} ${sec.picks.length}장을 골랐어요. 확인한 뒤 아래 휴지통 버튼을 누르세요.`);
    };
    box.querySelector(".dup-head").appendChild(pick);

    const grid = document.createElement("div");
    grid.className = "group-grid";
    box.appendChild(grid);
    let shown = 0;
    const more = document.createElement("button");
    more.className = "cleanup-more";
    const drawMore = () => {
      sec.tiles.slice(shown, shown + CLEANUP_PAGE).forEach(({ it, index, keep }, k) => {
        // 묶음이 바뀌는 자리에 구분선 — 어느 사진끼리 비슷한지 보이게
        if (sec.grouped && keep && shown + k > 0) {
          const br = document.createElement("div");
          br.className = "cleanup-break";
          grid.appendChild(br);
        }
        const t = makeTile(it, index);
        if (keep) t.insertAdjacentHTML("beforeend", `<span class="keep-badge">남김 추천</span>`);
        if (state.selection.has(it.id)) t.classList.add("selected");
        grid.appendChild(t);
      });
      shown = Math.min(shown + CLEANUP_PAGE, sec.tiles.length);
      more.hidden = shown >= sec.tiles.length;
      more.textContent = `더 보기 (${sec.tiles.length - shown}장 남음)`;
    };
    more.onclick = drawMore;
    drawMore();
    box.appendChild(more);
    c.appendChild(box);
  });
};

/* ---------------- 검색 결과 ---------------- */
views.search = ({ items, query }) => {
  state.currentItems = items || [];
  state.searchQuery = query || "";
  $("#view-title").textContent = `"${query}" 검색 결과`;
  $("#view-count").textContent = `${state.currentItems.length}장`;
  if (!state.currentItems.length) {
    $("#content").innerHTML = `<div class="empty">검색 결과가 없습니다</div>`;
    return;
  }
  addTopbarBtn("▶ 슬라이드쇼", () => startSlideshow(0));
  renderFlatGrid(state.currentItems);
};

/* ---------------- 폰 연결 ---------------- */
views.phone = async () => {
  const info = await api.get("/api/server-info");
  const appInfo = await api.get("/api/app-info").catch(() => ({ apk_available: false }));
  const c = $("#content");
  c.innerHTML = `
  <div class="phone-wrap">
    <section class="phone-card highlight">
      <h3>⭐ 전용 앱 (GPS 보존 · 와이파이 자동백업)</h3>
      <p>브라우저 업로드는 위치정보가 제거되지만, <strong>전용 앱은 원본 GPS를 그대로 보존</strong>하고
      <strong>지정한 와이파이에 들어오면 새 사진을 자동 백업</strong>합니다. ChatGPT처럼 대화하며 설정해요.</p>
      <div id="app-qr-box"></div>
      <div id="app-dl-status" class="small"></div>
      <p class="small">📱 <strong>안드로이드</strong>: 위 QR로 앱(APK)을 받아 설치(‘알 수 없는 출처’ 허용) 후 실행 →
      이 화면의 1번 QR을 앱으로 찍어 서버 연결.<br>
      🍏 <strong>아이폰</strong>: Apple 정책상 QR 직접 설치가 안 돼 App Store/TestFlight 배포가 필요합니다
      (빌드·배포 방법은 <code>mobile/README.md</code> 참고).</p>
      <div id="app-dev-box"></div>
    </section>
    <section class="phone-card">
      <h3>📤 1. 폰에서 바로 올리기 (수동)</h3>
      <p>폰 카메라로 QR을 찍거나 주소를 입력하세요. 같은 와이파이에 있어야 합니다.</p>
      <div id="qr-box"></div>
      <code class="phone-url">${info.upload_url}</code>
      <p class="small">사진·동영상이 원본 그대로 전송되고, 이미 서버에 있는 사진은 자동으로 건너뜁니다.
      홈 화면에 추가하면 앱처럼 쓸 수 있어요.</p>
      <p class="small">⚠️ <strong>위치정보(GPS)는 브라우저 업로드로 보존이 어렵습니다.</strong>
      안드로이드(특히 삼성 등)는 사진을 넘길 때 위치를 가립니다 — "둘러보기/내 파일"로 골라도
      제거될 수 있어요(촬영시각·화질은 보존됨). 위치까지 확실히 보존하려면 아래 <strong>2번 WebDAV
      자동 백업</strong>을 쓰고, 이미 올린 사진은 사진을 열어 <strong>지도를 클릭해 위치를 직접 지정</strong>할 수 있어요.</p>
    </section>
    <section class="phone-card">
      <h3>🔄 2. 와이파이 들어오면 자동 백업 (권장 · GPS 보존)</h3>
      <p>브라우저는 백그라운드 업로드가 불가능하고 위치도 제거되므로, 시스템 위치 권한으로
      <strong>원본 GPS를 보존</strong>하는 검증된 자동백업 앱을 서버에 연결합니다.</p>
      <ol>
        <li><strong>PhotoSync</strong>(아이폰·안드로이드) 또는 <strong>FolderSync</strong>(안드로이드) 설치</li>
        <li>전송 대상으로 <strong>WebDAV</strong> 선택 후 아래 주소 입력 (계정/암호는 아무 값이나)</li>
        <li><code>${info.webdav_url}</code></li>
        <li>앱의 <strong>자동 전송(Autotransfer)</strong>에서 "집 와이파이 연결 시" 조건을 켜면,
            폰이 이 와이파이에 들어올 때마다 새 사진을 백그라운드로 올립니다</li>
      </ol>
      <p class="small">업로드된 사진은 <code>photos/MobileBackup/</code>에 저장되고 자동으로 색인됩니다.
      서버에 이미 있는 사진(중복)은 저장하지 않습니다.</p>
    </section>
    <section class="phone-card">
      <h3>☁️ 3. 구글 포토 백업 (Takeout)</h3>
      <p>구글은 계정 직접 로그인이나 API로 전체 라이브러리를 내려받는 것을 막아두었고,
      <strong>위치정보(GPS)를 보존하는 유일한 방법이 Google Takeout</strong>입니다.</p>
      <ol>
        <li><a href="https://takeout.google.com" target="_blank" rel="noopener">takeout.google.com</a>에서
            <strong>Google 포토</strong>만 선택해 내보내기 (zip으로 받기)</li>
        <li>받은 <code>takeout-*.zip</code> 파일을 아래 버튼으로 올리기
            (서버 PC라면 <code>photos/</code> 폴더에 직접 넣어도 됩니다)</li>
        <li>끝! 자동으로 압축을 풀고 GPS·날짜를 살려 색인합니다</li>
      </ol>
      <div class="btn-row">
        <button class="pn-btn primary" id="to-pick">📦 테이크아웃 zip 올리기</button>
        <button class="pn-btn" id="to-cancel" hidden>⏹ 중지</button>
      </div>
      <input type="file" id="to-input" accept=".zip,application/zip" multiple hidden>
      <div class="pn-progress" id="to-progress" hidden><div class="pn-progress-bar" id="to-progress-bar"></div></div>
      <div id="to-upload-status" class="small"></div>
      <label class="small to-keep"><input type="checkbox" id="to-keep">
        처리 후 원본 zip 보관 (<code>photos/_zips_done/</code>) — 끄면 압축 해제가 끝난 zip을 삭제해 공간을 아낍니다</label>
      <p class="small">한 번에 최대 5개까지 고를 수 있고, 여러 개면 <strong>전부 올린 뒤 한 번에 압축 해제</strong>합니다.
      수 GB 파일도 16MB씩 나눠 보내며, 중간에 끊기면 같은 파일을 다시 고르면 이어서 올립니다.</p>
      <div id="takeout-status" class="small"></div>
      <div id="takeout-albums"></div>
      <div id="takeout-done"></div>
      <div id="takeout-history"></div>
    </section>
  </div>`;
  new QRCode(document.getElementById("qr-box"), {
    text: info.upload_url, width: 180, height: 180,
    colorDark: "#0f1117", colorLight: "#ffffff",
  });
  // 전용 앱 다운로드 QR (APK가 준비된 경우)
  if (appInfo.apk_available && appInfo.apk_url) {
    new QRCode(document.getElementById("app-qr-box"), {
      text: appInfo.apk_url, width: 180, height: 180,
      colorDark: "#0f1117", colorLight: "#ffffff",
    });
  }
  renderAppStatus(appInfo);
  // 개발용(dev client) 앱 QR — 개발자 본인용
  const devBox = document.getElementById("app-dev-box");
  if (devBox && appInfo.apk_dev_available && appInfo.apk_dev_url) {
    devBox.innerHTML =
      `<div class="dev-app"><strong>🛠 개발용 앱 (dev client)</strong>
       <span class="small">— Metro 연결로 코드 즉시 반영. 개발자 본인 설치용</span>
       <div id="app-dev-qr"></div>
       <div class="small">📥 <code>${appInfo.apk_dev_url}</code></div></div>`;
    new QRCode(document.getElementById("app-dev-qr"), {
      text: appInfo.apk_dev_url, width: 150, height: 150,
      colorDark: "#0f1117", colorLight: "#ffffff",
    });
  }
  // 대기 중인 Takeout zip 표시
  try {
    const { zips } = await api.get("/api/takeout/pending");
    const el = document.getElementById("takeout-status");
    if (el && zips.length) {
      const pend = zips.filter((z) => !z.processed);
      el.innerHTML = pend.length
        ? `📦 처리 대기 중인 zip ${pend.length}개: ${pend.map((z) => z.name + " (" + z.size_mb + "MB)").join(", ")} — 색인 시 자동 처리됩니다.`
        : `✅ photos 폴더의 Takeout zip ${zips.length}개가 모두 처리되었습니다.`;
    }
  } catch (_) { /* 무시 */ }
  renderTakeoutAlbums();
  renderTakeoutDone();
  renderTakeoutHistory();
  wireTakeoutUpload();
};

/* 전용 앱 배포 상태 — 저장소가 가리키는 빌드를 서버가 실제로 갖고 있는지.
   APK는 git에 없으므로(용량) git pull 뒤엔 서버가 링크를 보고 직접 받아온다. */
function renderAppStatus(a) {
  const el = document.getElementById("app-dl-status");
  if (!el) return;  // 다른 화면으로 넘어갔다
  const up = a.apk_update || {};
  const ver = a.apk_version ? `v${a.apk_version}` : "";
  const btn = `<button class="app-refresh">최신 앱 받기</button>`;
  if (up.state === "downloading") {
    el.innerHTML = `⏳ 새 앱 ${ver} 내려받는 중… ${up.percent || 0}%`;
    setTimeout(pollAppUpdate, 1500);
  } else if (up.state === "error") {
    el.innerHTML = `⚠️ 앱을 받지 못했습니다 — ${up.error || ""} ${btn}`;
  } else if (!a.apk_available) {
    el.innerHTML = `ℹ️ 서버에 앱이 없습니다. <code>mobile/APK_URL.txt</code>의 링크에서 받아오거나,
      <code>mobile/README.md</code>대로 빌드해 <code>data/app/photonest-uploader.apk</code>에 두세요. ${btn}`;
  } else if (a.apk_current) {
    el.innerHTML = `📥 <code>${a.apk_url}</code> — 최신 ${ver}`;
  } else {
    el.innerHTML = `📥 <code>${a.apk_url}</code><br>
      ⚠️ 서버의 앱이 최신(${ver})이 아닙니다 — 받아서 폰에 다시 설치하세요. ${btn}`;
  }
  const b = el.querySelector(".app-refresh");
  if (b) b.onclick = async () => {
    b.disabled = true;
    renderAppStatus(await api.post("/api/app-refresh"));
  };
}

async function pollAppUpdate() {
  if (!document.getElementById("app-dl-status")) return;  // 화면이 바뀌면 멈춘다
  try {
    const a = await api.get("/api/app-info");
    const box = document.getElementById("app-qr-box");
    if (a.apk_available && box && !box.childElementCount) {
      views.phone();  // 없던 APK가 생겼다 — QR까지 다시 그린다
      return;
    }
    renderAppStatus(a);
  } catch (_) { /* 무시 */ }
}

/* PC에서 받은 Takeout zip(수 GB) 업로드 — 16MB 조각 + 이어받기.
   한 번에 보내면 잠깐의 끊김에도 처음부터 다시라서, 서버가 받은 지점(offset)을
   확인하며 조각을 이어 붙인다. 같은 파일을 다시 고르면 받은 데까지 건너뛴다. */
const TO_CHUNK = 16 * 1024 * 1024;
const TO_MAX_FILES = 5;

function wireTakeoutUpload() {
  const keep = document.getElementById("to-keep");
  if (!keep) return;
  api.get("/api/settings").then((s) => { keep.checked = s.takeout_keep_zip !== false; });
  keep.onchange = async () => {
    const s = await api.post("/api/settings", { takeout_keep_zip: keep.checked });
    keep.checked = s.takeout_keep_zip !== false;
  };
  document.getElementById("to-pick").onclick = () => document.getElementById("to-input").click();
  document.getElementById("to-input").onchange = (e) => {
    const files = [...e.target.files];
    e.target.value = "";
    if (files.length) uploadTakeoutZips(files);
  };
}

async function toFetchJson(url, opts) {
  const r = await fetch(url, opts);
  let data = {};
  try { data = await r.json(); } catch (_) { /* 본문 없음 */ }
  return { ok: r.ok, status: r.status, data };
}

async function uploadTakeoutZips(files) {
  const $id = (x) => document.getElementById(x);
  const status = (html) => { const el = $id("to-upload-status"); if (el) el.innerHTML = html; };
  const bar = (pct) => { const el = $id("to-progress-bar"); if (el) el.style.width = pct + "%"; };
  const zips = files.filter((f) => f.name.toLowerCase().endsWith(".zip"))
    .sort((a, b) => a.name.localeCompare(b.name));  // -001, -002 … 순서대로
  if (!zips.length) { status("zip 파일만 올릴 수 있습니다."); return; }
  if (zips.length > TO_MAX_FILES) {
    status(`한 번에 최대 ${TO_MAX_FILES}개까지 올릴 수 있습니다 (${zips.length}개 선택됨).`);
    return;
  }
  // 여러 개면 전부 올린 뒤 한 번에 압축 해제 — 업로드와 해제가 SD 카드를 두고 다투지 않게
  const batch = zips.length > 1;

  let cancelled = false;
  let current = null;  // 진행 중인 업로드 id — 중지 시 서버 조각 삭제
  $id("to-pick").disabled = true;
  $id("to-cancel").hidden = false;
  $id("to-progress").hidden = false;
  $id("to-cancel").onclick = () => { cancelled = true; };

  const total = zips.reduce((a, f) => a + f.size, 0);
  let doneBytes = 0;   // 앞서 끝난 파일들의 바이트
  const t0 = Date.now();
  let sentThisRun = 0; // 속도 계산용 (이어받기로 건너뛴 양 제외)
  const done = [];
  const skipped = [];  // 이미 올린 적 있는 zip

  try {
    for (const [i, f] of zips.entries()) {
      const label = `${i + 1}/${zips.length} ${f.name}`;
      const init = await toFetchJson("/api/takeout/upload/init", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: f.name, size: f.size, last_modified: f.lastModified,
          batch_remaining: zips.slice(i).reduce((a, z) => a + z.size, 0),
          batch_total: total,
        }),
      });
      if (!init.ok) throw new Error(init.data.detail || `시작 실패 (${init.status})`);
      if (init.data.already) {  // 이미 올라온 zip — 다시 보내지 않는다
        skipped.push(f.name);
        doneBytes += f.size;
        continue;
      }
      current = init.data.upload_id;
      let offset = init.data.offset;
      if (offset > 0) status(`↻ ${label} — ${fmtBytes(offset)}까지 받은 것을 이어서 올립니다`);

      let fails = 0;
      while (offset < f.size) {
        if (cancelled) throw new Error("cancelled");
        const end = Math.min(offset + TO_CHUNK, f.size);
        let r;
        try {
          r = await toFetchJson(`/api/takeout/upload/${current}?offset=${offset}`, {
            method: "PUT", headers: { "Content-Type": "application/octet-stream" },
            body: f.slice(offset, end),
          });
        } catch (_) {
          r = { ok: false, status: 0, data: {} };  // 네트워크 끊김
        }
        if (r.ok) {
          sentThisRun += r.data.offset - offset;
          offset = r.data.offset;
          fails = 0;
        } else if (r.status === 409 && r.data.detail && r.data.detail.offset != null) {
          offset = r.data.detail.offset;  // 서버가 받은 지점에서 다시
        } else if (r.status === 0 || r.status >= 500) {
          if (++fails > 8) throw new Error("연결이 계속 끊깁니다. 잠시 후 같은 파일을 다시 고르면 이어서 올립니다.");
          status(`⚠️ ${label} — 연결 끊김, 다시 시도 중… (${fails}/8)`);
          await new Promise((res) => setTimeout(res, Math.min(30000, 1000 * 2 ** fails)));
          continue;
        } else {
          throw new Error(r.data.detail || `업로드 실패 (${r.status})`);
        }
        const all = doneBytes + offset;
        const secs = (Date.now() - t0) / 1000;
        const speed = secs > 0 ? sentThisRun / secs : 0;
        const eta = speed > 0 ? (total - all) / speed : 0;
        bar((all / total) * 100);
        status(`⬆️ ${batch ? "1단계 업로드 " : ""}${label} — ${fmtBytes(offset)} / ${fmtBytes(f.size)}` +
          (speed ? ` · ${fmtBytes(speed)}/s · 남은 시간 약 ${Math.ceil(eta / 60)}분` : ""));
      }

      status(`🔍 ${label} — zip 확인 중…`);
      const fin = await toFetchJson(
        `/api/takeout/upload/${current}/complete?process=${batch ? "false" : "true"}`, { method: "POST" });
      if (!fin.ok) throw new Error(fin.data.detail && fin.data.detail.offset == null
        ? fin.data.detail : `마무리 실패 (${fin.status}) — 같은 파일을 다시 골라 주세요.`);
      current = null;
      doneBytes += f.size;
      done.push(fin.data.name);
    }
    bar(100);
    const skipNote = skipped.length
      ? `<br>↷ 이미 올린 적 있는 zip ${skipped.length}개는 건너뛰었습니다: ${skipped.join(", ")}` : "";
    if (!done.length) {
      status(`ℹ️ 새로 올릴 zip이 없습니다.${skipNote}`);
      return;
    }
    if (batch) await toFetchJson("/api/takeout/process", { method: "POST" });
    status(`✅ ${done.length}개 업로드 완료 — 이제 ${batch ? "한 번에 " : ""}압축 해제와 색인을 시작합니다. 사진이 많으면 시간이 걸려요.${skipNote}`);
    speak(`테이크아웃 파일 ${done.length}개를 받았어요. 정리를 시작할게요.`);
    pollTakeoutProcessing();
  } catch (e) {
    // 일괄 업로드가 중간에 멈춰도 다 올라간 zip은 바로 처리한다
    if (batch && done.length) await toFetchJson("/api/takeout/process", { method: "POST" });
    const tail = done.length ? ` 앞서 다 올라간 ${done.length}개는 압축 해제를 시작합니다.` : "";
    if (e.message === "cancelled") {
      if (current) await toFetchJson(`/api/takeout/upload/${current}`, { method: "DELETE" });
      status("⏹ 업로드를 중지했습니다." + tail);
    } else {
      status(`❌ ${e.message}` + tail);
    }
    if (done.length) pollTakeoutProcessing();
  } finally {
    if ($id("to-pick")) {
      $id("to-pick").disabled = false;
      $id("to-cancel").hidden = true;
    }
  }
}

/* 업로드 뒤 압축 해제·색인 진행 상황 표시 — 화면을 떠나면 멈춘다 */
async function pollTakeoutProcessing(seenBusy = false, tries = 0) {
  const el = document.getElementById("takeout-status");
  if (!el) return;
  try {
    const s = await api.get("/api/status");
    const busy = s.takeout_running || s.indexing;
    // 색인 스레드가 막 뜨는 중일 수 있다 — 한 번도 바쁜 걸 못 봤으면 몇 번 더 본다
    if (!busy && !seenBusy && tries < 3) {
      setTimeout(() => pollTakeoutProcessing(false, tries + 1), 2000);
      return;
    }
    seenBusy = seenBusy || busy;
    if (s.takeout_running) {
      el.textContent = `📦 ${s.takeout_phase || "압축 해제"} 중… 새 사진 ${s.takeout_added || 0}개`;
    } else if (s.indexing) {
      el.textContent = `🗂️ 색인 중… ${s.phase || ""} ${s.total ? `${s.done}/${s.total}` : ""}`;
    } else {
      el.textContent = "✅ 압축 해제와 색인이 끝났습니다.";
      renderTakeoutAlbums();  // 새 앨범 폴더가 생겼을 수 있다
      renderTakeoutDone();
      renderTakeoutHistory();
      return;
    }
  } catch (_) { /* 잠시 뒤 다시 */ }
  setTimeout(() => pollTakeoutProcessing(seenBusy, tries), 3000);
}

/* 구글포토 앨범 폴더 감지 → 사용자에게 묻고 앨범 생성 */
async function renderTakeoutAlbums() {
  const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const box = document.getElementById("takeout-albums");
  if (!box) return;
  try {
    const { albums } = await api.get("/api/takeout/albums");
    if (!albums.length) return;
    const newN = albums.filter((a) => !a.album_exists).length;
    const rows = albums.map((a) =>
      `<label class="ta-row"><input type="checkbox" class="ta-item" data-name="${encodeURIComponent(a.name)}" checked>
       <span>${esc(a.name)}</span>
       <small>${a.count}장${a.album_exists ? " · 이미 있음(사진만 보충)" : ""}</small></label>`
    ).join("");
    box.innerHTML =
      `<div class="ta-box">
        <strong>📁 구글포토 앨범 폴더 ${albums.length}개 발견${newN ? ` (새 앨범 ${newN}개)` : ""}</strong>
        <p class="small">테이크아웃의 폴더 구조가 앨범으로 묶여 있어요. 선택한 폴더를
        PhotoNest 앨범으로 만들까요? (연도별 자동 폴더는 제외됨)</p>
        <label class="ta-row ta-all"><input type="checkbox" id="ta-all" checked>
          <span>전체 선택</span><small id="ta-count"></small></label>
        <div class="ta-list">${rows}</div>
        <div class="ta-actions">
          <button id="ta-apply">✅ 선택한 앨범 만들기</button>
          <span id="ta-status" class="small"></span>
        </div>
      </div>`;
    const items = [...box.querySelectorAll(".ta-item")];
    const all = document.getElementById("ta-all");
    const sync = () => {  // 일부만 선택되면 '전체 선택'을 반쯤 체크로
      const n = items.filter((x) => x.checked).length;
      all.checked = n === items.length;
      all.indeterminate = n > 0 && n < items.length;
      document.getElementById("ta-count").textContent = `${n}/${items.length}개 선택`;
    };
    all.onchange = () => { items.forEach((x) => { x.checked = all.checked; }); sync(); };
    items.forEach((x) => { x.onchange = sync; });
    sync();
    document.getElementById("ta-apply").onclick = async () => {
      const names = items.filter((x) => x.checked)
        .map((x) => decodeURIComponent(x.dataset.name));
      if (!names.length) {
        document.getElementById("ta-status").textContent = "선택된 앨범이 없습니다.";
        return;
      }
      document.getElementById("ta-status").textContent = "생성 중…";
      const r = await api.post("/api/takeout/albums/apply", { names });
      document.getElementById("ta-status").textContent =
        `✅ 새 앨범 ${r.created}개 · 기존 앨범 보충 ${r.updated}개 · 사진 ${r.added}장 추가됨`;
      speak(`앨범 ${r.created + r.updated}개를 정리했어요.`);
      renderTakeoutAlbums(); // '이미 있음' 상태 갱신
    };
  } catch (_) { /* 무시 */ }
}

/* 처리 끝나 _zips_done에 보관 중인 zip — 사진은 이미 풀려 있어 지워도 된다 */
async function renderTakeoutDone() {
  const box = document.getElementById("takeout-done");
  if (!box) return;
  const esc = (t) => t.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  try {
    const { zips, total } = await api.get("/api/takeout/done");
    if (!zips.length) { box.innerHTML = ""; return; }
    box.innerHTML =
      `<div class="ta-box">
        <strong>🗄️ 보관 중인 zip ${zips.length}개 · ${fmtBytes(total)}</strong>
        <p class="small">압축 해제가 끝난 원본입니다. 지워도 사진·앨범·색인은 그대로 남습니다.</p>
        <div class="ta-list">${zips.map((z) =>
          `<div class="ta-row td-row"><span>${esc(z.name)}</span>
           <small>${fmtBytes(z.size)} · ${z.mtime.slice(0, 10)}</small>
           <button class="pn-btn td-del" data-name="${encodeURIComponent(z.name)}">삭제</button></div>`).join("")}
        </div>
        <div class="ta-actions">
          <button class="pn-btn" id="td-all">🗑 전부 삭제 (${fmtBytes(total)} 확보)</button>
          <span id="td-status" class="small"></span>
        </div>
      </div>`;
    const del = async (names, label) => {
      if (!confirm(`${label}을(를) 삭제할까요? 되돌릴 수 없습니다.\n(풀려 있는 사진은 그대로 남습니다)`)) return;
      const r = await api.post("/api/takeout/done/delete", { names });
      document.getElementById("td-status").textContent =
        `✅ ${r.deleted}개 삭제 · ${fmtBytes(r.freed)} 확보`;
      renderTakeoutDone();
      renderTakeoutHistory();
    };
    box.querySelectorAll(".td-del").forEach((b) => {
      b.onclick = () => {
        const name = decodeURIComponent(b.dataset.name);
        del([name], name);
      };
    });
    document.getElementById("td-all").onclick = () => del(null, `보관 zip ${zips.length}개 전부`);
  } catch (_) { /* 무시 */ }
}

/* 처리 내역 — zip 파일별로 언제·어떻게 받아 어떻게 처리했는지 */
async function renderTakeoutHistory() {
  const box = document.getElementById("takeout-history");
  if (!box) return;
  const esc = (t) => String(t).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const when = (t) => (t ? esc(t.slice(5, 16)) : "");
  try {
    const { imports } = await api.get("/api/takeout/history");
    if (!imports.length) { box.innerHTML = ""; return; }
    const row = (imp) => {
      const result = imp.status === "queued" ? "⏳ 처리 대기"
        : imp.status === "error" ? `❌ 실패 — ${esc(imp.error || "")}`
        : `✅ 새로 추가 ${imp.added.toLocaleString()}개` +
          (imp.existing != null
            ? ` · 이미 있음 ${imp.existing.toLocaleString()}개`
            : imp.skipped ? ` · 건너뜀 ${imp.skipped.toLocaleString()}개 파일` : "");
      const zip = imp.zip_state === "kept"
        ? `zip 보관 중${imp.kept_size ? ` (${fmtBytes(imp.kept_size)})` : ""}`
        : imp.zip_state === "deleted" ? "zip 삭제됨" : "zip 대기 중";
      const times = [
        imp.uploaded_at ? `${imp.source === "upload" ? "업로드" : "넣음"} ${when(imp.uploaded_at)}` : "",
        imp.processed_at ? `처리 ${when(imp.processed_at)}` : "",
      ].filter(Boolean).join(" → ");
      return `<div class="th-item">
        <div class="th-head"><strong>${esc(imp.zip_name)}</strong>
          <small>${imp.size ? fmtBytes(imp.size) : ""}${imp.source ? ` · ${imp.source === "upload" ? "웹 업로드" : "폴더에 직접"}` : ""}</small></div>
        <div class="small">${result}</div>
        <div class="small th-meta">${times}${times ? " · " : ""}${zip}</div>
      </div>`;
    };
    box.innerHTML =
      `<div class="ta-box">
        <strong>📜 테이크아웃 처리 내역 ${imports.length}건</strong>
        ${imports.map(row).join("")}
      </div>`;
  } catch (_) { /* 무시 */ }
}

/* ---------------- 저장소·백업 ---------------- */
const MEDIA_EXTS = new Set([
  "jpg", "jpeg", "png", "webp", "bmp", "gif", "heic", "heif",
  "mp4", "mov", "avi", "mkv", "webm",
]);

function fmtBytes(n) {
  if (n == null) return "-";
  if (n >= 1e9) return (n / 1e9).toFixed(n >= 1e10 ? 0 : 1) + "GB";
  if (n >= 1e6) return (n / 1e6).toFixed(0) + "MB";
  return Math.max(1, Math.round(n / 1e3)) + "KB";
}

views.storage = async () => {
  const c = $("#content");
  c.innerHTML = `
  <div class="phone-wrap">
    <section class="phone-card">
      <h3>📊 보관 통계</h3>
      <div class="stat-grid" id="st-grid"><span class="loading-ph">계산 중…</span></div>
      <div class="disk-bar"><div class="disk-used" id="st-disk-bar"></div></div>
      <p class="small" id="st-disk-text"></p>
    </section>
    <section class="phone-card">
      <h3>📤 PC 폴더 업로드</h3>
      <p>PC의 사진 폴더를 선택하면 하위 폴더까지 포함해 <strong>원본 그대로(화질·촬영정보·GPS 무손실)</strong>
      서버로 복사되고 자동으로 색인됩니다. 이미 서버에 있는 사진(중복)은 건너뜁니다.</p>
      <div class="btn-row">
        <button class="pn-btn primary" id="pc-pick-folder">📁 폴더 선택해서 올리기</button>
        <button class="pn-btn" id="pc-pick-files">🖼️ 파일 골라서 올리기</button>
      </div>
      <input type="file" id="pc-folder-input" webkitdirectory multiple hidden>
      <input type="file" id="pc-file-input" multiple accept="image/*,video/*" hidden>
      <div id="pc-summary" class="small"></div>
      <div class="pn-progress" id="pc-progress" hidden><div class="pn-progress-bar" id="pc-progress-bar"></div></div>
      <p class="small">저장 위치: <code>photos/MobileBackup/PC/…</code> (선택한 폴더 구조 유지)</p>
    </section>
    <section class="phone-card">
      <h3>🔌 USB 백업</h3>
      <p>USB를 꽂고 🔄 새로고침 후 드라이브를 골라 백업하세요. 사진 라이브러리 전체와
      색인 DB(앨범·인물·즐겨찾기 포함)를 복사하며, <strong>다시 실행하면 바뀐 파일만</strong> 증분 복사합니다.</p>
      <div class="btn-row">
        <select id="usb-target"></select>
        <button class="pn-btn" id="usb-refresh" title="드라이브 다시 찾기">🔄</button>
        <button class="pn-btn primary" id="usb-start">💾 백업 시작</button>
        <button class="pn-btn" id="usb-cancel" hidden>⏹ 중지</button>
      </div>
      <div id="usb-status" class="small"></div>
      <div class="pn-progress" id="usb-progress" hidden><div class="pn-progress-bar" id="usb-progress-bar"></div></div>
    </section>
  </div>`;

  renderStorageStats();
  wirePcUpload();
  wireUsbBackup();
};

async function renderStorageStats() {
  try {
    const s = await api.get("/api/storage/stats");
    const total = s.photos.count + s.videos.count;
    const tiles = [
      ["🖼️ 사진", `${s.photos.count.toLocaleString()}장`, fmtBytes(s.photos.bytes)],
      ["🎬 동영상", `${s.videos.count.toLocaleString()}개`, fmtBytes(s.videos.bytes)],
      ["📦 전체", `${total.toLocaleString()}개`, fmtBytes(s.photos.bytes + s.videos.bytes)],
      ["🗑️ 휴지통", `${s.trash.count.toLocaleString()}개`, fmtBytes(s.trash.bytes)],
      ["🧠 앱 데이터", "썸네일·색인", fmtBytes(s.data_bytes)],
    ];
    $("#st-grid").innerHTML = tiles.map(([label, num, sub]) =>
      `<div class="stat-tile"><span class="stat-label">${label}</span>
       <span class="stat-num">${num}</span><span class="stat-sub">${sub}</span></div>`).join("");
    const pct = s.disk.total ? (s.disk.used / s.disk.total * 100) : 0;
    $("#st-disk-bar").style.width = pct.toFixed(1) + "%";
    $("#st-disk-bar").classList.toggle("warn", pct >= 90);
    $("#st-disk-text").textContent =
      `라이브러리 드라이브: 전체 ${fmtBytes(s.disk.total)} 중 ${fmtBytes(s.disk.used)} 사용 (${pct.toFixed(0)}%) · 가용 ${fmtBytes(s.disk.free)}`;
  } catch (_) {
    $("#st-grid").innerHTML = `<span class="small">통계를 불러오지 못했습니다</span>`;
  }
}

function wirePcUpload() {
  $("#pc-pick-folder").onclick = () => $("#pc-folder-input").click();
  $("#pc-pick-files").onclick = () => $("#pc-file-input").click();
  $("#pc-folder-input").onchange = (e) => uploadPcFiles([...e.target.files], e.target);
  $("#pc-file-input").onchange = (e) => uploadPcFiles([...e.target.files], e.target);
}

async function uploadPcFiles(all, inputEl) {
  inputEl.value = "";
  const files = all.filter((f) =>
    MEDIA_EXTS.has((f.name.split(".").pop() || "").toLowerCase()));
  const ignored = all.length - files.length;
  if (!files.length) {
    $("#pc-summary").textContent = "선택한 항목에 사진·동영상이 없습니다.";
    return;
  }
  $("#pc-pick-folder").disabled = $("#pc-pick-files").disabled = true;
  $("#pc-progress").hidden = false;

  let done = 0, saved = 0, dup = 0, err = 0;
  const update = () => {
    $("#pc-progress-bar").style.width = (done / files.length * 100) + "%";
    $("#pc-summary").innerHTML =
      `<strong>${done}/${files.length}</strong> 완료 · 저장 ${saved} · 중복 ${dup}` +
      (err ? ` · <span style="color:#ff5c7a">실패 ${err}</span>` : "") +
      (ignored ? ` · 미디어 아닌 파일 ${ignored}개 제외` : "");
  };
  update();

  const queue = [...files];
  async function worker() {
    while (queue.length) {
      const f = queue.shift();
      try {
        const fd = new FormData();
        fd.append("files", f, f.name);
        fd.append("device", "PC");
        // 폴더 선택 시 하위 폴더 구조 보존 (파일 선택은 바로 아래에 저장)
        const rel = f.webkitRelativePath || "";
        const dir = rel.includes("/") ? rel.slice(0, rel.lastIndexOf("/")) : "";
        if (dir) fd.append("subdir", dir);
        fd.append("meta", JSON.stringify({ [f.name]: f.lastModified }));
        const r = await fetch("/api/upload", { method: "POST", body: fd });
        const data = await r.json();
        const st = (data.results && data.results[0] && data.results[0].status) || "error";
        if (st === "saved") saved++;
        else if (st === "duplicate") dup++;
        else err++;
      } catch (_) { err++; }
      done++;
      update();
    }
  }
  await Promise.all([worker(), worker(), worker()]);
  $("#pc-summary").innerHTML += " — 완료! 새 사진은 자동으로 색인됩니다.";
  $("#pc-pick-folder").disabled = $("#pc-pick-files").disabled = false;
  renderStorageStats();
}

function wireUsbBackup() {
  const sel = $("#usb-target");
  const loadTargets = async () => {
    const { targets } = await api.get("/api/backup/targets");
    sel.innerHTML = targets.length
      ? targets.map((t) =>
          `<option value="${t.path}"${t.writable ? "" : " disabled"}>` +
          `${t.name} — 남은 공간 ${fmtBytes(t.free)}${t.writable ? "" : " (쓰기 불가)"}</option>`).join("")
      : `<option value="">USB가 감지되지 않았습니다</option>`;
    $("#usb-start").disabled = !targets.some((t) => t.writable);
  };
  loadTargets();
  $("#usb-refresh").onclick = loadTargets;

  $("#usb-start").onclick = async () => {
    if (!sel.value) return;
    const r = await api.post("/api/backup/start", { path: sel.value });
    if (!r.ok) { $("#usb-status").textContent = "⚠️ " + r.error; return; }
    pollBackup();
  };
  $("#usb-cancel").onclick = () => api.post("/api/backup/cancel");

  // 뷰 진입 시 이미 진행 중이던 백업이 있으면 이어서 표시
  api.get("/api/backup/status").then((s) => {
    if (s.state === "preparing" || s.state === "running") pollBackup();
  }).catch(() => {});
}

async function pollBackup() {
  if (state.view !== "storage") return; // 다른 화면으로 이동하면 폴링 중단
  let s;
  try { s = await api.get("/api/backup/status"); } catch (_) { return; }
  const running = s.state === "preparing" || s.state === "running";
  $("#usb-start").disabled = running;
  $("#usb-cancel").hidden = !running;
  $("#usb-progress").hidden = !running && s.state !== "done";

  if (s.state === "preparing") {
    $("#usb-status").textContent = "백업 준비 중… (바뀐 파일 확인)";
  } else if (s.state === "running") {
    const pct = s.total_bytes ? (s.done_bytes / s.total_bytes * 100) : 0;
    $("#usb-progress-bar").style.width = pct.toFixed(1) + "%";
    $("#usb-status").textContent =
      `복사 중 ${fmtBytes(s.done_bytes)}/${fmtBytes(s.total_bytes)} · ` +
      `새로 복사 ${s.copied} · 이미 최신 ${s.skipped}` +
      (s.failed ? ` · 실패 ${s.failed}` : "") +
      (s.current ? ` — ${s.current}` : "");
  } else if (s.state === "done") {
    $("#usb-progress-bar").style.width = "100%";
    $("#usb-status").textContent =
      `✅ 백업 완료 — 새로 복사 ${s.copied}개 · 이미 최신 ${s.skipped}개` +
      (s.failed ? ` · 실패 ${s.failed}개` : "") + ` → ${s.dest}/PhotoNestBackup`;
    return;
  } else if (s.state === "cancelled") {
    $("#usb-status").textContent = "⏹ 백업을 중지했습니다. 다시 시작하면 이어서 복사됩니다.";
    return;
  } else if (s.state === "error") {
    $("#usb-status").textContent = "⚠️ " + (s.error || "백업 실패");
    return;
  } else return;
  setTimeout(pollBackup, 1000);
}

async function cleanAllDuplicates() {
  const { groups } = await api.get("/api/duplicates");
  if (!groups.length) { speak("정리할 중복 사진이 없어요."); return; }
  const extra = groups.reduce((s, g) => s + g.length - 1, 0);
  if (!confirm(`중복 ${groups.length}그룹에서 각각 첫 장만 남기고 ${extra}장을 휴지통으로 옮길까요?\n(휴지통에서 복원할 수 있습니다)`)) return;
  const r = await api.post("/api/duplicates/clean");
  speak(`중복 ${r.groups}그룹을 정리해서 ${r.trashed}장을 휴지통으로 옮겼어요.`);
  if (state.view === "duplicates") switchView("duplicates");
}

/* ---------------- 공통 ---------------- */
function addTopbarBtn(label, onClick) {
  const b = document.createElement("button");
  b.className = "topbar-btn";
  b.textContent = label;
  b.onclick = onClick;
  $("#topbar-actions").appendChild(b);
}
