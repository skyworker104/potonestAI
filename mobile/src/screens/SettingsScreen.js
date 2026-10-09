/** 설정 화면 — 앱 버전, 연결 상태, 백업 범위. */
import React from "react";
import { View, Text, ScrollView, TouchableOpacity, StyleSheet } from "react-native";

import { versionLine } from "../lib/version";
import { displayAddress } from "../lib/serverAddress";
import { C } from "../theme";

function Row({ label, value, hint, onPress, action }) {
  const Wrap = onPress ? TouchableOpacity : View;
  return (
    <Wrap style={s.row} onPress={onPress}>
      <View style={{ flex: 1 }}>
        <Text style={s.rowLabel}>{label}</Text>
        {!!value && <Text style={s.rowValue}>{value}</Text>}
        {!!hint && <Text style={s.rowHint}>{hint}</Text>}
      </View>
      {!!action && <Text style={s.rowAction}>{action}</Text>}
    </Wrap>
  );
}

export default function SettingsScreen({
  cfg, onClose, onPickAlbums, onOpenServer, onScanServer, onChangeServerByChat,
}) {
  const folders = cfg?.albums?.length
    ? cfg.albums.map((a) => a.title).join(", ")
    : "전체 사진";

  return (
    <View style={s.root}>
      <View style={s.header}>
        <TouchableOpacity style={s.back} onPress={onClose} accessibilityLabel="대화창으로 돌아가기">
          <Text style={s.backText}>‹ 대화</Text>
        </TouchableOpacity>
        <Text style={s.title}>설정</Text>
        <View style={{ width: 64 }} />
      </View>

      <ScrollView contentContainerStyle={{ padding: 14, paddingBottom: 40 }}>
        <Text style={s.section}>서버</Text>
        <Row
          label="연결 상태"
          value={cfg?.serverUrl ? `서버 IP ${displayAddress(cfg.serverUrl)}` : "연결되지 않음"}
          hint={cfg?.serverUrl
            ? "공유기를 다시 켜 서버 IP가 바뀌었다면 아래에서 바꾸세요."
            : "아래 ‘QR 스캔’으로 서버 화면의 QR을 찍어 연결하세요."}
        />
        <View style={s.btnRow}>
          <TouchableOpacity style={s.btn} onPress={onScanServer} accessibilityLabel="QR을 찍어 서버 IP 변경">
            <Text style={s.btnText}>📷 QR 스캔</Text>
          </TouchableOpacity>
          <TouchableOpacity style={s.btn} onPress={onChangeServerByChat} accessibilityLabel="대화로 서버 IP 변경">
            <Text style={s.btnText}>💬 대화로 IP 변경</Text>
          </TouchableOpacity>
        </View>
        <Text style={s.btnHint}>
          QR은 서버 화면 ‘폰 연결’ 탭의 전용 앱 QR이나 1번 QR 어느 것이든 돼요.
          대화창에서 “서버 IP 변경해줘”라고 해도 바꿀 수 있어요.
        </Text>
        {!!cfg?.serverUrl && (
          <Row
            label="서버 사진 보기"
            hint="PC와 같은 검색·타임라인 화면을 앱 안에서 엽니다."
            action="›"
            onPress={onOpenServer}
          />
        )}

        <Text style={s.section}>백업</Text>
        <Row
          label="백업할 폴더"
          value={folders}
          action="›"
          onPress={onPickAlbums}
        />
        <Row
          label="자동 백업"
          value={cfg?.autoBackup ? (cfg?.wifiOnly ? "켜짐 (와이파이에서만)" : "켜짐") : "꺼짐"}
          hint="대화창에서 “와이파이에서 자동으로 올려줘”라고 하면 바뀝니다."
        />

        <Text style={s.section}>앱 정보</Text>
        <Row
          label="앱 버전"
          value={versionLine()}
          hint="날짜 + 그날의 빌드 순번입니다. 서버 ‘폰 연결’ 탭에 표시된 버전과 같으면 최신이에요."
        />
      </ScrollView>
    </View>
  );
}

const s = StyleSheet.create({
  root: { flex: 1, backgroundColor: C.bg },
  header: {
    flexDirection: "row", alignItems: "center", padding: 16, paddingTop: 54,
    borderBottomColor: "#262b3a", borderBottomWidth: 1,
  },
  back: { width: 64 },
  backText: { color: C.accent, fontSize: 16 },
  title: { flex: 1, color: C.text, fontSize: 17, fontWeight: "700", textAlign: "center" },
  section: { color: C.muted, fontSize: 12, marginTop: 18, marginBottom: 8, marginLeft: 4 },
  row: {
    flexDirection: "row", alignItems: "center", gap: 10,
    backgroundColor: C.panel2, borderColor: "#2c3245", borderWidth: 1,
    borderRadius: 12, padding: 14, marginBottom: 8,
  },
  rowLabel: { color: C.text, fontSize: 14.5 },
  rowValue: { color: C.muted, fontSize: 13, marginTop: 4 },
  rowHint: { color: C.muted, fontSize: 11.5, marginTop: 5, lineHeight: 17 },
  rowAction: { color: C.muted, fontSize: 20 },
  btnRow: { flexDirection: "row", gap: 8, marginBottom: 6 },
  btn: {
    flex: 1, alignItems: "center", paddingVertical: 12, borderRadius: 10,
    backgroundColor: "rgba(79,140,255,.14)", borderWidth: 1, borderColor: C.accent,
  },
  btnText: { color: C.text, fontSize: 13.5, fontWeight: "600" },
  btnHint: { color: C.muted, fontSize: 11.5, lineHeight: 17, marginHorizontal: 4, marginBottom: 8 },
});
