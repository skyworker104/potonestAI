"""search_retry(완화 사다리) 유닛 테스트 — AI 스택 없이 스텁 finder로 검증."""
from backend import search_retry


def make_plan(**over):
    """search.find 키워드 인자와 동일한 형태의 기본 plan."""
    plan = dict(
        search_text=None, date_from=None, date_to=None, media_type=None,
        raw_query="q", only_ids=None, bbox=None, exclude_ids=None,
        hour_from=None, hour_to=None, place_text=None, top_k=60,
    )
    plan.update(over)
    return plan


class StubFinder:
    """호출 기록을 남기고, 판정 함수(match)가 참인 호출에만 결과를 준다."""

    def __init__(self, match, results):
        self.match = match
        self.results = results
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return self.results if self.match(kwargs) else []


# ---------- 성공 시 재시도 없음 ----------

def test_first_try_success_no_retry():
    hit = [{"id": "a", "score": 0.5}]
    finder = StubFinder(lambda kw: True, hit)
    results, relaxed = search_retry.run_with_retry(
        make_plan(search_text="dog"), finder)
    assert results == hit
    assert relaxed == []
    assert len(finder.calls) == 1


# ---------- R1: 날짜 완화 ----------

def test_year_range_widened():
    """'작년'(연 단위) 0장 → ±1년 확장에서 발견."""
    plan = make_plan(search_text="beach",
                     date_from="2025-01-01", date_to="2025-12-31T23:59:59")
    finder = StubFinder(lambda kw: kw["date_from"] != "2025-01-01",
                        [{"id": "a", "score": 0.5}])
    results, relaxed = search_retry.run_with_retry(plan, finder)
    assert relaxed == ["date_widened"]
    widened = finder.calls[1]
    assert widened["date_from"] < "2025-01-01"          # 앞으로 확장
    assert widened["date_to"] > "2025-12-31T23:59:59"   # 뒤로 확장
    assert results


def test_month_range_widened_by_month():
    """'작년 6월'(월 단위) → ±1개월만 확장 (±1년 아님)."""
    plan = make_plan(search_text="beach",
                     date_from="2025-06-01", date_to="2025-06-30T23:59:59")
    finder = StubFinder(lambda kw: kw["date_from"] != "2025-06-01",
                        [{"id": "a", "score": 0.5}])
    _, relaxed = search_retry.run_with_retry(plan, finder)
    assert relaxed == ["date_widened"]
    widened = finder.calls[1]
    assert widened["date_from"].startswith("2025-05")
    assert widened["date_to"].startswith("2025-07")


def test_open_ended_from_widened_year():
    """'올해'(date_from만) → 1년 앞으로 확장, date_to는 None 유지."""
    plan = make_plan(search_text="dog", date_from="2026-01-01")
    finder = StubFinder(lambda kw: kw["date_from"] != "2026-01-01",
                        [{"id": "a", "score": 0.5}])
    _, relaxed = search_retry.run_with_retry(plan, finder)
    assert relaxed == ["date_widened"]
    assert finder.calls[1]["date_from"].startswith("2024-12")
    assert finder.calls[1]["date_to"] is None


def test_multiyear_range_skips_widen_goes_to_removal():
    """다년 범위(>400일)는 확장 생략 → 날짜 제거로 직행."""
    plan = make_plan(search_text="dog",
                     date_from="2020-01-01", date_to="2023-12-31T23:59:59")
    finder = StubFinder(lambda kw: kw["date_from"] is None,
                        [{"id": "a", "score": 0.5}])
    _, relaxed = search_retry.run_with_retry(plan, finder)
    assert relaxed == ["date_removed"]
    # 확장 시도가 아예 없어야 함: 원 시도 → 바로 제거 시도
    assert len(finder.calls) == 2


# ---------- R2: 장소 완화 ----------

def test_bbox_demoted_to_place_meta():
    """GPS(bbox) 0장 → 지명 메타 필터로 강등."""
    plan = make_plan(search_text="바닷가", bbox=(33.1, 33.6, 126.1, 127.0))
    finder = StubFinder(
        lambda kw: kw["bbox"] is None and kw["place_text"] == "제주",
        [{"id": "a", "score": 0.5}])
    _, relaxed = search_retry.run_with_retry(plan, finder, place_name="제주")
    assert relaxed == ["place_to_meta"]


def test_date_and_bbox_combined_relaxation():
    """날짜 확장 단독·bbox 강등 단독 실패 → 조합(확장+강등)에서 발견."""
    plan = make_plan(search_text="바닷가", bbox=(33.1, 33.6, 126.1, 127.0),
                     date_from="2025-01-01", date_to="2025-12-31T23:59:59")
    finder = StubFinder(
        lambda kw: (kw["bbox"] is None and kw["place_text"] == "제주"
                    and kw["date_from"] != "2025-01-01"),
        [{"id": "a", "score": 0.5}])
    _, relaxed = search_retry.run_with_retry(plan, finder, place_name="제주")
    assert relaxed == ["date_widened", "place_to_meta"]


