/** PhotoNest 서버 연동 — 서버 확인, 원본(위치정보 포함) 사진 업로드. */
import * as FileSystem from "expo-file-system";

export async function checkServer(serverUrl) {
  const r = await fetch(`${serverUrl}/api/status`, { method: "GET" });
  if (!r.ok) throw new Error("서버 응답 오류");
  return r.json();
}

/**
 * 사진/동영상 1개를 원본 그대로 업로드.
 * asset.localUri는 시스템 권한으로 접근한 원본이라 EXIF·GPS가 보존된다.
 * 반환 status: 'saved' | 'duplicate' | 'unsupported' | 'error'
 */
export async function uploadAsset(serverUrl, asset, device) {
  const uri = asset.localUri || asset.uri;
  const name = asset.filename || uri.split("/").pop();
  const res = await FileSystem.uploadAsync(`${serverUrl}/api/upload`, uri, {
    httpMethod: "POST",
    uploadType: FileSystem.FileSystemUploadType.MULTIPART,
    fieldName: "files",
    parameters: {
      device: device || "내폰",
      meta: JSON.stringify({ [name]: asset.modificationTime || asset.creationTime || 0 }),
    },
    mimeType: asset.mediaType === "video" ? "video/mp4" : "image/jpeg",
  });
  try {
    const body = JSON.parse(res.body);
    return body.results?.[0]?.status || (res.status < 300 ? "saved" : "error");
  } catch {
    return res.status < 300 ? "saved" : "error";
  }
}
