"""명소·자연지명 → 좌표 범위 (산·공원·섬·호수·사찰 등, GeoNames KR).

"지리산에서 찍은 사진"은 places(시·도·관광도시 사전)에도 없고, geoname.py의
역지오코딩은 거주지(동·리)만 쓰므로 사진 지명에도 '지리산'이 남지 않는다.
그래서 지명을 사진에 연결할 고리가 없어 이미지 검색으로 새고, CLIP은
'Jirisan'을 모르니 무관한 사진만 나왔다(실사례: 10장 중 1장만 지리산권).

geoname.py가 이미 받아 둔 KR.zip에서 산(T)·공원(L)·호수/폭포(H)·
사찰/궁궐 등(S)의 한글 이름과 좌표를 뽑아, 이름을 중심점 + 반경으로 바꾼다.
반경은 GeoNames에 면적 정보가 없어 지형 종류별 경험값을 쓴다 — 큰 산은
능선·계곡 입구까지 사진이 퍼지므로 넉넉히, 건물형 명소는 좁게.

같은 이름이 여러 곳이면(지리산: 경남 산청 + 경북의 작은 산) 반경 안에
사진이 가장 많은 곳을 고른다 — 사용자가 실제 다녀온 곳일 가능성이 높다.
"""
import io
import json
import math
import re
import zipfile

from . import db, geoname

CACHE = geoname.DIR / "landmarks.json"
# 반경표·포함 지형이 바뀌면 올린다 — 옛 캐시(예: 한라산 4km)를 버리고 다시 만든다
CACHE_VERSION = 2

# (feature class, code) → 반경 km. 코드가 없으면 class 기본값.
_RADIUS = {
    ("T", "MTS"): 25.0, ("T", "MT"): 12.0, ("T", "VLC"): 12.0,  # 한라산은 VLC ("T", "PK"): 8.0, ("T", "HLL"): 3.0,
    ("T", "ISL"): 8.0, ("T", "ISLS"): 15.0, ("T", "BCH"): 2.0, ("T", "VAL"): 5.0,
    ("T", None): 4.0,
    ("L", "PRK"): 8.0, ("L", "RES"): 10.0, ("L", None): 4.0,
    ("H", "LK"): 3.0, ("H", "FLLS"): 2.0, ("H", "RSV"): 4.0, ("H", "BAY"): 5.0,
    ("S", None): 1.5,
}
_H_CODES = {"LK", "FLLS", "RSV", "BAY"}  # H는 하천 등 너무 많아 일부만
_S_CODES = {"TMPL", "PAL", "CSTL", "HSTS", "MUS", "MNMT", "TOWR", "ZOO",
            "AMUS", "GDN", "SHRN", "PGDA", "OBPT", "LTHSE", "UNIV", "STDM"}
_KIND = {"T": "산·지형", "L": "공원·보호구역", "H": "호수·폭포", "S": "명소"}

_HANGUL = re.compile(r"[가-힣]")
_cache = {"index": None}


def _radius(fclass, code):
    return _RADIUS.get((fclass, code)) or _RADIUS[(fclass, None)]


def _keep(fclass, code):
    if fclass in ("T", "L"):
        return True
    if fclass == "H":
        return code in _H_CODES
    if fclass == "S":
        return code in _S_CODES
    return False


def _norm(name):
    return re.sub(r"\s+", "", name or "")


def _build():
    """KR.zip → {한글이름: [[lat, lon, radius_km, kind], ...]}. 없으면 {}."""
    zp = geoname.DIR / "KR.zip"
    if not zp.exists():
        return {}
    index = {}
    with zipfile.ZipFile(zp) as z, z.open("KR.txt") as f:
        for line in io.TextIOWrapper(f, encoding="utf-8"):
            p = line.rstrip("\n").split("\t")
            if len(p) < 8 or not _keep(p[6], p[7]):
                continue
            try:
                lat, lon = float(p[4]), float(p[5])
            except ValueError:
                continue
            entry = [lat, lon, _radius(p[6], p[7]), _KIND[p[6]]]
            for alt in p[3].split(","):
                if _HANGUL.search(alt):
                    key = _norm(alt)
                    if len(key) >= 2:
                        index.setdefault(key, []).append(entry)
    try:
        CACHE.write_text(json.dumps({"v": CACHE_VERSION, "index": index},
                                    ensure_ascii=False))
    except OSError:
        pass  # 캐시 실패는 다음 기동에 다시 만들면 된다
    return index


