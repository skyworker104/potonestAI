/* PhotoNest AI — 라이트박스(상세보기, ℹ️ 토글 정보·지도) & 슬라이드쇼 */

let lbIndex = -1;
let lbMap = null;
let lbInfoVisible = false;

function openLightbox(index) {
  if (index < 0 || index >= state.currentItems.length) return;
  lbIndex = index;
  const it = state.currentItems[index];
  const media = $("#lb-media");
  media.innerHTML =
    it.type === "video"
      ? `<video src="/media/${encodeURIComponent(it.path)}" controls autoplay></video>`
      : `<img src="/media/${encodeURIComponent(it.path)}" alt="">`;

  $("#lb-fav").textContent = it.favorite ? "⭐" : "☆";
  $("#lightbox").hidden = false;
  // 기본은 사진만 크게 — 정보 패널은 ℹ️로 열기 (열려 있었다면 내용 갱신)
  if (lbInfoVisible) renderLbInfo();
  else setLbInfo(false);
}

function setLbInfo(show) {
  lbInfoVisible = show;
  $("#lb-body").classList.toggle("show-info", show);
  $("#lb-info-btn").classList.toggle("on", show);
  if (!show && lbMap) { lbMap.remove(); lbMap = null; }
  if (show) renderLbInfo();
}

function renderLbInfo() {
  const it = currentLbItem();
  if (!it) return;
  $("#lb-name").textContent = it.path || "(휴지통)";
  const d = new Date(it.taken_at);
  $("#lb-date").textContent =
    `📅 ${dateLabel(it.taken_at)} ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}` +
    (it.type === "video" && it.duration ? ` · ⏱ ${it.duration}초` : "");

  renderExif(it);
  renderComment(it);
  renderMap(it);
}

let lbMarker = null;
let lbEditMode = false;
let lbPending = null;  // 편집 중 임시 좌표 {lat, lon}

function renderMap(it) {
  const mapDiv = $("#lb-map");
  if (lbMap) { lbMap.remove(); lbMap = null; }
  lbMarker = null; lbPending = null;
  const hasGps = it.lat != null && it.lon != null;
  lbEditMode = !hasGps;  // 위치 없으면 바로 지정 가능

  mapDiv.className = "";
  mapDiv.innerHTML = "";
  const center = hasGps ? [it.lat, it.lon] : [36.5, 127.8]; // 없으면 한국 중심
  lbMap = L.map(mapDiv).setView(center, hasGps ? 13 : 6);
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: "© OpenStreetMap",
  }).addTo(lbMap);
  if (hasGps) {
    lbMarker = L.marker(center).addTo(lbMap).bindPopup("촬영 위치").openPopup();
  }
  setTimeout(() => lbMap && lbMap.invalidateSize(), 80);

  // 지도 클릭 → 위치 지정(편집 모드일 때만)
  lbMap.on("click", (e) => {
    if (!lbEditMode) return;
    lbPending = { lat: e.latlng.lat, lon: e.latlng.lng };
    if (!lbMarker) lbMarker = L.marker(e.latlng, { draggable: true }).addTo(lbMap);
    else lbMarker.setLatLng(e.latlng);
    lbMarker.on("dragend", (ev) => {
      const p = ev.target.getLatLng();
      lbPending = { lat: p.lat, lon: p.lng };
    });
    updateLocBar(it);
  });

  updateLocBar(it);
}

