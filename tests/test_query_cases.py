"""대화 검색 질의 유형 기준표 — claudedocs/search-query-cases.md의 실행 가능한 형태.

새 질의 표현을 지원하거나 동작을 바꿀 때는 이 표에 행을 먼저 추가한다.
  - FRAME_CASES: 문장 → 규칙으로 뽑혀야 할 조건 + 내용으로 남아야 할 말
  - CHAT_CASES : 문장 → 대화 처리 후 검색 계획(plan)과 LLM 호출 여부
기준 시각: 2026-10-09(금) 15:00
"""
from datetime import datetime

import pytest

from backend import db, indexer, landmarks, llm, main, query_frame, search_retry, skills

NOW = datetime(2026, 10, 9, 15, 0)

# (유형 ID, 문장, 기대 조건(부분), 내용으로 남을 말 — skills._strip_terms 기준)
FRAME_CASES = [
    # --- T: 촬영 시기 ---
    ("T1", "2019년 사진", dict(date_from="2019-01-01"), ""),
    ("T2", "2025년 5월 사진", dict(date_from="2025-05-01", date_to="2025-05-31T23:59:59"), ""),
    ("T3", "3월에 찍은 사진", dict(date_from="2026-03-01"), ""),
    ("T4", "2025년 5월 3일 사진", dict(date_from="2025-05-03", date_to="2025-05-03T23:59:59"), ""),
    ("T4", "12월 25일 사진", dict(date_from="2025-12-25"), ""),          # 아직 안 온 날 → 작년
    ("T5", "작년 사진", dict(date_from="2025-01-01", date_to="2025-12-31T23:59:59"), ""),
    ("T5", "재작년 사진", dict(date_from="2024-01-01"), ""),
    ("T5", "지난달 사진", dict(date_from="2026-09-01"), ""),
    ("T5", "이번 달 사진", dict(date_from="2026-10-01"), ""),
    ("T6", "3년 전 사진", dict(date_from="2023-01-01"), ""),
    ("T7", "최근 3일 동안 찍은 사진", dict(date_from="2026-10-06"), ""),
    ("T7", "지난 2주간 사진", dict(date_from="2026-09-25"), ""),
    ("T8", "오늘 찍은 사진", dict(date_from="2026-10-09", date_to="2026-10-09T23:59:59"), ""),
    ("T8", "어제 사진", dict(date_from="2026-10-08"), ""),
    ("T8", "그저께 사진", dict(date_from="2026-10-07"), ""),
    ("T9", "지난주에 찍은 사진", dict(date_from="2026-09-28", date_to="2026-10-04T23:59:59"), ""),
    ("T9", "지난 주말 사진", dict(date_from="2026-10-03", date_to="2026-10-04T23:59:59"), ""),
    ("T9", "이번 주 사진", dict(date_from="2026-10-05"), ""),
    ("T10", "여름에 찍은 사진", dict(season="여름", months=[6, 7, 8]), ""),
    ("T10", "작년 겨울 눈 사진", dict(season="겨울", date_from="2025-01-01"), "눈"),
    ("T10", "여름휴가 사진", dict(season="여름"), "휴가"),     # '휴가'는 상황 말(대화 층에서 제거)
    ("T11", "아침에 찍은 커피", dict(hour_from=6, hour_to=11), "커피"),
    ("T11", "밤에 찍은 사진", dict(hour_from=20, hour_to=5), ""),
    # --- U: 올린 시기 (촬영일과 다르다) ---
    ("U1", "최근 업로드된 사진 보여줘", dict(upload=True, order="added_desc"), ""),
    ("U1", "최근 올라온 사진 보여줘", dict(upload=True, order="added_desc"), ""),
    ("U1", "새로 추가된 사진", dict(upload=True, order="added_desc"), ""),
    ("U1", "최근 백업된 사진", dict(upload=True), ""),
    ("U1", "요즘 올린 동영상", dict(upload=True, media_type="video"), ""),
    ("U2", "오늘 올린 사진", dict(added_from="2026-10-09", date_from=None), ""),
    ("U2", "어제 백업된 사진", dict(added_from="2026-10-08", date_from=None), ""),
    ("U3", "최근 일주일 동안 업로드한 사진", dict(upload=True), ""),
    # --- O: 정렬·개수 ---
    ("O1", "가장 오래된 사진", dict(order="taken_asc"), ""),
    ("O1", "처음 찍은 사진", dict(order="taken_asc"), ""),
    ("O1", "옛날 가족 사진", dict(order="taken_asc"), "가족"),
    ("O2", "최근 사진", dict(order="taken_desc"), ""),
    ("O2", "마지막으로 찍은 사진", dict(order="taken_desc"), ""),
    ("O3", "최근 사진 10장만", dict(limit=10, order="taken_desc"), ""),
    ("O3", "강아지 사진 다섯 장", dict(limit=5), "강아지"),
    # --- A: 속성·종류 ---
    ("A1", "즐겨찾기한 사진", dict(favorites=True), ""),
    ("A1", "좋아요 누른 동영상", dict(favorites=True, media_type="video"), ""),
    ("A2", "동영상 보여줘", dict(media_type="video"), ""),
    ("A3", "사진만 보여줘", dict(media_type="image"), ""),
    ("A4", "스크린샷 보여줘", dict(kind="screenshot"), ""),
    ("A4", "캡처한 사진", dict(kind="screenshot"), ""),
    # --- C: 내용만 (조건이 없어야 한다) ---
    ("C1", "강아지 사진 보여줘", dict(order=None, upload=False, favorites=False), "강아지"),
    ("C1", "선물 받은 꽃 사진", dict(upload=False), "선물 받은 꽃"),  # '받은'은 올린 시기가 아니다
    ("C1", "새로운 카페 사진", dict(upload=False), "새로운 카페"),
    # --- 조합 ---
    ("M1", "작년 여름 제주도 바다 사진 10장", dict(season="여름", limit=10, date_from="2025-01-01"),
     "제주도 바다"),
    ("M2", "최근 올린 강아지 동영상", dict(upload=True, media_type="video"), "강아지"),
]


