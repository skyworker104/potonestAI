/** 앱 버전 표기 — 날짜 + 빌드 순번 (예: 2026.08.21-3).
 *
 * 1.2.0 같은 시맨틱 버전만으로는 "지금 폰에 깔린 게 언제 것인지"를 알 수 없다.
 * 빌드 날짜와 순번을 붙이면 서버가 배포 중인 빌드와 눈으로 대조할 수 있다.
 * 값은 app.json에서 온다 — expo.extra.buildDate, expo.android.versionCode.
 */
import Constants from "expo-constants";

/** "2026.08.21-3" — 날짜를 모르면 순번만, 둘 다 없으면 시맨틱 버전. */
export function buildLabel() {
  const cfg = Constants.expoConfig || {};
  const date = cfg.extra?.buildDate;
  const build = cfg.android?.versionCode ?? cfg.ios?.buildNumber;
  if (date && build != null) return `${String(date).replace(/-/g, ".")}-${build}`;
  if (date) return String(date).replace(/-/g, ".");
  if (build != null) return `빌드 ${build}`;
  return cfg.version || "알 수 없음";
}

/** "1.2.0 (2026.08.21-3)" — 설정 화면에 보여줄 한 줄. */
export function versionLine() {
  const cfg = Constants.expoConfig || {};
  const label = buildLabel();
  return cfg.version ? `${cfg.version} (${label})` : label;
}
