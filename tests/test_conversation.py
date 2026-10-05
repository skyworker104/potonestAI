"""대화 맥락 — 세션별 직전 검색, '아니 ~' 교정, 이어 묻기, 확인 뒤에만 스킬 저장."""
import pytest

from backend import db, indexer, landmarks, llm, main, search_retry, skills


@pytest.fixture
def chat(monkeypatch):
    plans, added, penalized = [], [], []
    parses = {
        "제주도 고양이 사진": dict(search_text="고양이"),
        "고양이 사진": dict(search_text="고양이"),
        "강아지 사진": dict(search_text="강아지"),
    }

    def fake_parse(message, history=None):
        core = parses.get(message, {})
        return dict(intent="search", search_text=core.get("search_text"),
                    place_text=None, engine="openrouter")

    def fake_run(plan, finder, **kw):
        plans.append(plan)
        return [{"id": "m1", "score": None}], []

    monkeypatch.setattr(llm, "parse", fake_parse)
    monkeypatch.setattr(search_retry, "run_with_retry", fake_run)
    monkeypatch.setattr(indexer, "ai_available", lambda: False)
    monkeypatch.setattr(db, "match_person_name", lambda m: None)
    monkeypatch.setattr(landmarks, "detect", lambda *a, **k: None)
    monkeypatch.setattr(landmarks, "resolve", lambda *a, **k: None)
    monkeypatch.setattr(skills, "match", lambda q: (None, 0.0))
    monkeypatch.setattr(skills, "add", lambda *a, **k: added.append((a, k)) or {"id": "sk1"})
    monkeypatch.setattr(skills, "reinforce", lambda *a, **k: None)
    monkeypatch.setattr(skills, "penalize", lambda sid, w: penalized.append((sid, w)))
    main._sessions.clear()

    def send(message, session="A"):
        return main.chat(main.ChatRequest(message=message, history=[], session_id=session))
    send.plans, send.added, send.penalized = plans, added, penalized
    return send


def test_follow_up_keeps_previous_conditions(chat):
    chat("제주도 고양이 사진")
    r = chat("그럼 작년 거는?")
    plan = chat.plans[-1]
    assert plan["bbox"] is not None            # 제주 위치 물려받음
    assert plan["search_text"] == "고양이"      # 내용 물려받음
    assert plan["date_from"]                   # 바꾼 조건(작년) 적용
    assert any(l.startswith("🔗 직전 검색에 이어서: 장소·사진 내용") for l in r["explanation"])


def test_sessions_do_not_share_state(chat):
    chat("제주도 고양이 사진", session="A")
    chat("그럼 작년 거는?", session="B")       # B엔 직전 검색이 없다 — 이어 묻기 아님
    assert chat.plans[-1]["bbox"] is None
    assert chat.plans[-1]["search_text"] is None


def test_correction_starts_a_new_search_and_blames_the_old_skill(chat, monkeypatch):
    monkeypatch.setattr(skills, "match", lambda q: (
        ({"id": "old", "label": "고양이", "search_text": "고양이"}, 0.9)
        if q == "고양이 사진" else (None, 0.0)))
    monkeypatch.setattr(skills, "record_use", lambda sid: None)
    chat("고양이 사진")
    r = chat("아니 강아지 사진")
    assert chat.plans[-1]["search_text"] == "강아지"
    assert chat.penalized == [("old", 1.0)]
    assert r["explanation"][0].startswith("↩️")


def test_llm_reading_is_saved_only_after_confirmation(chat):
    chat("강아지 사진")
    assert chat.added == []                    # 해석만으로는 저장하지 않는다
    chat("맞아")
    assert len(chat.added) == 1
    assert chat.added[0][1]["search_text"] == "강아지"


def test_opening_a_result_also_confirms_the_reading(chat):
    chat("강아지 사진")
    main.feedback_view(main.ViewFeedback(media_id="m1", session_id="A"))
    assert len(chat.added) == 1


def test_follow_up_fragments_are_never_learned(chat):
    chat("제주도 고양이 사진")
    chat("그럼 강아지는?")
    main.feedback_view(main.ViewFeedback(media_id="m1", session_id="A"))
    assert chat.added == []
