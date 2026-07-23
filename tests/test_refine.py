"""대화형 정제(PR2) 유닛 테스트 — 정제 지시어 감지와 대상 교집합."""
from backend import llm


# ---------- detect_refine: 지시어 감지 + 나머지 추출 ----------

def test_basic_refine():
    assert llm.detect_refine("그 중에서 밤에 찍은 것만") == "밤에 찍은 것만"


def test_no_space_variants():
    """음성인식이 붙여 적는 변형."""
    assert llm.detect_refine("그중에 강아지") == "강아지"
    assert llm.detect_refine("그중 강아지 나온 것만") == "강아지 나온 것만"


def test_other_markers():
    assert llm.detect_refine("거기서 동영상만") == "동영상만"
    assert llm.detect_refine("여기서 제주도 사진만") == "제주도 사진만"
    assert llm.detect_refine("이 중에서 아빠 나온 거") == "아빠 나온 거"
    assert llm.detect_refine("검색 결과에서 밤 사진만") == "밤 사진만"


def test_marker_mid_sentence():
    assert llm.detect_refine("밤 사진만 그 중에서 보여줘") == "밤 사진만 보여줘"


def test_marker_alone_returns_empty():
    """지시어만 있으면 빈 문자열 — 호출부가 되묻는다."""
    assert llm.detect_refine("그 중에서") == ""
    assert llm.detect_refine("그중에") == ""


def test_no_marker_returns_none():
    assert llm.detect_refine("강아지 사진 찾아줘") is None
    assert llm.detect_refine("작년 여름 바닷가") is None


def test_refine_not_confused_with_feedback():
    """'그 중에서 ~것만 보여줘'는 정제로 잡혀야 한다 (피드백 'only' 아님).

    main.chat()이 정제를 피드백보다 먼저 검사하는 전제를 문서화하는 테스트:
    이 발화는 detect_refine과 detect_feedback 둘 다에 걸리므로 순서가 중요하다.
    """
    msg = "그 중에서 밤에 찍은 것만 보여줘"
    assert llm.detect_refine(msg) == "밤에 찍은 것만 보여줘"
    assert llm.detect_feedback(msg) is not None  # 순서를 바꾸면 오동작함을 명시


# ---------- _combine_ids: 정제 대상 ∩ 인물 필터 ----------

def test_combine_ids():
    from backend.main import _combine_ids
    assert _combine_ids(None, ["p1", "p2"]) == ["p1", "p2"]
    assert _combine_ids(["a", "b"], None) == ["a", "b"]
    # 교집합 — base 순서 유지
    assert _combine_ids(["a", "b", "c"], ["c", "a"]) == ["a", "c"]
    assert _combine_ids(["a", "b"], []) == []
