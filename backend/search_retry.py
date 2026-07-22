"""검색 결과 인지 재시도 — 0장/저품질이면 조건을 한 단계씩 완화해 다시 찾는다.

설계: claudedocs/conversational-search-design.md §2 (Phase 1)

원칙:
  - LLM 불필요 — 순수 코드 사다리(저사양 태블릿·Termux에서도 동작)
  - 조건은 한 번에 한 단계만 완화하고, 무엇을 완화했는지 라벨로 알려
    호출부가 답변에 명시하게 한다 ("작년엔 없지만 재작년 사진 12장…")
  - 완화해도 의미가 사라지는 시도(내용어 없이 장소까지 제거 등)는 하지 않고
    솔직하게 0장을 반환한다

이 모듈은 backend의 다른 모듈을 임포트하지 않는다(순수 stdlib) —
finder(search.find)·english_fn(llm._ko_to_en)·quality_bar(embedder.params)는
호출부가 주입한다. 유닛 테스트가 AI 스택 없이 가능해진다.
"""
from datetime import datetime, timedelta

# 완화 단계 라벨 → main.py RELAX_PHRASES가 한국어 문구로 변환
NEAREST_LIMIT = 20          # R4 근사 제시 장수
_MONTH_SPAN_DAYS = 45       # 이하면 '월 단위' 범위로 보고 ±1개월 확장
_YEAR_SPAN_DAYS = 400       # 이하면 '연 단위' — 초과(다년 범위)는 확장 생략


def _top_score(results):
    """결과 최고 점수 (점수 없는 결과뿐이면 None)."""
    scores = [r.get("score") for r in results if r.get("score") is not None]
    return max(scores) if scores else None


def _parse_iso(v):
    if not v:
        return None
    try:
        return datetime.fromisoformat(v)
    except (ValueError, TypeError):
        return None


def _widen_dates(plan):
    """날짜 범위를 한 단계 확장한 plan. 확장 불가(다년 범위 등)면 None.

    월 단위(±45일 이내) → ±1개월, 연 단위 → ±1년.
    한쪽 경계만 있으면(예: '올해' = date_from만) 연 단위로 취급.
    """
    d1, d2 = _parse_iso(plan.get("date_from")), _parse_iso(plan.get("date_to"))
    if not (d1 or d2):
        return None
    if d1 and d2:
        span = (d2 - d1).days
        if span > _YEAR_SPAN_DAYS:
            return None  # 이미 다년 범위 — 확장 무의미, 제거 단계로
        delta = timedelta(days=31) if span <= _MONTH_SPAN_DAYS else timedelta(days=366)
    else:
        delta = timedelta(days=366)
    return {
        **plan,
        "date_from": (d1 - delta).strftime("%Y-%m-%d") if d1 else None,
        "date_to": (d2 + delta).strftime("%Y-%m-%dT23:59:59") if d2 else None,
    }


def _merged_place_query(plan, place_name):
    """장소 필터 제거 시 지명을 내용 검색어에 병합 — 최후 수단.

    (지명을 의미검색에 넣으면 '고양시'→'고양이' 오탐이 있어 평소엔 분리하지만,
    필터로는 0장인 상황이므로 임베딩 검색에라도 태워보는 것이 낫다)
    """
    name = plan.get("place_text") or place_name or ""
    text = plan.get("search_text") or ""
    return f"{name} {text}".strip()


def _english_plan(plan, english_fn):
    """내용어를 영어로 변환한 plan. 변환 불가/동일하면 None."""
    st = plan.get("search_text")
    if not st or not english_fn:
        return None
    try:
        en = english_fn(st)
    except Exception:
        return None
    if not en or en == st:
        return None
    return {**plan, "search_text": en}


