"""미디어 폴더 스캔 → SQLite 색인. EXIF/GPS, 썸네일, 중복 해시, (선택) CLIP 임베딩.

AI 색인은 선택형: AI_SEARCH=0 이거나 sentence-transformers 미설치면
메타데이터만 색인하고 의미 검색은 비활성화된다 (저사양 기기 대응).
"""
import hashlib
import json
import os
import struct
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PIL import Image, ImageOps

from . import caption, db, faces, ocr

try:
    import piexif
except ImportError:
    piexif = None

try:
    import cv2
except ImportError:
    cv2 = None

try:
    from pillow_heif import register_heif_opener
    register_heif_opener()  # PIL이 HEIC/HEIF(아이폰 기본 포맷)를 읽을 수 있게
    HEIC_OK = True
except ImportError:
    HEIC_OK = False

BASE_DIR = Path(__file__).resolve().parent.parent
PHOTOS_DIR = Path(os.environ.get("PHOTOS_DIR", BASE_DIR / "photos"))
DATA_DIR = db.DATA_DIR
THUMBS_DIR = DATA_DIR / "thumbs"
TRASH_DIR = DATA_DIR / "trash"

AI_ENABLED = os.environ.get("AI_SEARCH", "1") not in ("0", "false", "off")

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}
if HEIC_OK:
    IMAGE_EXTS |= {".heic", ".heif"}
VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
THUMB_SIZE = 640

_state = {
    "indexing": False, "ready": False, "total": 0, "done": 0,
    "ai": AI_ENABLED, "ai_ready": False,
    "faces": faces.FACE_ENABLED, "faces_ready": False,
    "ocr_ready": False,
    "phase": "", "error": None,
}
_index_lock = threading.Lock()


def ai_available():
    """이미지 의미 검색(SigLIP2) 사용 가능 여부."""
    if not AI_ENABLED:
        return False
    from . import siglip
    return siglip.available()


def text_ai_available():
    """코멘트 문장 검색(sentence-transformers) 사용 가능 여부."""
    if not AI_ENABLED:
        return False
    try:
        import sentence_transformers  # noqa: F401
        return True
    except ImportError:
        return False


# ---------- EXIF ----------

def _rational_to_float(r):
    return r[0] / r[1] if r[1] else 0.0


def _gps_to_deg(values, ref):
    d = _rational_to_float(values[0])
    m = _rational_to_float(values[1])
    s = _rational_to_float(values[2])
    deg = d + m / 60.0 + s / 3600.0
    if ref in (b"S", b"W", "S", "W"):
        deg = -deg
    return deg


def _takeout_sidecar(path: Path):
    """구글 테이크아웃 사이드카 JSON (촬영시각·GPS) 읽기."""
    for cand in (
        path.with_name(path.name + ".supplemental-metadata.json"),
        path.with_name(path.name + ".json"),
    ):
        if cand.exists():
            try:
                return json.loads(cand.read_text(encoding="utf-8"))
            except Exception:
                return None
    return None


_QT_EPOCH = datetime(1904, 1, 1, tzinfo=timezone.utc)  # QuickTime/MP4 시각 기준


def video_creation_time(path: Path):
    """MP4/MOV 컨테이너의 mvhd 아톰에서 촬영시각 추출 (ffmpeg 불필요).

    저장값은 UTC(1904 기준 초)이므로 로컬 시각으로 변환해 반환.
    값이 없거나 비정상이면 None.
    """
    try:
        size = path.stat().st_size
        chunk = 3_000_000
        with open(path, "rb") as f:
            data = f.read(chunk)          # moov가 앞(fast-start)에 있는 경우
            idx = data.find(b"mvhd")
            if idx < 0 and size > chunk:   # moov가 파일 끝에 있는 경우
                f.seek(max(0, size - chunk))
                data = f.read()
                idx = data.find(b"mvhd")
        if idx < 0:
            return None
        version = data[idx + 4]
        off = idx + 8
        if version == 1:
            secs = struct.unpack(">Q", data[off:off + 8])[0]
        else:
            secs = struct.unpack(">I", data[off:off + 4])[0]
        if not secs:
            return None
        dt_utc = _QT_EPOCH + timedelta(seconds=secs)
        # 비정상 범위(시각 미기록 등) 제외
        if not (1990 <= dt_utc.year <= datetime.now().year + 1):
            return None
        return dt_utc.astimezone().replace(tzinfo=None).isoformat()
    except Exception:
        return None


