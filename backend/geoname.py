"""오프라인 역지오코딩 — GPS 좌표 → 지명 (GeoNames 데이터, CC-BY 4.0).

"협재 사진"처럼 사전에 없는 임의 지명 검색을 위해, 사진 좌표를 가장
가까운 지명으로 매핑해 media.place_name에 저장한다(색인 파이프라인의
'지명 매핑' 페이즈). 저장된 지명은 search._named_matches가 검색한다.

데이터 (최초 1회 다운로드 ~12MB, 이후 npz 캐시. 실패 시 조용히 비활성):
  KR.zip        한국 전역 거주지(동/리 단위) — 한글 이름 우선
  cities500.zip 전세계 도시(인구 500+) — 해외 여행 사진 대응

places.py(주요 지역 bbox — GPS 필터 검색)와 역할이 다르다:
places는 "제주도 사진"의 정확한 범위 판정, 여기는 동네 수준 이름 부여.
"""
import io
import json
import os
import re
import urllib.request
import zipfile
from pathlib import Path

import numpy as np

from . import db

DIR = Path(os.environ.get("GEONAMES_DIR", db.DATA_DIR / "models" / "geonames"))
_SOURCES = [
    ("KR.zip", "https://download.geonames.org/export/dump/KR.zip", "KR.txt"),
    ("cities500.zip", "https://download.geonames.org/export/dump/cities500.zip",
     "cities500.txt"),
]
MAX_KM = 15.0  # 가장 가까운 지명이 이보다 멀면(바다 등) 지명 없음 처리
# 지명 문자열 구성이 바뀌면 올린다 — 색인을 다시 만들고 사진 지명을 다시 매긴다
INDEX_VERSION = 4

_HANGUL = re.compile(r"[가-힣]")
_ADMIN_SUFFIX = re.compile(r"(시|군|구|읍|면|동|가)$")
_cache = {"loaded": False, "lat": None, "lon": None, "names": None, "rebuilt": False}


def _download(url, dst):
    tmp = dst.with_suffix(dst.suffix + ".part")
    with urllib.request.urlopen(url, timeout=60) as r, open(tmp, "wb") as f:  # noqa: S310
        while True:
            chunk = r.read(1024 * 256)
            if not chunk:
                break
            f.write(chunk)
    tmp.rename(dst)


def _ko_names(alts, limit=4):
    """대안 이름 목록에서 한글 이름 수집 (하나만 고르면 고어('경성')가 걸릴
    수 있고, 검색은 부분일치라 이름이 많을수록 재현율이 좋다)."""
    if not alts:
        return []
    return [t for t in alts.split(",") if _HANGUL.search(t)][:limit]


def _parse_kr(lines):
    """KR.txt → 거주지(P) 행. 리 단위는 한글 대안명이 없는 경우가 많아
    (실사례: 지리산 사진 182장이 'Kŏrim 경남 Chungsan Pukch'on'처럼 로마자뿐)
    상위 행정구역 한글명 — 읍·면·동(ADM3), 시·군·구(ADM2), 시·도(ADM1) — 을
    병기해 "산청 사진", "시천면에서 찍은" 같은 질의가 걸리게 한다."""
    adm = {}  # (admin1, admin2[, admin3]) 코드 → 한글명들
    parsed = []
    for line in lines:
        f = line.rstrip("\n").split("\t")
        if len(f) < 11:
            continue
        if f[6] == "A" and f[7] in ("ADM1", "ADM2", "ADM3"):
            kos = _ko_names(f[3], limit=2)
            if f[7] != "ADM1":
                # 시·군·읍·면은 '산청군'처럼 접미사 붙은 이름만 — '산청' 질의는
                # 부분일치로 걸린다. 짧은 꼴('고양')을 넣으면 검색의 한 글자
                # 오타 허용이 '고양이'를 '고양'으로 잡는다(실측: 고양이 60 → 495장).
                kos = [k for k in _ko_names(f[3], limit=8) if _ADMIN_SUFFIX.search(k)][:1]
            if not kos:
                continue
            depth = {"ADM1": 1, "ADM2": 2, "ADM3": 3}[f[7]]
            codes = tuple(f[10 + i] if len(f) > 10 + i else "" for i in range(depth))
            if all(codes):
                adm[codes] = kos
        elif f[6] == "P":
            parsed.append(f)
    rows = []
    for f in parsed:
        kos = _ko_names(f[3])
        codes = [f[10 + i] if len(f) > 10 + i else "" for i in range(3)]
        admin = []
        for depth in (3, 2, 1):  # 좁은 행정구역부터
            admin += adm.get(tuple(codes[:depth]), []) if all(codes[:depth]) else []
        label = " ".join(dict.fromkeys(kos + [f[1]] + admin))
        try:
            rows.append((label, float(f[4]), float(f[5])))
        except ValueError:
            continue
    return rows


# 해외 사진 지명에 붙일 한글 나라 이름 (GeoNames 국가 정보엔 한글명이 없다)
_COUNTRY_KO = {
    "JP": "일본", "CN": "중국", "TW": "대만", "HK": "홍콩", "MO": "마카오",
    "TH": "태국", "VN": "베트남", "PH": "필리핀", "SG": "싱가포르", "MY": "말레이시아",
    "ID": "인도네시아", "KH": "캄보디아", "LA": "라오스", "MN": "몽골", "IN": "인도",
    "US": "미국", "CA": "캐나다", "MX": "멕시코", "GU": "괌", "MP": "사이판",
    "AU": "호주", "NZ": "뉴질랜드", "FR": "프랑스", "IT": "이탈리아", "ES": "스페인",
    "PT": "포르투갈", "DE": "독일", "GB": "영국", "CH": "스위스", "AT": "오스트리아",
    "CZ": "체코", "NL": "네덜란드", "BE": "벨기에", "GR": "그리스", "TR": "튀르키예",
    "HR": "크로아티아", "HU": "헝가리", "RU": "러시아", "AE": "아랍에미리트", "EG": "이집트",
}