def _ladder(plan, place_name, english_fn):
    """0장일 때의 완화 시도 목록을 (라벨들, 새 plan) 순서로 생성.

    덜 침습적인 완화(기간 넓히기)부터, 사용자 의도를 더 바꾸는 완화
    (조건 제거) 순. 각 시도는 원 plan 기준의 독립 완화 또는 그 조합이다.
    """
    has_date = bool(plan.get("date_from") or plan.get("date_to"))
    has_bbox = plan.get("bbox") is not None
    has_ptext = bool(plan.get("place_text"))
    content = plan.get("search_text")

    widened = _widen_dates(plan) if has_date else None
    if widened:
        yield ["date_widened"], widened

    # GPS(bbox) 검색 → 지명 메타(앨범·폴더·지오코딩) 필터로 강등.
    # GPS 없는 사진이 많은 라이브러리에서 가장 흔한 구제 경로.
    if has_bbox and place_name:
        demoted = {**plan, "bbox": None, "place_text": place_name}
        yield ["place_to_meta"], demoted
        if widened:
            yield ["date_widened", "place_to_meta"], \
                {**widened, "bbox": None, "place_text": place_name}

    if has_date:
        yield ["date_removed"], {**plan, "date_from": None, "date_to": None}

    # 장소 필터 자체를 제거 — 내용어가 남아 있을 때만
    # (내용도 날짜도 없이 장소만 빼면 전체 사진이 쏟아지므로 하지 않는다)
    if (has_bbox or has_ptext) and content:
        merged = _merged_place_query(plan, place_name)
        no_place = {**plan, "bbox": None, "place_text": None, "search_text": merged}
        yield ["place_removed"], no_place
        if has_date:
            yield ["date_removed", "place_removed"], \
                {**no_place, "date_from": None, "date_to": None}

    ep = _english_plan(plan, english_fn)
    if ep:
        yield ["english_retry"], ep


def _nearest_time(plan, finder):
    """R4 — 요청 시기에 사진이 아예 없으면 가장 가까운 시기 사진을 제시.

    내용·장소 조건까지 전부 실패한 뒤의 최후 제시이므로 조건 없이
    시간 근접도만 본다. 원 요청에 날짜가 없었으면 해당 없음([]).
    """
    d1, d2 = _parse_iso(plan.get("date_from")), _parse_iso(plan.get("date_to"))
    if not (d1 or d2):
        return []
    target = d1 + (d2 - d1) / 2 if (d1 and d2) else (d1 or d2)
    browse = {
        **plan, "search_text": None, "bbox": None, "place_text": None,
        "date_from": None, "date_to": None, "top_k": 100000,
    }
    items = finder(**browse)
    if not items:
        return []

    def dist(it):
        t = _parse_iso(it.get("taken_at"))
        return abs((t - target).total_seconds()) if t else float("inf")

    return sorted(items, key=dist)[:NEAREST_LIMIT]


def run_with_retry(plan, finder, *, place_name=None, quality_bar=None,
                   english_fn=None):
    """plan대로 검색하고, 0장/저품질이면 완화 사다리로 재시도.

    plan: finder(search.find) 키워드 인자 dict
    place_name: bbox 검색의 지명 표시명 (메타 필터 강등에 사용)
    quality_bar: 이 미만의 최고점수는 '사실상 무관'으로 보고 표현 재시도
                 (embedder.params 기반 — None이면 저품질 판정 생략)
    english_fn: 한국어 내용어 → 영어 변환 (None이면 영어 재시도 생략)

    반환: (results, 적용된 완화 라벨 리스트) — 완화 없으면 (results, [])
    """
    results = finder(**plan)

    if results:
        # 결과는 있지만 최고점이 임계 턱걸이 → 표현만 바꿔 1회 재시도.
        # (필터 완화는 같은 저품질 결과만 늘리므로 하지 않는다)
        top = _top_score(results) if plan.get("search_text") else None
        if quality_bar is not None and top is not None and top < quality_bar:
            alt = _english_plan(plan, english_fn)
            if alt:
                retry = finder(**alt)
                if retry and (_top_score(retry) or 0) > top:
                    return retry, ["english_retry"]
        return results, []

    for labels, attempt in _ladder(plan, place_name, english_fn):
        r = finder(**attempt)
        if r:
            return r, labels

    nearest = _nearest_time(plan, finder)
    if nearest:
        return nearest, ["nearest_time"]
    return [], []