def extract_exif(path: Path):
    taken_at, lat, lon = None, None, None
    suffix = path.suffix.lower()
    if suffix in VIDEO_EXTS:
        taken_at = video_creation_time(path)
    exif_src = None
    if piexif and suffix in {".jpg", ".jpeg"}:
        exif_src = str(path)
    elif piexif and suffix in {".heic", ".heif"}:
        try:
            exif_src = Image.open(path).info.get("exif")  # HEIC는 PIL에서 EXIF 바이트 추출
        except Exception:
            exif_src = None
    if exif_src:
        try:
            exif = piexif.load(exif_src)
            dt = exif.get("Exif", {}).get(piexif.ExifIFD.DateTimeOriginal)
            if dt:
                taken_at = datetime.strptime(
                    dt.decode(), "%Y:%m:%d %H:%M:%S"
                ).isoformat()
            gps = exif.get("GPS", {})
            if gps.get(piexif.GPSIFD.GPSLatitude) and gps.get(piexif.GPSIFD.GPSLongitude):
                lat = _gps_to_deg(
                    gps[piexif.GPSIFD.GPSLatitude],
                    gps.get(piexif.GPSIFD.GPSLatitudeRef, b"N"),
                )
                lon = _gps_to_deg(
                    gps[piexif.GPSIFD.GPSLongitude],
                    gps.get(piexif.GPSIFD.GPSLongitudeRef, b"E"),
                )
        except Exception:
            pass
    # (0,0) 좌표는 위치정보가 제거된 것 (안드로이드 포토피커가 GPS를 0으로 채움)
    if lat == 0 and lon == 0:
        lat = lon = None

    # EXIF에 없는 정보는 구글 테이크아웃 사이드카 JSON에서 보충
    if taken_at is None or lat is None:
        sc = _takeout_sidecar(path)
        if sc:
            try:
                if taken_at is None and sc.get("photoTakenTime", {}).get("timestamp"):
                    taken_at = datetime.fromtimestamp(
                        int(sc["photoTakenTime"]["timestamp"])
                    ).isoformat()
                geo = sc.get("geoData") or {}
                if lat is None and (geo.get("latitude") or geo.get("longitude")):
                    lat, lon = geo["latitude"], geo["longitude"]
            except Exception:
                pass
    if taken_at is None:
        taken_at = datetime.fromtimestamp(path.stat().st_mtime).isoformat()
    return taken_at, lat, lon


def _rat(v):
    """piexif rational (num, den) → float."""
    try:
        return v[0] / v[1] if v[1] else None
    except (TypeError, IndexError, ZeroDivisionError):
        return None


def _decode(v):
    if isinstance(v, bytes):
        return v.decode("utf-8", "ignore").strip("\x00 ").strip()
    return str(v).strip() if v is not None else None


def extract_details(path: Path):
    """카메라 장비·촬영 설정 등 상세 EXIF를 사람이 읽기 좋은 형태로 추출.

    상세정보 패널을 열 때 해당 파일에서 즉석 호출 (DB 저장 불필요).
    """
    suffix = path.suffix.lower()
    info = {
        "camera": None, "lens": None, "aperture": None, "shutter": None,
        "iso": None, "focal": None, "focal35": None, "flash": None,
        "exposure_bias": None, "megapixels": None, "filesize": None,
    }
    try:
        info["filesize"] = _human_size(path.stat().st_size)
    except OSError:
        pass

    if not piexif or suffix not in {".jpg", ".jpeg", ".heic", ".heif"}:
        return info
    try:
        if suffix in {".heic", ".heif"}:
            src = Image.open(path).info.get("exif")
            if not src:
                return info
            exif = piexif.load(src)
        else:
            exif = piexif.load(str(path))
    except Exception:
        return info

    z, ex = exif.get("0th", {}), exif.get("Exif", {})

    make = _decode(z.get(piexif.ImageIFD.Make))
    model = _decode(z.get(piexif.ImageIFD.Model))
    if make and model and make.split()[0].lower() in model.lower():
        info["camera"] = model           # "Apple iPhone 12" 같은 중복 방지
    elif make or model:
        info["camera"] = " ".join(x for x in (make, model) if x)
    info["lens"] = _decode(ex.get(piexif.ExifIFD.LensModel))

    fn = _rat(ex.get(piexif.ExifIFD.FNumber))
    if fn:
        info["aperture"] = f"f/{fn:.1f}".rstrip("0").rstrip(".")

    et = _rat(ex.get(piexif.ExifIFD.ExposureTime))
    if et:
        info["shutter"] = f"1/{round(1/et)}초" if et < 1 else f"{et:g}초"

    iso = ex.get(piexif.ExifIFD.ISOSpeedRatings)
    if isinstance(iso, (list, tuple)):
        iso = iso[0] if iso else None
    if iso:
        info["iso"] = f"ISO {iso}"

    fl = _rat(ex.get(piexif.ExifIFD.FocalLength))
    if fl:
        info["focal"] = f"{fl:g}mm"
    fl35 = ex.get(piexif.ExifIFD.FocalLengthIn35mmFilm)
    if fl35:
        info["focal35"] = f"{fl35}mm"

    flash = ex.get(piexif.ExifIFD.Flash)
    if isinstance(flash, int):
        info["flash"] = "켜짐" if flash & 1 else "꺼짐"

    eb = _rat(ex.get(piexif.ExifIFD.ExposureBiasValue))
    if eb is not None and abs(eb) > 1e-6:
        info["exposure_bias"] = f"{eb:+.1f} EV"

    return info


