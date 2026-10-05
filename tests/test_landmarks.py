"""명소 사전(landmarks) — "지리산에서 찍은 사진"이 GPS 위치 검색으로 풀리는지.

실사례: 지리산권 GPS 사진이 182장 있었지만 '지리산'을 좌표로 바꿀 길이 없어
CLIP 의미검색으로 새고 무관한 사진 9장이 섞여 나왔다.
"""
import zipfile

import pytest

from backend import db, geoname, landmarks

# GeoNames KR.txt 형식: id, name, ascii, alternatenames, lat, lon, class, code, ...
_ROWS = [
    ("1", "Jirisan", "Jirisan", "Jirisan,지리산", "35.33694", "127.73056", "T", "MT"),
    ("2", "Jirisan", "Jirisan", "지리산", "36.61963", "128.76206", "T", "MT"),   # 경북의 작은 산
    ("3", "Jirisan IC", "Jirisan IC", "지리산", "35.4841", "127.58919", "R", "RDJCT"),  # 도로 — 제외
    ("4", "Gureum", "Gureum", "구름", "37.0", "127.0", "T", "HLL"),          # 내용어 같은 지형명
    ("5", "Bulguksa", "Bulguksa", "불국사", "35.7900", "129.3320", "S", "TMPL"),
    ("6", "Bada", "Bada", "바다", "34.0", "126.0", "T", "BCH"),             # 내용어 사전 단어
]


@pytest.fixture
def gaz(tmp_path, monkeypatch):
    data = tmp_path / "data"
    gdir = data / "models" / "geonames"
    gdir.mkdir(parents=True)
    with zipfile.ZipFile(gdir / "KR.zip", "w") as z:
        z.writestr("KR.txt", "\n".join("\t".join(r + ("KR",)) for r in _ROWS) + "\n")
    monkeypatch.setattr(db, "DATA_DIR", data)
    monkeypatch.setattr(db, "DB_FILE", data / "photonest.db")
    monkeypatch.setattr(geoname, "DIR", gdir)
    monkeypatch.setattr(landmarks, "CACHE", gdir / "landmarks.json")
    monkeypatch.setitem(landmarks._cache, "index", None)
    db.init()
    return gdir


def _photo(mid, lat, lon):
    db.upsert_media(dict(id=mid, path=f"{mid}.jpg", type="image",
                         taken_at="2025-05-25T10:00:00", lat=lat, lon=lon,
                         width=1, height=1, duration=None, sig=mid, hash=mid))


def test_mountain_name_resolves_to_a_gps_box(gaz):
    hit = landmarks.resolve("지리산")
    assert hit["kind"] == "산·지형" and hit["radius_km"] == 12.0
    lat0, lat1, lon0, lon1 = hit["bbox"]
    assert lat0 < 35.33694 < lat1 and lon0 < 127.73056 < lon1
    assert (gaz / "landmarks.json").exists()  # 다음 기동부터는 캐시


def test_same_name_picks_the_place_the_photos_are(gaz):
    # 산청 중산리 쪽 사진 3장 — 경북의 동명 산이 아니라 경남 지리산을 골라야 한다
    for i, (la, lo) in enumerate([(35.28, 127.75), (35.30, 127.70), (35.35, 127.80)]):
        _photo(f"m{i}", la, lo)
    hit = landmarks.resolve("지리산")
    assert hit["photos"] == 3
    assert hit["bbox"][0] < 35.3 < hit["bbox"][1]


def test_national_park_suffix_is_accepted(gaz):
    assert landmarks.resolve("지리산 국립공원")["name"] == "지리산"


def test_roads_and_unknown_names_do_not_resolve(gaz):
    assert landmarks.resolve("없는산") is None
    # 도로(R)는 색인하지 않는다 — 경북·경남 두 산만 후보
    assert len(landmarks._index()["지리산"]) == 2


def test_detect_in_a_spoken_sentence(gaz):
    hit = landmarks.detect("지리산에서 찍은 사진보여줘")
    assert hit["name"] == "지리산"
    assert "지리산" not in hit["residual"]
    assert landmarks.detect("불국사 사진")["name"] == "불국사"