@pytest.mark.parametrize("case_id,text,expected,content", FRAME_CASES,
                         ids=[f"{c[0]}:{c[1]}" for c in FRAME_CASES])
def test_frame_cases(case_id, text, expected, content):
    f = query_frame.parse(text, NOW)
    for k, v in expected.items():
        if k in ("date_from", "added_from") and v:
            assert (f[k] or "").startswith(v), (k, f[k])
        else:
            assert f[k] == v, (k, f[k])
    assert skills._strip_terms(f["residual"]) == content


# ---------- 대화 층: 문장 → 검색 계획 ----------

@pytest.fixture
def chat(monkeypatch):
    plans, llm_calls = [], []

    def fake_parse(message, history=None):
        llm_calls.append(message)
        core = skills._strip_terms(message)
        return dict(intent="search", search_text=core or None, place_text=None,
                    engine="openrouter")

    monkeypatch.setattr(llm, "parse", fake_parse)
    monkeypatch.setattr(search_retry, "run_with_retry",
                        lambda plan, finder, **kw: plans.append(plan) or ([{"id": "m1"}], []))
    monkeypatch.setattr(indexer, "ai_available", lambda: False)
    monkeypatch.setattr(db, "match_person_name", lambda m: None)
    monkeypatch.setattr(db, "list_albums", lambda: [])
    monkeypatch.setattr(landmarks, "detect", lambda *a, **k: None)
    monkeypatch.setattr(landmarks, "resolve", lambda *a, **k: None)
    monkeypatch.setattr(skills, "match", lambda q: (None, 0.0))
    monkeypatch.setattr(skills, "add", lambda *a, **k: None)
    monkeypatch.setattr(main.search, "place_alias", lambda t: None)
    main._sessions.clear()

    def send(text):
        llm_calls.clear()
        r = main.chat(main.ChatRequest(message=text, history=[], session_id="cases"))
        return r, (plans[-1] if plans else None), list(llm_calls)
    return send


# (유형 ID, 문장, 기대 plan(부분), LLM 호출 여부)
CHAT_CASES = [
    ("U1", "최근 업로드된 사진 보여줘", dict(order="added_desc", search_text=None), False),
    ("U1", "최근 올라온 사진 보여줘", dict(order="added_desc", search_text=None), False),
    ("U2", "오늘 올린 동영상", dict(media_type="video", search_text=None), False),
    ("O1", "가장 오래된 사진", dict(order="taken_asc", search_text=None), False),
    ("O3", "최근 사진 10장만", dict(top_k=10, search_text=None), False),
    ("A1", "즐겨찾기한 사진 보여줘", dict(favorites=True, search_text=None), False),
    ("A4", "스크린샷 보여줘", dict(kind="screenshot", search_text=None), False),
    ("T5", "작년 사진", dict(search_text=None), False),
    ("T10", "여름에 찍은 사진", dict(months=[6, 7, 8], search_text=None), False),
    ("C1", "강아지 사진 보여줘", dict(search_text="강아지", order=None), True),
    ("M2", "최근 올린 강아지 사진", dict(order="added_desc", search_text="강아지"), True),
    ("C2", "작년 여름휴가 가서 찍은 바다", dict(months=[6, 7, 8], search_text="바다"), True),
    ("P1", "제주도 사진", dict(search_text=None), False),
]


@pytest.mark.parametrize("case_id,text,expected,calls_llm", CHAT_CASES,
                         ids=[f"{c[0]}:{c[1]}" for c in CHAT_CASES])
def test_chat_cases(chat, case_id, text, expected, calls_llm):
    r, plan, llm_calls = chat(text)
    assert r["intent"] == "search"
    for k, v in expected.items():
        assert plan[k] == v, (k, plan[k])
    assert bool(llm_calls) == calls_llm, llm_calls


def test_upload_query_explains_it_is_not_the_capture_date(chat):
    r, _, _ = chat("최근 업로드된 사진 보여줘")
    assert r["reply"].startswith("최근 올린 사진을 찾았어요.")
    assert any(l.startswith("⬆️ 올린 시각 기준") for l in r["explanation"])


def test_reply_wording_follows_order_media_and_year(chat, monkeypatch):
    monkeypatch.setattr(search_retry, "run_with_retry",
                        lambda plan, finder, **kw: ([{"id": f"m{i}"} for i in range(plan["top_k"])], []))
    monkeypatch.setattr(main.search, "find", lambda **kw: [{"id": i} for i in range(3922)])
    r, _, _ = chat("가장 오래된 사진 10장")
    assert "가장 오래된 10장을 보여드려요" in r["reply"]
    assert "📦 표시: 전체 3,922장 중 가장 오래된 10장" in r["explanation"]
    r, _, _ = chat("최근 올린 동영상")
    assert r["reply"].startswith("최근 올린 동영상을 찾았어요.")
    r, _, _ = chat("작년 여름에 찍은 사진")
    assert r["reply"].startswith("'작년 여름'에 찍은 사진을 찾았어요.")
