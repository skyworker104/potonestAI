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
views.map = async () => {
  const { items } = await api.get("/api/geo");
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
  const cluster = L.markerClusterGroup();
  const bounds = [];
  items.forEach((it, i) => {
    const icon = L.divIcon({
      className: "photo-marker",
      html: `<img src="/thumbs/${it.id}.jpg">`,
      iconSize: [52, 52],
    });
    const mk = L.marker([it.lat, it.lon], { icon });
    mk.on("click", () => openLightbox(i));
    cluster.addLayer(mk);
    bounds.push([it.lat, it.lon]);
  });
  geoMap.addLayer(cluster);
  geoMap.fitBounds(bounds, { padding: [40, 40] });
};

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

/* ---------------- 중복 정리 ---------------- */
views.duplicates = async () => {
  const { groups } = await api.get("/api/duplicates");
  $("#view-count").textContent = groups.length ? `${groups.length}그룹` : "";
  const c = $("#content");
  if (!groups.length) {
    c.innerHTML = `<div class="empty">중복된 사진이 없습니다 🎉</div>`;
    return;
  }
  addTopbarBtn("🧹 중복 모두 지우기", cleanAllDuplicates);
  state.currentItems = groups.flat();
  let idx = 0;
  groups.forEach((g, gi) => {
    const sec = document.createElement("div");
    sec.className = "dup-group";
    const head = document.createElement("div");
    head.className = "dup-head";
    head.innerHTML = `<h4>그룹 ${gi + 1} · ${g.length}장 동일</h4>`;
    const btn = document.createElement("button");
    btn.textContent = "첫 장만 남기고 휴지통으로";
    btn.onclick = async () => {
      for (const it of g.slice(1)) await api.post(`/api/media/${it.id}/trash`);
      speak(`${g.length - 1}장을 휴지통으로 옮겼어요.`);
      switchView("duplicates");
    };
    head.appendChild(btn);
    sec.appendChild(head);
    const grid = document.createElement("div");
    grid.className = "group-grid";
    g.forEach((it) => {
      const myIdx = idx++;
      const t = makeTile(it, myIdx);
      grid.appendChild(t);
    });
    sec.appendChild(grid);
    c.appendChild(sec);
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
        <li>받은 <code>takeout-*.zip</code> 파일을 <code>photos/</code> 폴더에 넣기</li>
        <li>끝! 자동으로 압축을 풀고 GPS·날짜를 살려 색인합니다
            (처리된 zip은 <code>photos/_zips_done/</code>으로 옮겨집니다)</li>
      </ol>
      <div id="takeout-status" class="small"></div>
      <div id="takeout-albums"></div>
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
      `<label class="ta-row"><input type="checkbox" data-name="${encodeURIComponent(a.name)}"${a.album_exists ? "" : " checked"}>
       <span>${esc(a.name)}</span>
       <small>${a.count}장${a.album_exists ? " · 이미 있음(사진만 보충)" : ""}</small></label>`
    ).join("");
    box.innerHTML =
      `<div class="ta-box">
        <strong>📁 구글포토 앨범 폴더 ${albums.length}개 발견${newN ? ` (새 앨범 ${newN}개)` : ""}</strong>
        <p class="small">테이크아웃의 폴더 구조가 앨범으로 묶여 있어요. 선택한 폴더를
        PhotoNest 앨범으로 만들까요? (연도별 자동 폴더는 제외됨)</p>
        <div class="ta-list">${rows}</div>
        <div class="ta-actions">
          <button id="ta-apply">✅ 선택한 앨범 만들기</button>
          <span id="ta-status" class="small"></span>
        </div>
      </div>`;
    document.getElementById("ta-apply").onclick = async () => {
      const names = [...box.querySelectorAll("input:checked")]
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
