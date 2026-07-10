import { StatusBar } from "expo-status-bar";
import ChatScreen from "./src/screens/ChatScreen";
// 백그라운드 자동백업 태스크 정의 등록(앱 로드 시 1회)
import "./src/lib/backgroundTask";

export default function App() {
  return (
    <>
      <StatusBar style="light" />
      <ChatScreen />
    </>
  );
}
