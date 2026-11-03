"""대화로 앨범 만들기 — 찾은 사진(사진+동영상)을 "이 사진들 앨범으로 만들어줘"로 묶는다."""
import pytest

from backend import db, indexer, landmarks, llm, main, search_retry, skills


@pytest.mark.parametrize("msg, name, add, search", [
    ("이사진들을 앨범으로 만들어줘", None, False, None),
    ("이 사진들을 제주여행 앨범으로 만들어줘", "제주여행", False, None),
    ("찾은 거 전부 엄마 생신 앨범에 넣어줘", "엄마 생신", True, None),
    ("이걸로 새 앨범 만들어줘", None, False, None),
    ("이 사진들 '2025 여름' 앨범으로 묶어줘", "2025 여름", False, None),
    ("이 사진들을 여름이라는 앨범으로 만들어줘", "여름", False, None),
    ("거제도 앨범으로 만들어줘", "거제도", False, None),      # '거'를 지시어로 떼지 않는다
    ("제주도 바다 사진 찾아서 앨범으로 만들어줘", None, False, "제주도 바다 사진"),
    ("제주도 사진 찾아서 가족여행 앨범에 넣어줘", "가족여행", True, "제주도 사진"),
])
def test_detect_album_request(msg, name, add, search):
    assert llm.detect_album_request(msg) == {"name": name, "add": add, "search": search}


@pytest.mark.parametrize("msg", ["가족 앨범 만들어줘",     # 빈 앨범 만들기 (화면 음성 명령)
                                 "쿤 앨범에서 바다 사진", "앨범 보여줘", "바다 사진 찾아줘"])
def test_not_album_request(msg):
    assert llm.detect_album_request(msg) is None


@pytest.fixture
def chat(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_FILE", tmp_path / "photonest.db")
    db.init()
    with db.conn() as c:
        for mid, kind, taken in [("p1", "image", "2025-07-02T10:00:00"),
                                 ("v1", "video", "2025-08-15T18:00:00"),
                                 ("p2", "image", "2025-07-20T09:00:00")]:
            c.execute("INSERT INTO media (id, path, type, taken_at) VALUES (?, ?, ?, ?)",
                      (mid, f"{mid}.jpg", kind, taken))
    found = {"ids": ["p1", "v1"]}
    monkeypatch.setattr(llm, "parse", lambda m, history=None: dict(
        intent="search", search_text="바다", place_text=None, engine="openrouter"))
    monkeypatch.setattr(search_retry, "run_with_retry", lambda plan, finder, **kw: (
        [{"id": i, "score": None} for i in found["ids"]], []))
    monkeypatch.setattr(indexer, "ai_available", lambda: False)
    monkeypatch.setattr(db, "match_person_name", lambda m: None)
    monkeypatch.setattr(landmarks, "detect", lambda *a, **k: None)
    monkeypatch.setattr(landmarks, "resolve", lambda *a, **k: None)
    monkeypatch.setattr(skills, "match", lambda q: (None, 0.0))
    main._sessions.clear()

    def send(message, selected=None, session="A"):
        return main.chat(main.ChatRequest(message=message, history=[], session_id=session,
                                          selected_ids=selected))
    send.found = found
    return send


def items(album_id):
    return sorted(it["id"] for it in db.list_photos(album_id=album_id, limit=100))


def test_results_become_album_with_photos_and_videos(chat):
    chat("바다 사진 찾아줘")
    r = chat("이 사진들을 앨범으로 만들어줘")
    assert r["intent"] == "album" and r["created"]
    assert items(r["album_id"]) == ["p1", "v1"]
    assert r["album_name"] == "바다 2025.07~2025.08"     # 내용 + 촬영 기간으로 이름
    assert "2개" in r["reply"]


def test_named_album_and_existing_album(chat):
    chat("바다 사진 찾아줘")
    r = chat("이 사진들을 제주여행 앨범으로 만들어줘")
    assert r["album_name"] == "제주여행" and items(r["album_id"]) == ["p1", "v1"]

    chat.found["ids"] = ["v1", "p2"]
    chat("바다 사진 찾아줘")
    r2 = chat("찾은 거 전부 제주여행 앨범에 넣어줘")   # 같은 이름 → 그 앨범에 더한다
    assert r2["album_id"] == r["album_id"] and not r2["created"]
    assert items(r["album_id"]) == ["p1", "p2", "v1"]
    assert "1개" in r2["reply"] and "이미" in r2["reply"]
    assert len(db.list_albums()) == 1


def test_auto_name_does_not_merge_into_existing_album(chat):
    chat("바다 사진 찾아줘")
    a = chat("이 사진들 앨범으로 만들어줘")
    b = chat("이 사진들 앨범으로 만들어줘")
    assert b["album_id"] != a["album_id"]
    assert b["album_name"] == a["album_name"] + " (2)"


def test_selected_photos_only(chat):
    chat("바다 사진 찾아줘")
    r = chat("이 사진들 앨범으로 만들어줘", selected=["v1"])
    assert items(r["album_id"]) == ["v1"] and "고른 1개" in r["reply"]


def test_search_and_album_in_one_sentence(chat):
    r = chat("바다 사진 찾아서 앨범으로 만들어줘")
    assert r["intent"] == "search" and [x["id"] for x in r["results"]] == ["p1", "v1"]
    assert items(r["album_id"]) == ["p1", "v1"]


def test_no_previous_results(chat):
    r = chat("이 사진들 앨범으로 만들어줘")
    assert r["intent"] == "chat" and "먼저" in r["reply"]
    assert db.list_albums() == []
    chat("바다 사진 찾아줘", session="B")        # 다른 탭의 검색 결과는 쓰지 않는다
    assert chat("이 사진들 앨범으로 만들어줘", session="A")["intent"] == "chat"