def _human_size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def content_hash(path: Path):
    """중복 탐지용 해시: 파일크기 + 앞 4MB md5 (대용량 동영상도 빠르게)."""
    h = hashlib.md5()
    h.update(str(path.stat().st_size).encode())
    with open(path, "rb") as f:
        h.update(f.read(4 * 1024 * 1024))
    return h.hexdigest()


# ---------- 썸네일 ----------

def _video_frame(path: Path):
    if cv2 is None:
        return None, 0.0
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        return None, 0.0
    frames = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
    fps = cap.get(cv2.CAP_PROP_FPS) or 0
    duration = frames / fps if fps else 0.0
    cap.set(cv2.CAP_PROP_POS_FRAMES, int(frames // 2))
    ok, frame = cap.read()
    cap.release()
    if not ok:
        return None, duration
    frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    return Image.fromarray(frame), duration


def _make_thumb(img: Image.Image, item_id: str):
    img = img.convert("RGB")
    img.thumbnail((THUMB_SIZE, THUMB_SIZE))
    THUMBS_DIR.mkdir(parents=True, exist_ok=True)
    img.save(THUMBS_DIR / f"{item_id}.jpg", "JPEG", quality=85)


# ---------- 색인 ----------

def scan_files():
    PHOTOS_DIR.mkdir(parents=True, exist_ok=True)
    skip_dir = "_zips_done"  # 처리 끝난 Takeout zip 보관함은 스캔 제외
    return [
        p for p in sorted(PHOTOS_DIR.rglob("*"))
        if p.is_file() and p.suffix.lower() in IMAGE_EXTS | VIDEO_EXTS
        and not p.name.startswith(".")
        and skip_dir not in p.parts
    ]


def _index_file(p: Path, existing):
    rel = str(p.relative_to(PHOTOS_DIR))
    sig = f"{p.stat().st_size}-{int(p.stat().st_mtime)}"
    if rel in existing and existing[rel]["sig"] == sig:
        return None  # 변경 없음

    # 확장자까지 id에 포함 (라이브포토처럼 같은 이름의 JPG+MP4 충돌 방지)
    item_id = rel.replace("/", "__").replace("\\", "__").replace(".", "_")
    is_video = p.suffix.lower() in VIDEO_EXTS
    taken_at, lat, lon = extract_exif(p)
    duration = 0.0

    if is_video:
        img, duration = _video_frame(p)
        if img is None:
            return None
    else:
        img = Image.open(p)
        # EXIF Orientation을 픽셀에 반영 (썸네일이 돌아가 보이는 문제 방지)
        img = ImageOps.exif_transpose(img)
    w, h = img.convert("RGB").size
    _make_thumb(img, item_id)

    meta = {
        "id": item_id, "path": rel, "type": "video" if is_video else "image",
        "taken_at": taken_at, "lat": lat, "lon": lon,
        "width": w, "height": h, "duration": round(duration, 1),
        "sig": sig, "hash": content_hash(p),
    }
    db.upsert_media(meta)
    return item_id


def _embed_missing():
    """임베딩 없는 미디어를 배치로 인코딩 (SigLIP2, AI 활성 시)."""
    ids = db.missing_embedding_ids()
    if not ids:
        return
    from . import siglip
    for i in range(0, len(ids), 16):
        batch = ids[i : i + 16]
        images, valid = [], []
        for mid in batch:
            tp = THUMBS_DIR / f"{mid}.jpg"
            if tp.exists():
                images.append(Image.open(tp))
                valid.append(mid)
        if not images:
            continue
        vecs = siglip.encode_images(images, batch_size=8)
        for mid, v in zip(valid, vecs):
            db.set_embedding(mid, v)
        _state["done"] = min(_state["done"] + len(valid), _state["total"])


def build_index(force=False):
    with _index_lock:
        if _state["indexing"]:
            # 색인 도중 새 파일 업로드 등으로 재요청 → 현재 색인이 끝난 뒤 한 번 더
            _state["rescan"] = True
            return
        _state["indexing"] = True
        _state["error"] = None

    try:
        _run_pipeline(force)
        while _state.pop("rescan", False):
            _run_pipeline(False)
    except Exception as e:  # noqa: BLE001
        _state["error"] = str(e)
    finally:
        _state["indexing"] = False


def _run_pipeline(force):
    if True:
        db.init()
        # Takeout zip이 있으면 먼저 자동 추출 (새 파일이 스캔에 포함되도록)
        try:
            from . import takeout
            _state["phase"] = "Takeout 압축 해제"
            takeout.process_pending()
        except Exception:
            pass
        files = scan_files()
        existing = {} if force else db.get_by_path()
        _state["phase"] = "메타데이터"
        _state["total"] = len(files)
        _state["done"] = 0

        present = set()
        new_ids = []  # 이번에 처음 발견된(기존 라이브러리에 없던) 미디어 — 자동 캡션 대상
        for p in files:
            rel = str(p.relative_to(PHOTOS_DIR))
            present.add(rel)
            is_new = rel not in existing
            try:
                item_id = _index_file(p, existing)
                if is_new and item_id:
                    new_ids.append(item_id)
            except Exception:
                pass
            _state["done"] += 1

        db.remove_missing(present)
        _state["ready"] = True

        if ai_available():
            _state["phase"] = "AI 색인"
            _state["total"] = len(db.missing_embedding_ids())
            _state["done"] = 0
            _embed_missing()
            _state["ai_ready"] = True

        if faces.available():
            _state["phase"] = "얼굴 분석"
            pending = db.unscanned_media_ids()
            _state["total"] = len(pending)
            _state["done"] = 0
            for mid in pending:
                try:
                    faces.process_media(mid, THUMBS_DIR)
                except Exception:
                    pass
                db.mark_faces_scanned(mid)
                _state["done"] += 1
            faces.cluster_unassigned()
            _state["faces_ready"] = True

        if ocr.available():
            _state["phase"] = "글자 인식(OCR)"
            pending = db.unscanned_ocr_ids()
            _state["total"] = len(pending)
            _state["done"] = 0
            for mid in pending:
                m = db.get_media(mid)
                text = None
                if m:
                    try:
                        p = PHOTOS_DIR / m["path"]
                        img = Image.open(p)
                        img = ImageOps.exif_transpose(img)
                        text = ocr.extract(img)
                    except Exception:
                        pass
                db.set_ocr_text(mid, text)
                _state["done"] += 1
            _state["ocr_ready"] = True

        if caption.available() and new_ids:
            # 비용이 커(장당 수십초) 신규 사진에만 적용 — 기존 라이브러리는 소급 안 함
            from . import search
            _state["phase"] = "사진 설명(캡션)"
            pending = [mid for mid in new_ids if (db.get_media(mid) or {}).get("type") == "image"]
            _state["total"] = len(pending)
            _state["done"] = 0
            for mid in pending:
                m = db.get_media(mid)
                cap = None
                if m:
                    try:
                        tp = THUMBS_DIR / f"{mid}.jpg"
                        cap = caption.generate(Image.open(tp)) if tp.exists() else None
                    except Exception:
                        pass
                emb = search.embed_comment(cap) if cap else None
                db.set_caption(mid, cap, emb)
                _state["done"] += 1
        _state["phase"] = ""


def get_state():
    return _state


# ---------- 휴지통 파일 이동 ----------

def move_to_trash(media_id):
    m = db.get_media(media_id)
    if not m or m["trashed_at"]:
        return False
    src = PHOTOS_DIR / m["path"]
    TRASH_DIR.mkdir(parents=True, exist_ok=True)
    dst = TRASH_DIR / f"{media_id}__{Path(m['path']).name}"
    if src.exists():
        src.rename(dst)
    db.set_trashed(media_id, str(dst.relative_to(DATA_DIR)))
    return True


def restore_from_trash(media_id):
    m = db.get_media(media_id)
    if not m or not m["trashed_at"]:
        return False
    src = DATA_DIR / m["trash_path"]
    dst = PHOTOS_DIR / m["path"]
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.exists():
        src.rename(dst)
    db.set_restored(media_id)
    return True


def delete_permanently(media_id):
    m = db.get_media(media_id)
    if not m:
        return False
    if m["trashed_at"] and m.get("trash_path"):
        f = DATA_DIR / m["trash_path"]
        if f.exists():
            f.unlink()
    thumb = THUMBS_DIR / f"{media_id}.jpg"
    if thumb.exists():
        thumb.unlink()
    # 업로드 중복 캐시에서 해시 제거 — 같은 파일을 다시 올릴 수 있도록
    with db.conn() as c:
        row = c.execute("SELECT hash FROM media WHERE id=?", (media_id,)).fetchone()
    if row and row["hash"]:
        from . import upload
        upload.forget_hash(row["hash"])
    db.delete_media(media_id)
    return True
