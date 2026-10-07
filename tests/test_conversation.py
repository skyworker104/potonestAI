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
    assert any(l == "🔗 직전 검색에 이어서: 장소·사진 내용은 그대로 썼어요" for l in r["explanation"])


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


def test_capped_filter_search_states_the_real_total(chat, monkeypatch):
    """'작년 사진'이 상한(1000)에 걸리면 '모두 1000장'이 아니라 실제 전체를 밝힌다."""
    from backend import search
    monkeypatch.setattr(search_retry, "run_with_retry",
                        lambda plan, finder, **kw: ([{"id": f"m{i}"} for i in range(1000)], []))
    monkeypatch.setattr(search, "find", lambda **kw: [{"id": i} for i in range(2345)])
    r = chat("작년 사진")
    assert "모두 2,345장이에요. 그중 최근 1,000장을 보여드려요." in r["reply"]
    assert r["total"] == 2345
    assert "📦 표시: 전체 2,345장 중 최근 1,000장" in r["explanation"]


def test_overseas_town_named_as_content_is_searched_as_a_place(chat, monkeypatch):
    """'나가노 사진': LLM이 내용으로 분류해도, 사진 지명에 'Nagano'가 있으면 장소다."""
    from backend import search
    monkeypatch.setattr(search, "place_tokens", lambda: {"nagano", "일본", "도쿄"})
    monkeypatch.setattr(search, "_to_english", lambda t: {"나가노": "Nagano"}.get(t, t))
    monkeypatch.setattr(llm, "parse", lambda m, history=None: dict(
        intent="search", search_text="나가노", place_text=None, engine="openrouter"))
    chat("나가노 사진")
    plan = chat.plans[-1]
    assert plan["place_text"] == "나가노" and plan["search_text"] is None


def test_common_content_word_is_not_mistaken_for_a_place(chat, monkeypatch):
    from backend import search
    monkeypatch.setattr(search, "place_tokens", lambda: {"sea", "nagano"})
    monkeypatch.setattr(llm, "parse", lambda m, history=None: dict(
        intent="search", search_text="바다", place_text=None, engine="openrouter"))
    chat("바다 사진")
    assert chat.plans[-1]["search_text"] == "바다"   # 내용어 사전 단어는 그대로


# ---------- 앨범 이름으로 찾기 ----------

@pytest.fixture
def albums(monkeypatch):
    data = {1: ("쿤", ["k1", "k2"]), 2: ("가족여행", ["f1"])}
    monkeypatch.setattr(db, "list_albums", lambda: [
        {"id": i, "name": n} for i, (n, _) in data.items()])
    real = db.list_photos

    def list_photos(album_id=None, **kw):
        if album_id is None:
            return real(**kw)
        return [{"id": m} for m in data[album_id][1]]
    monkeypatch.setattr(db, "list_photos", list_photos)


def test_one_letter_album_is_found_by_name(chat, albums):
    """'쿤 앨범' — 한 글자 이름이라 이름 검색에서 빠지고 이미지 검색으로 새던 문제."""
    r = chat("쿤 앨범 보여줘")
    plan = chat.plans[-1]
    assert plan["only_ids"] == ["k1", "k2"] and plan["search_text"] is None
    assert r["reply"].startswith("'쿤' 앨범의 사진이에요.")
    assert r["explanation"][0] == "📁 앨범: '쿤' 안의 사진만"


def test_conditions_are_searched_inside_the_album(chat, albums):
    r = chat("쿤 앨범에서 강아지 사진")
    plan = chat.plans[-1]
    assert plan["only_ids"] == ["k1", "k2"] and plan["search_text"] == "강아지"
    assert r["reply"].startswith("'쿤' 앨범에서 '강아지'를 사진 내용으로 보고 찾았어요.")


def test_album_word_order_and_particles(chat, albums):
    chat("앨범 쿤에서 강아지 사진")
    assert chat.plans[-1]["only_ids"] == ["k1", "k2"]


def test_album_name_must_be_a_whole_word(chat, albums):
    chat("타쿤 앨범")                      # '쿤'이 다른 낱말의 일부 — 앨범 아님
    assert chat.plans[-1]["only_ids"] is None


def test_one_letter_name_without_album_word_is_not_an_album(chat, albums):
    chat("쿤 사진")
    assert chat.plans[-1]["only_ids"] is None


def test_longer_name_matches_without_the_word_album(chat, albums):
    chat("가족여행 보여줘")
    assert chat.plans[-1]["only_ids"] == ["f1"]


def test_follow_up_stays_in_the_album(chat, albums):
    chat("쿤 앨범")
    r = chat("그럼 작년 거는?")
    plan = chat.plans[-1]
    assert plan["only_ids"] == ["k1", "k2"] and plan["date_from"]
    assert "앨범" in r["explanation"][0]
