"""지명 → 지리 좌표(bounding box) 검색.

'제주도' 같은 지명은 CLIP 이미지 검색으로는 다른 해변·풍경과 구분되지 않는다.
사진의 GPS 좌표로 해당 지역 범위 안인지 판정하면 정확히 찾을 수 있다.
사용자 정의 지명(피드백으로 학습)은 data/places_user.json에 누적된다.
"""
import json
import re

from . import db

USER_PLACES_FILE = db.DATA_DIR / "places_user.json"

# 지명: (위도min, 위도max, 경도min, 경도max). 별칭은 튜플 키로.
# 시·도 단위는 넉넉히, 섬·시군은 좁게.
_BUILTIN = {
    ("제주", "제주도"): (33.10, 33.60, 126.10, 127.00),
    ("서귀포",): (33.20, 33.35, 126.40, 126.75),
    ("부산", "해운대", "광안리"): (35.00, 35.40, 128.90, 129.30),
    ("서울",): (37.40, 37.70, 126.80, 127.20),
    ("인천",): (37.35, 37.60, 126.40, 126.80),
    ("대구",): (35.78, 35.95, 128.50, 128.70),
    ("광주",): (35.10, 35.25, 126.80, 126.95),
    ("대전",): (36.25, 36.45, 127.30, 127.50),
    ("울산",): (35.45, 35.62, 129.20, 129.45),
    ("강릉",): (37.70, 37.85, 128.85, 129.05),
    ("속초",): (38.18, 38.25, 128.52, 128.62),
    ("강원", "강원도"): (37.00, 38.60, 127.50, 129.40),
    ("경주",): (35.75, 35.90, 129.15, 129.30),
    ("포항",): (35.95, 36.15, 129.30, 129.45),
    ("여수",): (34.70, 34.82, 127.65, 127.78),
    ("통영",): (34.80, 34.90, 128.38, 128.48),
    ("전주",): (35.78, 35.88, 127.08, 127.18),
    ("경기", "경기도"): (37.00, 38.30, 126.50, 127.80),
    ("괌", "guam"): (13.20, 13.70, 144.60, 145.00),
    ("일본", "도쿄", "오사카"): (34.00, 36.00, 135.00, 140.00),
}


def _load_user():
    if USER_PLACES_FILE.exists():
        try:
            return json.loads(USER_PLACES_FILE.read_text())
        except Exception:
            return {}
    return {}


def _all_places():
    """{표시명: (별칭들, bbox)} 형태로 통합 (긴 이름 우선 매칭용)."""
    places = []
    for names, bbox in _BUILTIN.items():
        places.append((names[0], list(names), bbox))
    for name, info in _load_user().items():
        places.append((name, info.get("aliases", [name]), tuple(info["bbox"])))
    return places


def detect(text):
    """질의에서 지명을 찾아 (표시명, bbox, 지명 제거한 나머지) 반환. 없으면 None."""
    best = None  # (길이, name, bbox, alias)
    for name, aliases, bbox in _all_places():
        for alias in aliases:
            if alias and alias in text:
                if best is None or len(alias) > best[0]:
                    best = (len(alias), name, bbox, alias)
    if not best:
        return None
    _, name, bbox, alias = best
    residual = text.replace(alias, " ")
    residual = re.sub(r"\s+", " ", residual).strip()
    return {"name": name, "bbox": bbox, "residual": residual}


def in_bbox(lat, lon, bbox):
    if lat is None or lon is None:
        return False
    return bbox[0] <= lat <= bbox[1] and bbox[2] <= lon <= bbox[3]


def add_user_place(name, bbox, aliases=None):
    """피드백 등으로 사용자 지명 추가/갱신."""
    data = _load_user()
    data[name] = {"bbox": list(bbox), "aliases": aliases or [name]}
    db.DATA_DIR.mkdir(parents=True, exist_ok=True)
    USER_PLACES_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=1))
