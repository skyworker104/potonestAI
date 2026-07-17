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

_HANGUL = re.compile(r"[가-힣]")
_cache = {"loaded": False, "lat": None, "lon": None, "names": None}


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
    """KR.txt → 거주지(P) 행. 리 단위는 한글 대안명이 없는 경우가 있어
    시·도(ADM1) 한글명을 병기해 최소한 광역 지명으로는 걸리게 한다."""
    adm1 = {}  # admin1 코드 → 시·도 한글명
    parsed = []
    for line in lines:
        f = line.rstrip("\n").split("\t")
        if len(f) < 11:
            continue
        if f[6] == "A" and f[7] == "ADM1":
            kos = _ko_names(f[3], limit=2)
            if kos:
                adm1[f[10]] = kos[0]
        elif f[6] == "P":
            parsed.append(f)
    rows = []
    for f in parsed:
        kos = _ko_names(f[3])
        label = " ".join(dict.fromkeys(kos + [f[1]] +
                                       ([adm1[f[10]]] if f[10] in adm1 else [])))
        try:
            rows.append((label, float(f[4]), float(f[5])))
        except ValueError:
            continue
    return rows


def _parse_world(lines):
    """cities500.txt → 전세계 도시 (해외 여행 사진용)."""
    rows = []
    for line in lines:
        f = line.rstrip("\n").split("\t")
        if len(f) < 8:
            continue
        label = " ".join(dict.fromkeys(_ko_names(f[3]) + [f[1]]))
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
    return lat, lon, names


def _load():
    if _cache["loaded"]:
        return
    try:
        idx, nj = DIR / "index.npz", DIR / "names.json"
        if idx.exists() and nj.exists():
            d = np.load(idx)
            lat, lon = d["lat"], d["lon"]
            names = json.loads(nj.read_text())
        else:
            lat, lon, names = _build()
        _cache.update(loaded=True, lat=lat, lon=lon, names=names)
    except Exception:  # 네트워크 없음 등 — 기능 전체를 조용히 비활성
        _cache.update(loaded=True, lat=None, lon=None, names=None)


def available():
    _load()
    return _cache["lat"] is not None


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
    return " ".join(tokens)[:120] or None