def _parse_world(lines):
    """cities500.txt → 전세계 도시 (해외 여행 사진용).

    동네급 도시는 한글 대안명이 거의 없어 일본 사진 지명이 'Nukui Sakuradai'처럼
    로마자뿐이었다. 같은 광역 행정구역(도·현·주)의 중심 도시 한글명(PPLA·PPLC —
    일본은 대개 현 이름과 같다: 나가노, 도쿄)과 한글 나라 이름을 병기한다.
    """
    parsed = []
    region_ko = {}  # (나라, admin1) → 중심 도시 한글명
    for line in lines:
        f = line.rstrip("\n").split("\t")
        if len(f) < 11:
            continue
        parsed.append(f)
        kos = _ko_names(f[3], limit=1)
        if kos and f[7] in ("PPLA", "PPLC") and f[10]:
            # 같은 행정구역에 수도와 도청 소재지가 겹치면 도청 소재지 우선
            if f[7] == "PPLA" or (f[8], f[10]) not in region_ko:
                region_ko[(f[8], f[10])] = kos[0]
    rows = []
    for f in parsed:
        extra = [x for x in (region_ko.get((f[8], f[10])), _COUNTRY_KO.get(f[8])) if x]
        label = " ".join(dict.fromkeys(_ko_names(f[3]) + [f[1]] + extra))
        try:
            rows.append((label, float(f[4]), float(f[5])))
        except ValueError:
            continue
    return rows


def _build():
    rows = []
    for zname, url, txt in _SOURCES:
        zp = DIR / zname
        if not zp.exists():
            DIR.mkdir(parents=True, exist_ok=True)
            _download(url, zp)
        with zipfile.ZipFile(zp) as z, z.open(txt) as f:
            lines = io.TextIOWrapper(f, encoding="utf-8")
            rows += _parse_kr(lines) if zname == "KR.zip" else _parse_world(lines)
    names = [r[0] for r in rows]
    lat = np.array([r[1] for r in rows], np.float32)
    lon = np.array([r[2] for r in rows], np.float32)
    np.savez(DIR / "index.npz", lat=lat, lon=lon)
    (DIR / "names.json").write_text(json.dumps(names, ensure_ascii=False))
    (DIR / "index_version").write_text(str(INDEX_VERSION))
    return lat, lon, names


def _load():
    if _cache["loaded"]:
        return
    try:
        idx, nj, ver = DIR / "index.npz", DIR / "names.json", DIR / "index_version"
        current = ver.exists() and ver.read_text().strip() == str(INDEX_VERSION)
        if idx.exists() and nj.exists() and current:
            d = np.load(idx)
            lat, lon = d["lat"], d["lon"]
            names = json.loads(nj.read_text())
        else:
            # 옛 형식 색인이 있었다면 사진 지명도 옛 형식 — 호출부가 다시 매긴다
            had_old = idx.exists()
            lat, lon, names = _build()
            _cache["rebuilt"] = had_old
        _cache.update(loaded=True, lat=lat, lon=lon, names=names)
    except Exception:  # 네트워크 없음 등 — 기능 전체를 조용히 비활성
        _cache.update(loaded=True, lat=None, lon=None, names=None)


def available():
    _load()
    return _cache["lat"] is not None


def take_rebuilt():
    """색인이 새 형식으로 다시 만들어졌으면 True를 한 번만 돌려준다
    (저장된 사진 지명을 지우고 다시 매기라는 신호)."""
    _load()
    flag, _cache["rebuilt"] = _cache["rebuilt"], False
    return flag


def lookup(lat, lon, k=4):
    """좌표 → 근접 지명 문자열 (최근접 k곳의 이름 합침, 거리순 중복 제거).

    좌표 없음/데이터 없음/모두 MAX_KM 밖이면 None.
    예: 협재 해변 → "협재리 한림읍 제주시 ..." — 부분일치 검색용이라
    이름을 넓게 담을수록 "협재 사진" 같은 동네 질의 재현율이 좋다.
    """
    if lat is None or lon is None:
        return None
    _load()
    la, lo, names = _cache["lat"], _cache["lon"], _cache["names"]
    if la is None:
        return None
    # equirectangular 근사 — 수십 km 내 최근접 비교에는 충분히 정확
    dy = (la - lat) * 111.32
    dx = (lo - lon) * 111.32 * float(np.cos(np.radians(lat)))
    d2 = dx * dx + dy * dy
    near = np.argpartition(d2, min(k, len(d2) - 1))[:k]
    near = near[np.argsort(d2[near])]
    tokens = []
    for i in near:
        if d2[i] > MAX_KM * MAX_KM:
            break
        for t in names[i].split():
            if t not in tokens:
                tokens.append(t)
    # 동·리 이름 뒤에 행정구역 한글명이 붙어 길어졌다 — 넉넉히 둔다
    return " ".join(tokens)[:240] or None
