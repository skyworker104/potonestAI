/** 백업 핵심 로직 — 대상 사진 수집(GPS 보존), 순차 업로드, 진행 콜백. */
import * as MediaLibrary from "expo-media-library";
import { uploadAsset } from "./api";
import { loadConfig, markUploaded } from "./storage";

let _cancel = false;
export function cancelBackup() { _cancel = true; }

/**
 * 백업 대상 asset 수집.
 * opts.range: 'all' | 'recent'(count), opts.media: 'image'|'video'|undefined
 * onlyAfter: epoch(ms) — 자동 백업 시 마지막 백업 이후 생성분만.
 */
export async function collectAssets({ range = "all", count = 50, media, onlyAfter = 0, albums = [] } = {}) {
  const mediaType =
    media === "video" ? ["video"] : media === "image" ? ["photo"] : ["photo", "video"];
  const first = range === "recent" ? Math.min(count, 100) : 100;
  // 선택된 앨범이 있으면 그 앨범들만, 없으면 전체 라이브러리(album=undefined)
  const albumIds = albums.length ? albums.map((a) => a.id) : [null];

  let assets = [];
  for (const albumId of albumIds) {
    let cursor;
    while (true) {
      const page = await MediaLibrary.getAssetsAsync({
        mediaType, first, after: cursor,
        album: albumId || undefined,
        sortBy: [MediaLibrary.SortBy.creationTime],
      });
      assets.push(...page.assets);
      if (!page.hasNextPage) break;
      cursor = page.endCursor;
      if (assets.length > 20000) break; // 안전 상한
    }
  }
  // 여러 앨범에 같은 사진이 들어갈 수 있어 중복 제거
  const seen = new Set();
  assets = assets.filter((a) => (seen.has(a.id) ? false : seen.add(a.id)));
  if (onlyAfter) {
    assets = assets.filter((a) => (a.creationTime || 0) * 1000 > onlyAfter);
  }
  // 최신순 정렬 + 최근 N장 제한
  assets.sort((a, b) => (b.creationTime || 0) - (a.creationTime || 0));
  if (range === "recent") assets = assets.slice(0, count);
  return assets;
}

/**
 * 백업 실행. onProgress({done,total,saved,duplicate,error}) 콜백.
 * 각 asset은 getAssetInfoAsync로 localUri(원본, GPS 포함)를 얻어 업로드한다.
 */
export async function runBackup(opts = {}, onProgress = () => {}) {
  _cancel = false;
  const cfg = await loadConfig();
  if (!cfg.serverUrl) throw new Error("서버 미연결");

  const scope = opts.scope || cfg.scope || { range: "all" };
  const assets = await collectAssets({
    ...scope, media: opts.media, onlyAfter: opts.onlyAfter || 0,
    albums: cfg.albums || [],
  });

  const stat = { done: 0, total: assets.length, saved: 0, duplicate: 0, error: 0 };
  onProgress({ ...stat });
  const uploaded = [];

  for (const a of assets) {
    if (_cancel) break;
    try {
      const info = await MediaLibrary.getAssetInfoAsync(a, { shouldDownloadFromNetwork: true });
      const status = await uploadAsset(cfg.serverUrl, { ...a, localUri: info.localUri }, cfg.device);
      if (status === "saved") { stat.saved++; uploaded.push(a.id); }
      else if (status === "duplicate") { stat.duplicate++; uploaded.push(a.id); }
      else stat.error++;
    } catch {
      stat.error++;
    }
    stat.done++;
    onProgress({ ...stat });
  }

  if (uploaded.length) await markUploaded(uploaded);
  return stat;
}