function updateLocBar(it) {
  const bar = $("#lb-loc");
  const hasGps = it.lat != null && it.lon != null;
  let html = "";
  if (hasGps) {
    // 역지오코딩된 지명이 있으면 좌표 대신 사람이 읽는 이름을 우선 표시
    const place = (it.place_name || "").split(" ").filter(t => /[가-힣]/.test(t)).slice(0, 2).join(" ");
    html = `📍 ${place ? place + " · " : ""}위도 ${it.lat.toFixed(5)}, 경도 ${it.lon.toFixed(5)} ` +
           `· <a class="loc-link" id="loc-edit">위치 수정</a>`;
  } else if (lbEditMode) {
    html = lbPending
      ? `📍 선택한 위치: ${lbPending.lat.toFixed(5)}, ${lbPending.lon.toFixed(5)}`
      : `📍 위치 정보 없음 — 지도를 클릭해 촬영 위치를 지정하세요`;
  }
  if (lbEditMode && lbPending) {
    html += ` · <a class="loc-link" id="loc-save">이 위치로 저장</a>`;
  } else if (lbEditMode && !hasGps) {
    html += ``;
  }
  if (hasGps && lbEditMode && lbPending) {
    html = `📍 선택한 위치: ${lbPending.lat.toFixed(5)}, ${lbPending.lon.toFixed(5)} · ` +
           `<a class="loc-link" id="loc-save">저장</a> · <a class="loc-link" id="loc-cancel">취소</a>`;
  }
  bar.innerHTML = html;

  const edit = $("#loc-edit");
  if (edit) edit.onclick = () => { lbEditMode = true; updateLocBar(it); };
  const save = $("#loc-save");
  if (save) save.onclick = () => saveLocation(it);
  const cancel = $("#loc-cancel");
  if (cancel) cancel.onclick = () => { lbEditMode = false; lbPending = null; renderMap(it); };
}

async function saveLocation(it) {
  if (!lbPending) return;
  await api.post(`/api/media/${it.id}/location`, lbPending);
  it.lat = lbPending.lat; it.lon = lbPending.lon;  // 로컬 상태 갱신
  lbEditMode = false; lbPending = null;
  renderMap(it);
  speak("촬영 위치를 저장했어요. 이제 지도와 지역 검색에 나타나요.");
}

// 사진별로 상세 EXIF를 한 번만 불러와 캐시
const exifCache = {};
let exifReqToken = 0;

async function renderExif(it) {
  const box = $("#lb-exif");
  if (it.type === "video") {
    box.innerHTML = `<div class="exif-line">📐 ${it.width}×${it.height}</div>`;
    return;
  }
  const token = ++exifReqToken;
  box.innerHTML = `<div class="exif-loading">상세 정보 불러오는 중…</div>`;
  let det = exifCache[it.id];
  if (!det) {
    try {
      det = (await api.get(`/api/media/${it.id}/details`)).details || {};
      exifCache[it.id] = det;
    } catch (_) { det = {}; }
  }
  if (token !== exifReqToken) return; // 그 사이 다른 사진으로 이동

  // 값들을 구분자(·)로 이어 한 줄(공간 절약)로 표기
  const parts = [];
  if (det.camera) parts.push(det.camera);
  if (det.lens && det.lens !== det.camera) parts.push(det.lens);
  if (it.width && it.height) parts.push(`${it.width}×${it.height}`);
  const shot = [det.aperture, det.shutter, det.iso, det.focal].filter(Boolean).join(" · ");
  if (shot) parts.push(shot);
  if (det.focal35 && det.focal35 !== det.focal) parts.push(`환산 ${det.focal35}`);
  if (det.flash) parts.push(`플래시 ${det.flash}`);
  if (det.exposure_bias) parts.push(det.exposure_bias);
  if (det.filesize) parts.push(det.filesize);

  box.innerHTML = parts.length
    ? `<div class="exif-line">📷 ${parts.join(" · ")}</div>`
    : `<div class="exif-empty">추가 촬영 정보가 없는 사진입니다</div>`;
}

function renderComment(it) {
  const ta = $("#lb-comment");
  ta.value = it.comment || "";
  $("#lb-comment-status").textContent = "";
}

async function saveComment() {
  const it = currentLbItem();
  if (!it) return;
  const text = $("#lb-comment").value.trim();
  const status = $("#lb-comment-status");
  status.textContent = "저장 중…";
  try {
    await api.post(`/api/media/${it.id}/comment`, { comment: text });
    it.comment = text; // 로컬 상태도 갱신
    status.textContent = "✅ 저장됨";
    setTimeout(() => (status.textContent = ""), 2000);
  } catch (_) {
    status.textContent = "저장 실패";
  }
}

$("#lb-comment-save").onclick = saveComment;
// Ctrl/Cmd+Enter로 빠르게 저장
$("#lb-comment").addEventListener("keydown", (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key === "Enter") { e.preventDefault(); saveComment(); }
});

