/** 대화형 메인 화면 — 어시스턴트와 대화하며 연결·백업·자동설정. */
import React, { useEffect, useRef, useState } from "react";
import {
  View, Text, TextInput, TouchableOpacity, ScrollView,
  StyleSheet, KeyboardAvoidingView, Platform, ActivityIndicator,
} from "react-native";
import * as MediaLibrary from "expo-media-library";
import { CameraView, useCameraPermissions } from "expo-camera";

import { analyze } from "../lib/assistant";
import { checkServer } from "../lib/api";
import { runBackup, cancelBackup } from "../lib/backup";
import { loadConfig, saveConfig } from "../lib/storage";
import { registerAutoBackup, unregisterAutoBackup } from "../lib/backgroundTask";

const C = {
  bg: "#0f1117", panel: "#171a23", panel2: "#1e2230", text: "#e8eaf0",
  muted: "#8b91a3", accent: "#4f8cff", user: "#2b3550", ai: "#232838",
};

export default function ChatScreen() {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [cfg, setCfg] = useState(null);
  const [scanning, setScanning] = useState(false);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState(null);
  const [albumModal, setAlbumModal] = useState(false);
  const [albums, setAlbums] = useState([]);
  const [selIds, setSelIds] = useState(new Set());
  const scrollRef = useRef(null);
  const [perm, requestPerm] = useCameraPermissions();

  useEffect(() => {
    (async () => {
      const c = await loadConfig();
      setCfg(c);
      addAI(
        c.serverUrl
          ? `다시 오셨네요! ‘${c.device}’ 백업을 도와드릴게요. “백업 시작”이라고 해보세요.`
          : "안녕하세요! 사진을 회원님의 PhotoNest 서버로 백업하는 PhotoNest 업로더예요.\n원본 화질과 촬영 위치(GPS)를 그대로 보존해 올려드려요.\n\n먼저 PC 화면의 ‘폰 연결’ 탭에서 QR을 보여주세요. 아래 ‘QR 스캔’을 누르거나 서버 주소를 입력해 주셔도 돼요."
      );
    })();
  }, []);

  function push(role, text) {
    setMessages((m) => [...m, { role, text, key: String(Date.now() + Math.random()) }]);
    setTimeout(() => scrollRef.current?.scrollToEnd({ animated: true }), 50);
  }
  const addAI = (t) => push("ai", t);
  const addUser = (t) => push("user", t);

  async function handle(text) {
    addUser(text);
    const ctx = {
      connected: !!cfg?.serverUrl, serverUrl: cfg?.serverUrl,
      backing: busy, done: progress?.done, total: progress?.total,
    };
    const r = analyze(text, ctx);
    addAI(r.reply);
    if (r.action) await runAction(r.action);
  }

  async function runAction(action) {
    switch (action.type) {
      case "connect":
        return doConnect(action.serverUrl);
      case "open_qr_scanner":
        return openScanner();
      case "set_auto": {
        const next = await saveConfig({ autoBackup: action.value, wifiOnly: !!action.wifiOnly });
        setCfg(next);
        if (action.value) await registerAutoBackup();
        else await unregisterAutoBackup();
        return;
      }
      case "set_scope": {
        const scope = action.range ? { range: action.range, count: action.count } : cfg.scope;
        const next = await saveConfig({ scope });
        setCfg(next);
        return;
      }
      case "start_backup":
        return doBackup(action);
      case "pause_backup":
        cancelBackup();
        return;
      case "pick_albums":
        return openAlbumPicker();
      default:
        return;
    }
  }

  async function openAlbumPicker() {
    if (!(await ensureMediaPermission())) {
      addAI("폴더 목록을 보려면 사진 접근 권한이 필요해요. 설정에서 허용해 주세요.");
      return;
    }
    const list = await MediaLibrary.getAlbumsAsync({ includeSmartAlbums: true });
    const withAssets = list
      .filter((a) => a.assetCount > 0)
      .sort((a, b) => b.assetCount - a.assetCount);
    setAlbums(withAssets);
    const c = await loadConfig();
    setSelIds(new Set((c.albums || []).map((a) => a.id)));
    setAlbumModal(true);
  }

  function toggleAlbum(id) {
    setSelIds((prev) => {
      const n = new Set(prev);
      n.has(id) ? n.delete(id) : n.add(id);
      return n;
    });
  }

  async function saveAlbums() {
    const chosen = albums
      .filter((a) => selIds.has(a.id))
      .map((a) => ({ id: a.id, title: a.title }));
    const next = await saveConfig({ albums: chosen });
    setCfg(next);
    setAlbumModal(false);
    addAI(
      chosen.length
        ? `백업 폴더를 ${chosen.length}개로 정했어요: ${chosen.map((a) => a.title).join(", ")}.\n“백업 시작”이라고 하면 이 폴더들만 올려요. 언제든 “폴더 선택”으로 바꿀 수 있어요.`
        : "폴더를 선택하지 않아 전체 사진을 백업하도록 했어요."
    );
  }

  async function doConnect(serverUrl) {
    try {
      await checkServer(serverUrl);
      const next = await saveConfig({ serverUrl });
      setCfg(next);
      addAI(`✅ 연결됐어요! 이제 “백업 시작”이라고 하면 사진을 올려드려요. “와이파이에서 자동으로 올려줘”라고 하면 알아서 백업해 둘게요.`);
    } catch {
      addAI(
        `‘${serverUrl}’에 연결하지 못했어요. 확인해 주세요:\n` +
        "① 폰과 PC가 같은 와이파이인지\n" +
        "② PC에서 PhotoNest 서버가 켜져 있는지\n" +
        "③ 주소가 맞는지 (예: 192.168.0.10:8765)\n" +
        "주소를 직접 입력해 다시 시도하셔도 돼요."
      );
    }
  }

  async function ensureMediaPermission() {
    const { status } = await MediaLibrary.requestPermissionsAsync(false, ["photo", "video"]);
    return status === "granted";
  }

  async function doBackup(action) {
    if (busy) { addAI("이미 백업 중이에요. ‘얼마나 했어?’로 진행상황을 볼 수 있어요."); return; }
    if (!(await ensureMediaPermission())) {
      addAI("사진 접근 권한이 필요해요. 설정에서 사진 접근을 허용해 주세요.");
      return;
    }
    setBusy(true); setProgress({ done: 0, total: 0, saved: 0, duplicate: 0, error: 0 });
    try {
      const stat = await runBackup(
        { scope: action.range ? { range: action.range, count: action.count } : cfg.scope, media: action.media },
        (p) => setProgress(p)
      );
      addAI(`백업 완료! 새로 ${stat.saved}장 올렸어요${stat.duplicate ? `, 이미 있던 ${stat.duplicate}장은 건너뛰었어요` : ""}${stat.error ? `, ${stat.error}장은 실패했어요` : ""}. 사진의 위치정보도 그대로 보존됐어요.`);
    } catch (e) {
      addAI("백업 중 문제가 생겼어요: " + (e.message || "알 수 없는 오류"));
    } finally {
      setBusy(false);
    }
  }

  async function openScanner() {
    if (!perm?.granted) {
      const res = await requestPerm();
      if (!res.granted) { addAI("카메라 권한이 없어 QR을 못 읽어요. 서버 주소를 직접 입력해 주세요."); return; }
    }
    setScanning(true);
  }

  function onScanned({ data }) {
    setScanning(false);
    // QR 내용이 http://...:8765/upload 또는 서버 주소
    const m = data.match(/https?:\/\/[\d.]+:\d+/);
    const url = m ? m[0] : data;
    addAI(`QR을 읽었어요: ${url}`);
    doConnect(url);
  }

  if (scanning) {
    return (
      <View style={{ flex: 1, backgroundColor: "#000" }}>
        <CameraView style={{ flex: 1 }} barcodeScannerSettings={{ barcodeTypes: ["qr"] }} onBarcodeScanned={onScanned} />
        <TouchableOpacity style={s.scanCancel} onPress={() => setScanning(false)}>
          <Text style={{ color: "#fff", fontSize: 16 }}>닫기</Text>
        </TouchableOpacity>
        <Text style={s.scanHint}>PC 화면의 QR을 비춰주세요</Text>
      </View>
    );
  }

  if (albumModal) {
    return (
      <View style={s.root}>
        <View style={s.header}>
          <Text style={s.logo}>📁 백업할 폴더 선택</Text>
          <Text style={s.sub}>{selIds.size}개 선택됨 · 비우면 전체 사진 백업</Text>
        </View>
        <ScrollView style={{ flex: 1 }} contentContainerStyle={{ padding: 12 }}>
          {albums.map((a) => {
            const on = selIds.has(a.id);
            return (
              <TouchableOpacity key={a.id} style={[s.albumRow, on && s.albumRowOn]} onPress={() => toggleAlbum(a.id)}>
                <Text style={[s.albumCheck, on && { color: C.accent }]}>{on ? "☑" : "☐"}</Text>
                <Text style={s.albumTitle} numberOfLines={1}>{a.title}</Text>
                <Text style={s.albumCount}>{a.assetCount}장</Text>
              </TouchableOpacity>
            );
          })}
          {!albums.length && <Text style={{ color: C.muted, padding: 20 }}>표시할 앨범이 없어요.</Text>}
        </ScrollView>
        <View style={s.albumActions}>
          <TouchableOpacity style={s.albumCancel} onPress={() => setAlbumModal(false)}>
            <Text style={{ color: C.text }}>취소</Text>
          </TouchableOpacity>
          <TouchableOpacity style={s.albumSave} onPress={saveAlbums}>
            <Text style={{ color: "#fff", fontWeight: "600" }}>이 폴더로 설정</Text>
          </TouchableOpacity>
        </View>
      </View>
    );
  }

  return (
    <KeyboardAvoidingView style={s.root} behavior={Platform.OS === "ios" ? "padding" : undefined}>
      <View style={s.header}>
        <View style={{ flex: 1 }}>
          <Text style={s.logo}>📷 PhotoNest 업로더</Text>
          <Text style={s.sub}>
            {cfg?.serverUrl ? `연결됨 · ${cfg.autoBackup ? "자동백업 켜짐" : "수동"}` : "서버 미연결"}
            {cfg?.albums?.length
              ? ` · 폴더 ${cfg.albums.length}개`
              : cfg?.serverUrl ? " · 전체 사진" : ""}
          </Text>
        </View>
        <TouchableOpacity
          style={s.gear}
          onPress={openAlbumPicker}
          accessibilityLabel="백업할 폴더 설정"
        >
          <Text style={{ fontSize: 20 }}>⚙️</Text>
        </TouchableOpacity>
      </View>

      <ScrollView ref={scrollRef} style={s.log} contentContainerStyle={{ padding: 14 }}>
        {messages.map((m) => (
          <View key={m.key} style={[s.bubble, m.role === "user" ? s.user : s.ai]}>
            <Text style={s.bubbleText}>{m.text}</Text>
          </View>
        ))}
        {busy && progress && (
          <View style={[s.bubble, s.ai]}>
            <ActivityIndicator color={C.accent} />
            <Text style={s.bubbleText}>백업 중… {progress.done}/{progress.total} (올림 {progress.saved})</Text>
          </View>
        )}
      </ScrollView>

      <View style={s.quick}>
        {["백업 시작", "폴더 선택", "와이파이 자동백업", "얼마나 했어?", "QR 스캔", "도움말"].map((q) => (
          <TouchableOpacity key={q} style={s.chip} onPress={() => (q === "QR 스캔" ? openScanner() : handle(q))}>
            <Text style={s.chipText}>{q}</Text>
          </TouchableOpacity>
        ))}
      </View>

      <View style={s.composer}>
        <TextInput
          style={s.input} value={input} onChangeText={setInput}
          placeholder="예: 최근 사진만 올려줘" placeholderTextColor={C.muted}
          onSubmitEditing={() => { if (input.trim()) { handle(input.trim()); setInput(""); } }}
        />
        <TouchableOpacity style={s.send} onPress={() => { if (input.trim()) { handle(input.trim()); setInput(""); } }}>
          <Text style={{ color: "#fff" }}>전송</Text>
        </TouchableOpacity>
      </View>
    </KeyboardAvoidingView>
  );
}

