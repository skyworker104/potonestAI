/** 와이파이 진입 시 자동 백업 — 백그라운드 태스크.
 *
 * iOS는 OS가 실행 타이밍을 통제하므로 "즉시"가 아니라 주기적으로 깨어날 때
 * 동작한다(Immich 등도 동일). Android는 더 자유롭게 동작한다.
 * 앱이 포그라운드로 돌아올 때도 한 번 더 백업을 시도하는 것을 권장.
 */
import * as BackgroundFetch from "expo-background-fetch";
import * as TaskManager from "expo-task-manager";
import * as Network from "expo-network";
import { loadConfig } from "./storage";
import { runBackup } from "./backup";

export const TASK = "photonest-auto-backup";

TaskManager.defineTask(TASK, async () => {
  try {
    const cfg = await loadConfig();
    if (!cfg.autoBackup || !cfg.serverUrl) {
      return BackgroundFetch.BackgroundFetchResult.NoData;
    }
    // 와이파이 전용이면 현재 연결 확인
    if (cfg.wifiOnly) {
      const state = await Network.getNetworkStateAsync();
      const onWifi = state.type === Network.NetworkStateType.WIFI && state.isConnected;
      if (!onWifi) return BackgroundFetch.BackgroundFetchResult.NoData;
    }
    // 마지막 백업 이후 생성된 새 사진만
    const stat = await runBackup({ onlyAfter: cfg.lastBackupTime || 0 }, () => {});
    return stat.saved > 0
      ? BackgroundFetch.BackgroundFetchResult.NewData
      : BackgroundFetch.BackgroundFetchResult.NoData;
  } catch {
    return BackgroundFetch.BackgroundFetchResult.Failed;
  }
});

export async function registerAutoBackup() {
  try {
    await BackgroundFetch.registerTaskAsync(TASK, {
      minimumInterval: 15 * 60, // 최소 15분 (OS가 실제 타이밍 결정)
      stopOnTerminate: false,
      startOnBoot: true,
    });
  } catch {}
}

export async function unregisterAutoBackup() {
  try {
    if (await TaskManager.isTaskRegisteredAsync(TASK)) {
      await BackgroundFetch.unregisterTaskAsync(TASK);
    }
  } catch {}
}