function closeLightbox() {
  $("#lightbox").hidden = true;
  if (lbMap) { lbMap.remove(); lbMap = null; }
  $("#lb-media").innerHTML = ""; // 동영상 재생 중지
  lbIndex = -1;
}

function nextPhoto() {
  if (lbIndex >= 0) openLightbox((lbIndex + 1) % state.currentItems.length);
}
function prevPhoto() {
  if (lbIndex >= 0) openLightbox((lbIndex - 1 + state.currentItems.length) % state.currentItems.length);
}

function currentLbItem() {
  return lbIndex >= 0 ? state.currentItems[lbIndex] : null;
}

async function toggleFavoriteCurrent() {
  const it = currentLbItem();
  if (!it) return false;
  it.favorite = it.favorite ? 0 : 1;
  await api.post(`/api/media/${it.id}/favorite`, { value: !!it.favorite });
  $("#lb-fav").textContent = it.favorite ? "⭐" : "☆";
  return !!it.favorite;
}

async function trashCurrent() {
  const it = currentLbItem();
  if (!it) return false;
  await api.post(`/api/media/${it.id}/trash`);
  state.currentItems.splice(lbIndex, 1);
  if (!state.currentItems.length) { closeLightbox(); refreshView(); }
  else openLightbox(lbIndex % state.currentItems.length);
  return true;
}

$("#lb-close").onclick = closeLightbox;
$("#lb-next").onclick = nextPhoto;
$("#lb-prev").onclick = prevPhoto;
$("#lb-fav").onclick = toggleFavoriteCurrent;
$("#lb-info-btn").onclick = () => setLbInfo(!lbInfoVisible);
$("#lb-trash").onclick = async () => { await trashCurrent(); speak("휴지통으로 옮겼어요."); };
$("#lb-album").onclick = () => {
  const it = currentLbItem();
  if (it) openAlbumModal([it.id]);
};
$("#lb-slideshow").onclick = () => startSlideshow(lbIndex);

document.addEventListener("keydown", (e) => {
  if (!$("#slideshow").hidden) {
    if (e.key === "Escape") stopSlideshow();
    return;
  }
  if ($("#lightbox").hidden) return;
  if (e.key === "Escape") closeLightbox();
  if (e.key === "ArrowRight") nextPhoto();
  if (e.key === "ArrowLeft") prevPhoto();
  if (e.key === "i" || e.key === "I") setLbInfo(!lbInfoVisible);
});

/* ---------------- 슬라이드쇼 ---------------- */
let ssIndex = 0;
let ssTimer = null;
let ssPaused = false;

function ssShow(i) {
  const items = state.currentItems.filter((it) => it.type === "image");
  if (!items.length) return;
  ssIndex = ((i % items.length) + items.length) % items.length;
  const img = $("#ss-img");
  img.classList.remove("kenburns");
  void img.offsetWidth; // 애니메이션 재시작
  img.src = `/media/${encodeURIComponent(items[ssIndex].path)}`;
  img.classList.add("kenburns");
}

function startSlideshow(fromIndex = 0) {
  const items = state.currentItems.filter((it) => it.type === "image");
  if (!items.length) { speak("슬라이드쇼로 보여줄 사진이 없어요."); return; }
  closeLightbox();
  $("#slideshow").hidden = false;
  ssPaused = false;
  $("#ss-pause").textContent = "⏸";
  ssShow(Math.max(fromIndex, 0));
  clearInterval(ssTimer);
  ssTimer = setInterval(() => { if (!ssPaused) ssShow(ssIndex + 1); }, 4000);
  if (document.documentElement.requestFullscreen) {
    document.documentElement.requestFullscreen().catch(() => {});
  }
}

function stopSlideshow() {
  $("#slideshow").hidden = true;
  clearInterval(ssTimer);
  ssTimer = null;
  if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
}

$("#ss-exit").onclick = stopSlideshow;
$("#ss-next").onclick = () => ssShow(ssIndex + 1);
$("#ss-prev").onclick = () => ssShow(ssIndex - 1);
$("#ss-pause").onclick = () => {
  ssPaused = !ssPaused;
  $("#ss-pause").textContent = ssPaused ? "▶" : "⏸";
};