const s = StyleSheet.create({
  root: { flex: 1, backgroundColor: C.bg },
  header: {
    padding: 16, paddingTop: 54, borderBottomColor: "#262b3a", borderBottomWidth: 1,
    flexDirection: "row", alignItems: "center", gap: 10,
  },
  gear: {
    width: 40, height: 40, borderRadius: 20, alignItems: "center", justifyContent: "center",
    backgroundColor: C.panel2, borderWidth: 1, borderColor: "#2c3245",
  },
  logo: { color: C.text, fontSize: 19, fontWeight: "700" },
  sub: { color: C.muted, fontSize: 12, marginTop: 4 },
  log: { flex: 1 },
  bubble: { maxWidth: "88%", padding: 11, borderRadius: 14, marginBottom: 10 },
  user: { alignSelf: "flex-end", backgroundColor: C.user },
  ai: { alignSelf: "flex-start", backgroundColor: C.ai },
  bubbleText: { color: C.text, fontSize: 14, lineHeight: 20 },
  quick: { flexDirection: "row", flexWrap: "wrap", gap: 6, paddingHorizontal: 12, paddingBottom: 6 },
  chip: { backgroundColor: C.panel2, borderColor: "#2c3245", borderWidth: 1, borderRadius: 14, paddingVertical: 6, paddingHorizontal: 11 },
  chipText: { color: C.text, fontSize: 12.5 },
  composer: { flexDirection: "row", gap: 8, padding: 12, paddingBottom: 28, backgroundColor: C.panel },
  input: { flex: 1, backgroundColor: C.panel2, borderColor: "#2c3245", borderWidth: 1, borderRadius: 10, color: C.text, paddingHorizontal: 13, paddingVertical: 10, fontSize: 14 },
  send: { backgroundColor: C.accent, borderRadius: 10, paddingHorizontal: 18, justifyContent: "center" },
  scanCancel: { position: "absolute", top: 50, right: 20, backgroundColor: "rgba(0,0,0,.5)", padding: 12, borderRadius: 24 },
  scanHint: { position: "absolute", bottom: 60, alignSelf: "center", color: "#fff", backgroundColor: "rgba(0,0,0,.5)", padding: 10, borderRadius: 8 },
  albumRow: { flexDirection: "row", alignItems: "center", gap: 10, padding: 13, borderRadius: 10, marginBottom: 6, backgroundColor: C.panel2, borderWidth: 1, borderColor: "#2c3245" },
  albumRowOn: { borderColor: C.accent, backgroundColor: "rgba(79,140,255,.12)" },
  albumCheck: { color: C.muted, fontSize: 18 },
  albumTitle: { color: C.text, fontSize: 14, flex: 1 },
  albumCount: { color: C.muted, fontSize: 12 },
  albumActions: { flexDirection: "row", gap: 10, padding: 12, paddingBottom: 28, backgroundColor: C.panel },
  albumCancel: { flex: 1, alignItems: "center", justifyContent: "center", paddingVertical: 13, borderRadius: 10, borderWidth: 1, borderColor: "#2c3245" },
  albumSave: { flex: 2, alignItems: "center", justifyContent: "center", paddingVertical: 13, borderRadius: 10, backgroundColor: C.accent },
});