def _index():
    if _cache["index"] is None:
        try:
            data = json.loads(CACHE.read_text()) if CACHE.exists() else None
            if isinstance(data, dict) and data.get("v") == CACHE_VERSION:
                _cache["index"] = data["index"]
            else:
                _cache["index"] = _build()
        except Exception:  # 손상된 캐시·데이터 없음 — 기능만 비활성
            _cache["index"] = {}
    return _cache["index"]


def _bbox(lat, lon, r_km):
    dlat = r_km / 111.32
    dlon = r_km / (111.32 * max(math.cos(math.radians(lat)), 0.01))
    return (lat - dlat, lat + dlat, lon - dlon, lon + dlon)


def _photos_in(bbox, coords):
    return sum(1 for la, lo in coords
               if bbox[0] <= la <= bbox[1] and bbox[2] <= lo <= bbox[3])


def _photo_coords():
    with db.conn() as c:
        return [(r["lat"], r["lon"]) for r in c.execute(
            "SELECT lat, lon FROM media WHERE lat IS NOT NULL AND lon IS NOT NULL "
            "AND trashed_at IS NULL")]


# "지리산국립공원" 같은 표기도 받도록 떼어 볼 접미어
_SUFFIXES = ("국립공원", "도립공원", "군립공원")


def resolve(name):
    """지명 → {name, bbox, radius_km, kind, photos}. 모르는 이름이면 None."""
    key = _norm(name)
    if len(key) < 2:
        return None
    index = _index()
    cands = index.get(key)
    if not cands:
        for suf in _SUFFIXES:
            if key.endswith(suf) and len(key) > len(suf) + 1:
                cands = index.get(key[:-len(suf)])
                if cands:
                    key = key[:-len(suf)]
                    break
    if not cands:
        return None
    coords = _photo_coords() if len(cands) > 1 else None
    best = None
    for lat, lon, r, kind in cands:
        bbox = _bbox(lat, lon, r)
        n = _photos_in(bbox, coords) if coords is not None else None
        # 사진 수 우선, 같으면 넓은 지형(큰 산이 같은 이름의 언덕보다 대표성 큼)
        rank = (n or 0, r)
        if best is None or rank > best[0]:
            best = (rank, {"name": key, "bbox": bbox, "radius_km": r,
                           "kind": kind, "photos": n})
    return best[1]


# 토큰 끝 조사 — 긴 것부터. 명소 이름이 이 글자로 끝날 수 있어서(예: '…도')
# 원형을 먼저 찾아보고, 없을 때만 떼어 본다.
_JOSA = ("에서의", "에서", "에게", "까지", "부터", "으로", "이랑", "하고",
         "에", "의", "로", "랑", "도", "은", "는", "이", "가", "을", "를")
_TOKEN = re.compile(r"[가-힣A-Za-z0-9]+")

# 문장에서 직접 찾을 때(LLM 판단 없이)는 명소처럼 끝나는 이름만 본다.
# GeoNames에는 '구름'·'바위' 같은 두 글자 지형명도 있어, 이름만 보면 평범한
# 내용어가 지명으로 오인된다. (LLM이 장소라고 준 이름은 resolve로 바로 푼다)
_SHORT_SUFFIX = ("산", "봉", "섬", "도", "령", "재", "곶", "천")   # 두 글자부터
_LONG_SUFFIX = ("오름", "해수욕장", "해변", "공원", "폭포", "계곡", "호수", "저수지",
                "대교", "타워", "궁", "성", "사", "암", "항", "포구", "숲", "박물관")  # 세 글자부터


def _looks_like_landmark(form):
    if len(form) >= 2 and form.endswith(_SHORT_SUFFIX):
        return True
    return len(form) >= 3 and form.endswith(_LONG_SUFFIX)


def detect(text, exclude=()):
    """문장에서 명소 이름을 찾아 places.detect와 같은 모양으로 반환. 없으면 None.

    exclude: 내용어 사전(바다·공원·폭포 등) — GeoNames에 같은 이름의 지형이
    있어도 사진 내용으로 쓰인 경우가 압도적이라 지명으로 보지 않는다.
    """
    for tok in _TOKEN.findall(text or ""):
        forms = [tok] + [tok[:-len(j)] for j in _JOSA
                         if tok.endswith(j) and len(tok) - len(j) >= 2]
        for form in forms:
            if form in exclude:
                break  # 내용어 — 조사 뗀 형태도 보지 않는다
            if not _looks_like_landmark(form):
                continue
            hit = resolve(form)
            if hit:
                residual = re.sub(r"\s+", " ", text.replace(tok, " ")).strip()
                return dict(hit, residual=residual)
    return None
