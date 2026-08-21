# PhotoNest 업로더 — 전용 모바일 앱 (Expo / React Native)

브라우저 업로드는 iOS·안드로이드 모두 **위치정보(GPS)를 제거**합니다. 이 전용 앱은
시스템 사진 권한으로 **원본 GPS를 보존**하고, **지정한 와이파이에 들어오면 자동 백업**합니다.
ChatGPT처럼 **대화하며** 연결·백업·자동설정을 합니다 (오프라인 의미분석, AI 서버 불필요).

## 화면·동작

- 대화형 UI: "백업 시작", "와이파이에서 자동으로 올려줘", "최근 30장만", "얼마나 했어?", "멈춰" 등을
  자연어로 말하면 의미를 분석해 실행합니다. 빠른 버튼(칩)도 제공.
- 서버 연결: PC '폰 연결' 탭의 QR을 앱으로 스캔하거나 주소(예: `192.168.0.10:8765`) 입력.
- 업로드: 사진을 **원본 그대로**(EXIF·GPS 포함) 서버 `/api/upload`로 전송. 서버가 해시로 중복 스킵.
- 자동 백업: 와이파이 조건에서 새 사진만 백그라운드 업로드(iOS는 OS가 타이밍 통제 — Immich 등과 동일).

## 구조

```
mobile/
  App.js                      앱 진입점
  app.json                    Expo 설정(권한·번들ID)
  eas.json                    빌드 프로필
  src/lib/
    assistant.js              대화 의미분석 엔진(오프라인, 24/24 테스트 통과)
    assistant.test.js         의미분석 단위 테스트  (node src/lib/assistant.test.js)
    api.js                    서버 연동(원본 업로드)
    backup.js                 대상 수집(GPS 보존)·순차 업로드
    backgroundTask.js         와이파이 자동백업 백그라운드 태스크
    storage.js                설정·진행상태 영속화
  src/screens/ChatScreen.js   대화형 메인 화면
```

## 빌드 & 배포

> 이 컴퓨터엔 Xcode/Android SDK가 없어 빌드는 아래 도구로 진행하세요.
> Expo의 클라우드 빌드(EAS)를 쓰면 로컬 SDK 없이도 APK/IPA를 만들 수 있습니다.

### 0) 준비
```bash
cd mobile
npm install
npm i -g eas-cli      # 또는 npx eas-cli
npx expo login        # Expo 계정(무료)
```

### 1) 빠른 시험 (실기기, 빌드 없이)
```bash
npx expo start
```
폰에 **Expo Go** 앱 설치 후 QR 스캔 → 즉시 실행(개발용). 단 백그라운드 자동백업 등 일부 네이티브
기능은 개발 빌드/실제 빌드에서 정확히 동작합니다.

### 2) 안드로이드 APK (QR 배포용)
```bash
eas build -p android --profile preview
```
완료되면 EAS가 APK 다운로드 링크를 줍니다. 그 APK를 서버의
`data/app/photonest-uploader.apk`에 두면 — PC '폰 연결' 탭에 **앱 다운로드 QR이 자동 표시**됩니다.
사용자는 QR로 받아 설치(‘알 수 없는 출처’ 허용)하면 끝.

#### 새 버전을 배포할 때

`data/`는 gitignore라 APK가 clone에 딸려오지 않습니다. 서버·설치 스크립트가
`mobile/APK_URL.txt`의 링크로 내려받는 구조입니다.

> ⚠️ **EAS가 준 링크(`expo.dev/artifacts/...`)를 그대로 쓰지 마세요.** 아티팩트는
> 일정 기간 뒤 사라집니다(실제로 3주 만에 404가 났습니다). 링크가 죽으면 새로
> 설치하는 기기는 앱을 받을 방법이 없어집니다. **GitHub 릴리스에 올려** 만료
> 없는 주소를 쓰세요.

```bash
# 1) 버전 올리기 — versionCode를 안 올리면 기존 설치 위에 업데이트가 안 된다
#    mobile/app.json 의 expo.version, expo.android.versionCode

# 2) 빌드하고 APK를 내려받아 data/app/photonest-uploader.apk 로 교체
eas build -p android --profile preview

# 3) GitHub 릴리스로 올리고, 그 주소를 APK_URL.txt에 넣는다
gh release create uploader-v1.2.0 data/app/photonest-uploader.apk \
  --title "PhotoNest 업로더 v1.2.0 (Android)"
echo "https://github.com/<계정>/<저장소>/releases/download/uploader-v1.2.0/photonest-uploader.apk" \
  > mobile/APK_URL.txt

# 4) 커밋·푸시
```

설치된 기기(태블릿)에서는 Termux에서 이렇게 받습니다.

```bash
cd ~/photonest && git pull && bash scripts/restart.sh
```

재시작 뒤 **'폰 연결' 탭을 열면** 서버가 링크 변경을 알아채고 새 APK를 받아옵니다
(탭에 버전과 진행률이 표시됨). 받아둔 APK 옆의 `photonest-uploader.apk.url`에
출처가 적혀 있어 같은 빌드를 두 번 받지는 않습니다.
`scripts/install-termux.sh`를 다시 돌려도 동일하게 갱신됩니다.

### 3) 아이폰
Apple 정책상 QR 사이드로드가 안 됩니다. 둘 중 하나:
- **TestFlight**(베타, 무료 배포·100명): `eas build -p ios --profile preview` → `eas submit` → TestFlight 링크
- **App Store** 정식 출시: Apple Developer Program($99/년) + 심사

두 경우 모두 Apple Developer 계정이 필요합니다.

## 권한 메모

- 안드로이드: `READ_MEDIA_IMAGES/VIDEO`, **`ACCESS_MEDIA_LOCATION`**(원본 GPS 접근 핵심), 네트워크.
- iOS: `NSPhotoLibraryUsageDescription`, 백그라운드(`fetch`,`processing`). 설정→일반→**백그라운드 앱 새로고침** ON 필요.

## 향후 확장 (대화형으로 추가 가능)

앨범별 백업, 업로드 후 폰에서 삭제(공간 확보), 영상만/사진만, 특정 와이파이 SSID 지정,
중복 사전 점검, 진행 알림 등 — `assistant.js`에 의도를 추가하고 `ChatScreen`의 `runAction`에
동작을 연결하면 됩니다.
