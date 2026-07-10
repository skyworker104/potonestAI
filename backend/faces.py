"""얼굴 검출(YuNet) + 인식(SFace) + 인물 클러스터링.

OpenCV 내장 모델만 사용 — torch 불필요, 라즈베리파이에서도 동작.
모델(~37MB)은 최초 실행 시 opencv_zoo에서 자동 다운로드.
이름이 붙은 인물은 클러스터가 유지되며(증분 배정), 자연어 검색과 연동된다.
"""
import os
import urllib.request
from pathlib import Path

import numpy as np

from . import db

try:
    import cv2
except ImportError:
    cv2 = None

MODELS_DIR = db.DATA_DIR / "models"
FACES_DIR = db.DATA_DIR / "faces"

YUNET = ("face_detection_yunet_2023mar.onnx",
         "https://github.com/opencv/opencv_zoo/raw/main/models/"
         "face_detection_yunet/face_detection_yunet_2023mar.onnx")
SFACE = ("face_recognition_sface_2021dec.onnx",
         "https://github.com/opencv/opencv_zoo/raw/main/models/"
         "face_recognition_sface/face_recognition_sface_2021dec.onnx")

DETECT_SCORE = 0.8     # 검출 신뢰도 하한
MIN_FACE_PX = 36       # 너무 작은 얼굴 제외
CLUSTER_SIM = 0.40     # 같은 인물로 묶는 코사인 유사도 하한
CROP_SIZE = 160

FACE_ENABLED = os.environ.get("FACE_SEARCH", "1") not in ("0", "false", "off")

_detector = None
_recognizer = None


def _download(name, url):
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    dst = MODELS_DIR / name
    if dst.exists():
        return dst
    tmp = dst.with_suffix(".part")
    urllib.request.urlretrieve(url, tmp)
    tmp.rename(dst)
    return dst


def available():
    """얼굴 분석 가능 여부 (모델 다운로드 포함)."""
    if not FACE_ENABLED or cv2 is None or not hasattr(cv2, "FaceDetectorYN"):
        return False
    try:
        _download(*YUNET)
        _download(*SFACE)
        return True
    except Exception:
        return False


def _models():
    global _detector, _recognizer
    if _detector is None:
        _detector = cv2.FaceDetectorYN.create(
            str(MODELS_DIR / YUNET[0]), "", (320, 320), DETECT_SCORE
        )
        _recognizer = cv2.FaceRecognizerSF.create(str(MODELS_DIR / SFACE[0]), "")
    return _detector, _recognizer


def process_media(media_id, thumbs_dir):
    """썸네일에서 얼굴을 찾아 임베딩·크롭 저장. 찾은 얼굴 수 반환."""
    tp = thumbs_dir / f"{media_id}.jpg"
    if not tp.exists():
        return 0
    img = cv2.imread(str(tp))
    if img is None:
        return 0
    h, w = img.shape[:2]
    det, rec = _models()
    det.setInputSize((w, h))
    _, found = det.detect(img)
    if found is None:
        return 0

    FACES_DIR.mkdir(parents=True, exist_ok=True)
    n = 0
    for f in found:
        x, y, fw, fh = (int(v) for v in f[:4])
        if fw < MIN_FACE_PX or fh < MIN_FACE_PX:
            continue
        aligned = rec.alignCrop(img, f)
        feat = rec.feature(aligned).flatten().astype(np.float32)
        feat /= (np.linalg.norm(feat) or 1.0)

        face_id = db.add_face(media_id, f"{x},{y},{fw},{fh}", feat)

        # 얼굴 크롭 저장 (인물 카드용, 30% 여유)
        mx, my = int(fw * 0.3), int(fh * 0.3)
        crop = img[max(0, y - my):min(h, y + fh + my),
                   max(0, x - mx):min(w, x + fw + mx)]
        if crop.size:
            crop = cv2.resize(crop, (CROP_SIZE, CROP_SIZE))
            cv2.imwrite(str(FACES_DIR / f"{face_id}.jpg"), crop)
        n += 1
    return n


def cluster_unassigned():
    """미배정 얼굴을 기존 인물(centroid) 또는 새 인물에 배정.

    이름이 붙은 인물의 구성원은 건드리지 않으므로 이름이 유지된다.
    """
    centroids = {}  # person_id → (centroid, count)
    for pid, embs in db.faces_by_person().items():
        mat = np.vstack(embs)
        c = mat.mean(axis=0)
        c /= (np.linalg.norm(c) or 1.0)
        centroids[pid] = c

    assigned = 0
    for face_id, emb in db.unassigned_faces():
        best_pid, best_sim = None, CLUSTER_SIM
        for pid, c in centroids.items():
            sim = float(emb @ c)
            if sim > best_sim:
                best_pid, best_sim = pid, sim
        if best_pid is None:
            best_pid = db.create_person(cover_face_id=face_id)
            centroids[best_pid] = emb
        else:
            # centroid 점진 갱신
            c = centroids[best_pid] + emb * 0.3
            centroids[best_pid] = c / (np.linalg.norm(c) or 1.0)
        db.set_face_person(face_id, best_pid)
        assigned += 1
    return assigned