def test_place_text_merged_into_search_text():
    """지명 메타 필터 0장 → 필터 빼고 지명을 내용어에 병합."""
    plan = make_plan(search_text="고양이", place_text="고양시")
    finder = StubFinder(
        lambda kw: kw["place_text"] is None and kw["search_text"] == "고양시 고양이",
        [{"id": "a", "score": 0.5}])
    _, relaxed = search_retry.run_with_retry(plan, finder)
    assert relaxed == ["place_removed"]


def test_place_only_query_not_stripped():
    """내용어 없이 장소만인 질의는 장소 제거를 시도하지 않는다(전체 쏟아짐 방지)."""
    plan = make_plan(place_text="제주")
    finder = StubFinder(lambda kw: False, [])
    results, relaxed = search_retry.run_with_retry(plan, finder)
    assert results == [] and relaxed == []
    assert len(finder.calls) == 1  # 원 시도 1회뿐 — 완화 시도 없음


# ---------- R3: 영어 재시도 ----------

def test_english_retry_on_zero_results():
    plan = make_plan(search_text="강아지")
    finder = StubFinder(lambda kw: kw["search_text"] == "dog",
                        [{"id": "a", "score": 0.5}])
    _, relaxed = search_retry.run_with_retry(
        plan, finder, english_fn=lambda t: "dog")
    assert relaxed == ["english_retry"]


def test_low_quality_english_retry_better():
    """결과는 있지만 임계 턱걸이 → 영어 재시도 점수가 더 높으면 교체."""
    ko_hit = [{"id": "x", "score": 0.105}]
    en_hit = [{"id": "y", "score": 0.20}]

    def finder(**kw):
        return {"강아지": ko_hit, "dog": en_hit}[kw["search_text"]]

    results, relaxed = search_retry.run_with_retry(
        make_plan(search_text="강아지"), finder,
        quality_bar=0.1125, english_fn=lambda t: "dog")
    assert relaxed == ["english_retry"]
    assert results == en_hit


def test_low_quality_keeps_original_when_english_worse():
    ko_hit = [{"id": "x", "score": 0.105}]
    en_hit = [{"id": "y", "score": 0.08}]

    def finder(**kw):
        return {"강아지": ko_hit, "dog": en_hit}[kw["search_text"]]

    results, relaxed = search_retry.run_with_retry(
        make_plan(search_text="강아지"), finder,
        quality_bar=0.1125, english_fn=lambda t: "dog")
    assert relaxed == []
    assert results == ko_hit


def test_good_quality_no_english_retry():
    """임계 이상이면 영어 재시도 자체를 하지 않는다."""
    calls = []

    def finder(**kw):
        calls.append(kw)
        return [{"id": "x", "score": 0.3}]

    _, relaxed = search_retry.run_with_retry(
        make_plan(search_text="강아지"), finder,
        quality_bar=0.1125, english_fn=lambda t: "dog")
    assert relaxed == []
    assert len(calls) == 1


# ---------- R4: 근사 시기 제시 ----------

def test_nearest_time_fallback():
    """전 단계 실패 + 날짜 조건 있었음 → 가장 가까운 시기 순으로 제시."""
    items = [
        {"id": "far", "taken_at": "2016-05-01T10:00:00"},
        {"id": "near", "taken_at": "2021-03-01T10:00:00"},
        {"id": "farther", "taken_at": "2025-08-01T10:00:00"},
    ]

    def finder(**kw):
        # 조건 없는 브라우즈(search_text·date 모두 None)에서만 반환
        if kw["search_text"] is None and kw["date_from"] is None:
            return list(items)
        return []

    results, relaxed = search_retry.run_with_retry(
        make_plan(search_text="dog",
                  date_from="2019-01-01", date_to="2019-12-31T23:59:59"),
        finder)
    assert relaxed == ["nearest_time"]
    # 2019년 중간 기준 근접순: 2021 → 2016 → 2025
    assert [r["id"] for r in results] == ["near", "far", "farther"]


def test_nearest_time_respects_limit():
    items = [{"id": str(i), "taken_at": f"2020-01-{i + 1:02d}T10:00:00"}
             for i in range(30)]

    def finder(**kw):
        if kw["search_text"] is None and kw["date_from"] is None:
            return list(items)
        return []

    results, relaxed = search_retry.run_with_retry(
        make_plan(search_text="dog",
                  date_from="2019-01-01", date_to="2019-12-31T23:59:59"),
        finder)
    assert relaxed == ["nearest_time"]
    assert len(results) == search_retry.NEAREST_LIMIT


def test_no_date_no_nearest_time():
    """날짜 조건이 없었으면 근사 제시 없이 솔직하게 0장."""
    finder = StubFinder(lambda kw: False, [])
    results, relaxed = search_retry.run_with_retry(
        make_plan(search_text="dog"), finder, english_fn=lambda t: "dog")
    assert results == [] and relaxed == []
