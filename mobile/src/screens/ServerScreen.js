/** 서버 화면 — PC에서 보던 PhotoNest 화면을 앱 안에서 그대로 본다.
 *
 * 서버는 집 와이파이 안에만 있으므로, 밖에 있거나 서버가 꺼져 있으면 흰 화면
 * 대신 무엇이 문제인지 알려준다. 대화창으로 돌아가는 버튼은 항상 남겨둔다.
 */
import React, { useCallback, useEffect, useState } from "react";
import {
  View, Text, TouchableOpacity, StyleSheet, ActivityIndicator,
} from "react-native";
import { WebView } from "react-native-webview";
import * as Network from "expo-network";

import { checkServer } from "../lib/api";
import { diagnose } from "../lib/serverCheck";
import { C } from "../theme";

/** 네트워크 상태를 판정 로직이 쓰는 형태로 정리 — 못 읽으면 null(모름). */
async function readNetwork() {
  try {
    const st = await Network.getNetworkStateAsync();
    return {
      isConnected: st.isConnected,
      isWifi: st.type == null ? null : st.type === Network.NetworkStateType.WIFI,
    };
  } catch (_) {
    return null;
  }
}

export default function ServerScreen({ serverUrl, onClose }) {
  const [problem, setProblem] = useState(undefined);  // undefined=확인 중, null=정상
  const [loading, setLoading] = useState(true);

  const check = useCallback(async () => {
    setProblem(undefined);
    setLoading(true);
    const netState = await readNetwork();
    setProblem(await diagnose({ serverUrl, netState, ping: checkServer }));
  }, [serverUrl]);

  useEffect(() => { check(); }, [check]);

  return (
    <View style={s.root}>
      <View style={s.bar}>
        <TouchableOpacity style={s.back} onPress={onClose} accessibilityLabel="대화창으로 돌아가기">
          <Text style={s.backText}>‹ 대화창</Text>
        </TouchableOpacity>
        <Text style={s.title} numberOfLines={1}>서버 사진</Text>
        <TouchableOpacity style={s.reload} onPress={check} accessibilityLabel="다시 불러오기">
          <Text style={s.backText}>새로고침</Text>
        </TouchableOpacity>
      </View>

      {problem === undefined && (
        <View style={s.center}>
          <ActivityIndicator color={C.accent} />
          <Text style={s.hint}>서버를 확인하는 중…</Text>
        </View>
      )}

      {problem && (
        <View style={s.center}>
          <Text style={s.errTitle}>{problem.title}</Text>
          <Text style={s.errBody}>{problem.body}</Text>
          <TouchableOpacity style={s.retry} onPress={check}>
            <Text style={{ color: "#fff", fontWeight: "600" }}>다시 시도</Text>
          </TouchableOpacity>
          <TouchableOpacity onPress={onClose}>
            <Text style={[s.hint, { color: C.accent, marginTop: 16 }]}>대화창으로 돌아가기</Text>
          </TouchableOpacity>
        </View>
      )}

      {problem === null && (
        <View style={{ flex: 1 }}>
          <WebView
            source={{ uri: serverUrl }}
            style={{ flex: 1, backgroundColor: C.bg }}
            allowsInlineMediaPlayback
            mediaPlaybackRequiresUserAction={false}
            onLoadEnd={() => setLoading(false)}
            onError={() => { setLoading(false); check(); }}
            onHttpError={() => { setLoading(false); check(); }}
          />
          {loading && (
            <View style={s.overlay} pointerEvents="none">
              <ActivityIndicator color={C.accent} />
            </View>
          )}
        </View>
      )}
    </View>
  );
}

const s = StyleSheet.create({
  root: { flex: 1, backgroundColor: C.bg },
  bar: {
    flexDirection: "row", alignItems: "center", paddingHorizontal: 12,
    paddingTop: 52, paddingBottom: 12,
    borderBottomColor: "#262b3a", borderBottomWidth: 1, backgroundColor: C.panel,
  },
  back: { minWidth: 78 },
  reload: { minWidth: 78, alignItems: "flex-end" },
  backText: { color: C.accent, fontSize: 15 },
  title: { flex: 1, color: C.text, fontSize: 16, fontWeight: "700", textAlign: "center" },
  center: { flex: 1, alignItems: "center", justifyContent: "center", padding: 28, gap: 10 },
  hint: { color: C.muted, fontSize: 13, marginTop: 10 },
  errTitle: { color: C.text, fontSize: 17, fontWeight: "700", textAlign: "center" },
  errBody: { color: C.muted, fontSize: 13.5, lineHeight: 21, textAlign: "center" },
  retry: {
    marginTop: 8, backgroundColor: C.accent, borderRadius: 10,
    paddingVertical: 12, paddingHorizontal: 26,
  },
  overlay: {
    ...StyleSheet.absoluteFillObject, alignItems: "center", justifyContent: "center",
    backgroundColor: C.bg,
  },
});
