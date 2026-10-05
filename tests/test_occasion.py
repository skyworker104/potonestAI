"""상황 말(여행·놀러·휴가…) 분리, 일본 범위, 해석 설명.

실사례: "작년 일본 여행가서 찍은 사진 찾아줘" → '여행'이 사진 내용으로 AND
결합되어 작년 일본 사진 236장 중 2장만 나왔다. 일본 범위도 나가노 등 67장을 놓쳤다.
"""
from backend import llm, places


def test_occasion_words_are_split_off():
    assert llm.strip_occasion("작년 여행가서 찍은 사진") == ("작년 찍은 사진", ["여행"])
    assert llm.strip_occasion("가족여행 가서 찍은 바다 사진") == ("찍은 바다 사진", ["가족여행"])
    assert llm.strip_occasion("신혼 여행 때 찍은 사진")[1] == ["신혼여행"]
    assert llm.strip_occasion("출장 중에 찍은 야경") == ("찍은 야경", ["출장"])


def test_visible_events_and_plain_moves_are_kept():
    # 캠핑·소풍처럼 사진에 보이는 행사, 상황 말 없는 이동 표현은 그대로
    assert llm.strip_occasion("캠핑 사진") == ("캠핑 사진", [])
    assert llm.strip_occasion("바다에 가서 찍은 노을") == ("바다에 가서 찍은 노을", [])


def test_japan_covers_all_islands_but_not_korea():
    jp = places.detect("일본 사진")["bbox"]
    for name, lat, lon in [("나가노", 36.65, 138.18), ("삿포로", 43.06, 141.35),
                           ("후쿠오카", 33.59, 130.40), ("나하", 26.21, 127.68),
                           ("도쿄", 35.68, 139.69), ("쓰시마", 34.20, 129.29)]:
        assert places.in_bbox(lat, lon, jp), name
    for name, lat, lon in [("부산", 35.10, 129.04), ("울릉도", 37.48, 130.90),
                           ("독도", 37.24, 131.87), ("제주", 33.50, 126.53),
                           ("서울", 37.57, 126.98), ("포항", 36.02, 129.37)]:
        assert not places.in_bbox(lat, lon, jp), name


def test_tokyo_is_not_all_of_japan():
    tokyo = places.detect("도쿄 사진")["bbox"]
    assert places.in_bbox(35.68, 139.69, tokyo)
    assert not places.in_bbox(34.69, 135.50, tokyo)  # 오사카


def test_explanation_lists_every_condition_and_what_was_dropped():
    from backend import main
    lines = main._explain(
        place={"name": "일본", "bbox": (0, 1, 0, 1)}, place_text=None, search_text=None,
        date_from="2025-01-01", date_to="2025-12-31T23:59:59", date_label="작년",
        hour_from=None, hour_to=None, person=None, media_type=None,
        dropped=["여행"], relaxed=[], low_conf=False, refined=False)
    assert lines == [
        "📅 기간: '작년' → 2025.01.01 ~ 2025.12.31",
        "📍 장소: '일본' → GPS 위치가 그 지역 안인 사진",
        "➖ '여행' → 촬영 상황을 말하는 표현이라 검색 조건에서 뺐어요",
    ]


def test_occasion_only_request_does_not_return_everything(monkeypatch):
    """'가족여행 사진' — LLM이 내용을 비워도 전체 라이브러리를 쏟지 않는다."""
    from backend import main, skills
    seen = {}
    monkeypatch.setattr(skills, "match", lambda q: (None, 0.0))
    monkeypatch.setattr(skills, "add", lambda *a, **k: None)
    monkeypatch.setattr(main.db, "match_person_name", lambda m: None)
    monkeypatch.setattr(main.llm, "parse", lambda m, history=None: {
        "intent": "search", "search_text": None, "place_text": None,
        "engine": "openrouter"})
    monkeypatch.setattr(main, "_run_search",
                        lambda message, **kw: seen.update(kw) or {"results": []})
    main._last_search.clear()
    main.chat(main.ChatRequest(message="가족여행 사진", history=[]))
    assert seen["search_text"] == "가족여행"
    assert seen["dropped"] == []  # 뺀 게 아니라 내용으로 썼다
