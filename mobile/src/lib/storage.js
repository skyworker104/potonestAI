/** 설정·백업 진행상태 영속화 (AsyncStorage). */
import AsyncStorage from "@react-native-async-storage/async-storage";

const KEY = "photonest:config";

const DEFAULT = {
  serverUrl: null,
  device: "내폰",
  autoBackup: false,
  wifiOnly: true,
  scope: { range: "all" },     // 'all' | {range:'recent', count}
  albums: [],                  // 백업할 앨범 [{id, title}] — 비면 전체 라이브러리
  lastBackupTime: 0,           // epoch(ms) — 이후 생성된 사진만 자동 백업
  uploadedIds: [],             // 이미 올린 asset id (중복 스킵 보조; 서버도 해시로 막음)
};

export async function loadConfig() {
  try {
    const raw = await AsyncStorage.getItem(KEY);
    return raw ? { ...DEFAULT, ...JSON.parse(raw) } : { ...DEFAULT };
  } catch {
    return { ...DEFAULT };
  }
}

export async function saveConfig(patch) {
  const cur = await loadConfig();
  const next = { ...cur, ...patch };
  await AsyncStorage.setItem(KEY, JSON.stringify(next));
  return next;
}

export async function markUploaded(ids) {
  const cur = await loadConfig();
  const set = new Set(cur.uploadedIds);
  ids.forEach((id) => set.add(id));
  // 너무 커지지 않게 최근 50000개만 유지
  const arr = [...set].slice(-50000);
  await saveConfig({ uploadedIds: arr, lastBackupTime: Date.now() });
}