def test_detect_ignores_plain_content_words(gaz):
    # GeoNames에 '구름'·'바다' 지형이 있어도 문장 속 내용어는 지명이 아니다
    assert landmarks.detect("구름 사진 보여줘") is None
    assert landmarks.detect("바다 사진", exclude={"바다"}) is None


def test_llm_content_term_is_promoted_to_place(gaz):
    from backend import main
    # LLM이 '지리산'을 사진 내용으로 분류해도 장소로 옮긴다
    assert main._promote_landmark("지리산", None) == (None, "지리산")
    assert main._promote_landmark("지리산 단풍", None) == ("단풍", "지리산")
    assert main._promote_landmark("강아지", None) == ("강아지", None)


def test_reply_states_how_the_query_was_read():
    from backend import main
    lm = {"name": "지리산", "bbox": (0, 1, 0, 1), "radius_km": 12.0, "kind": "산·지형"}
    assert main._interpretation(lm, None, None) == "'지리산'을 장소(GPS 반경 약 12km)로 보고"
    assert main._interpretation(None, "고양시", "바다") == \
        "'고양시'를 장소 이름(앨범·폴더·지명)으로, '바다'를 사진 내용으로 보고"
    assert main._interpretation(None, None, None) == ""


# ---------- 약한 이미지 결과 컷 (search.find) ----------

@pytest.fixture
def weak_search(monkeypatch):
    """이미지 점수가 기준선 근처(0.240~0.251)뿐인 clip-onnx 상황을 흉내 낸다."""
    import numpy as np
    from backend import embedder, indexer, search
    items = [dict(id=f"p{i}", path=f"p{i}.jpg", type="image", taken_at="2025-01-01T00:00:00",
                  lat=None, lon=None, comment=None, place_name=None) for i in range(4)]
    monkeypatch.setattr(db, "list_photos", lambda **kw: items)
    monkeypatch.setattr(indexer, "ai_available", lambda: True)
    monkeypatch.setattr(indexer, "text_ai_available", lambda: False)
    monkeypatch.setattr(embedder, "params", lambda: embedder._PARAMS["clip-onnx"])
    monkeypatch.setattr(embedder, "model_id", lambda: "m")
    monkeypatch.setattr(embedder, "dim", lambda: 1)
    monkeypatch.setattr(embedder, "needs_english", lambda: False)
    monkeypatch.setattr(embedder, "encode_text", lambda t: np.array([[1.0]], np.float32))
    monkeypatch.setattr(db, "load_embeddings", lambda m, d: (
        ["p0", "p1", "p2", "p3"], np.array([[0.251], [0.248], [0.244], [0.10]], np.float32)))
    monkeypatch.setattr(db, "album_name_media", lambda: [])
    monkeypatch.setattr(db, "caption_texts", lambda: [])
    return search, items


def test_weak_image_hits_are_dropped_when_text_evidence_exists(weak_search, monkeypatch):
    search, items = weak_search
    # p3: 사진 속 글자에 '지리산' (OCR) — 이미지 점수는 낮아도 이것만 근거가 있다
    monkeypatch.setattr(search, "_ocr_matches",
                        lambda q, allowed, by_id, k: [(by_id["p3"], 1.0)])
    got = [r["id"] for r in search.find("지리산")]
    assert got == ["p3"]


def test_weak_image_hits_stay_when_they_are_the_only_evidence(weak_search, monkeypatch):
    search, _ = weak_search
    monkeypatch.setattr(search, "_ocr_matches", lambda *a: [])
    # 실재 주제도 이 점수대일 수 있어(실측 '강아지' 0.260) 버리지 않는다 — 답변이 밝힌다
    assert [r["id"] for r in search.find("강아지")] == ["p0", "p1", "p2"]


def test_volcano_counts_as_a_big_mountain(gaz, monkeypatch):
    # 한라산은 GeoNames에서 MT가 아니라 VLC — 기본 반경(4km)이면 대부분 놓친다
    assert landmarks._radius("T", "VLC") == 12.0


def test_stale_cache_is_rebuilt(gaz):
    # 옛 형식(버전 없음) 캐시에 잘못된 반경이 남아 있어도 다시 만든다
    (gaz / "landmarks.json").write_text('{"지리산": [[35.3, 127.7, 4.0, "산·지형"]]}')
    assert landmarks.resolve("지리산")["radius_km"] == 12.0
