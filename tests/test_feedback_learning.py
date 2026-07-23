"""피드백 학습(PR3) 유닛 테스트 — 긍정 감지, 스킬 신뢰도, v1 마이그레이션."""
import json

import pytest

from backend import llm, skills


# ---------- detect_feedback: positive ----------

def test_positive_short_confirmations():
    for msg in ("맞아", "그거야", "좋네", "잘 찾았네", "잘 찾아줬네",
                "고마워", "감사합니다", "딱이야", "완벽해"):
        fb = llm.detect_feedback(msg)
        assert fb and fb["type"] == "positive", msg


def test_positive_not_triggered_by_new_request():
    """긍정 단어로 시작해도 새 요청이 이어지면 피드백이 아니다."""
    assert llm.detect_feedback("좋아 그럼 제주도 바닷가 사진 찾아줘") is None
    assert llm.detect_feedback("맞아 그거 말고 강아지 보여줘") is None


def test_negative_still_detected():
    """회귀: 기존 부정 피드백 타입 유지."""
    assert llm.detect_feedback("틀렸어")["type"] == "wrong"
    assert llm.detect_feedback("위치로 다시 찾아줘")["type"] == "location"
    assert llm.detect_feedback("제주도 아닌 거 빼줘")["type"] == "only"


# ---------- 스킬 신뢰도 (동적 임계값 / 도태) ----------

def test_threshold_default():
    assert skills._threshold({"success": 0.0, "fail": 0.0}) == skills.MATCH_THRESHOLD


def test_threshold_lowered_when_trusted():
    assert skills._threshold({"success": 2.5, "fail": 0.0}) == 0.85


def test_threshold_raised_when_failing():
    assert skills._threshold({"success": 0.5, "fail": 1.0}) == 0.92


def test_retired_after_repeated_failures():
    assert skills._threshold({"success": 0.0, "fail": 3.0}) is None


def test_success_prevents_retirement():
    """성공 이력이 있으면 실패가 쌓여도 도태되지 않는다(엄격 모드만)."""
    assert skills._threshold({"success": 1.0, "fail": 4.0}) == 0.92


def test_v1_skill_without_counts():
    """스키마 v1(success/fail 없음)은 기본 문턱으로 동작 — 마이그레이션 불필요."""
    assert skills._threshold({"id": "sk_1", "label": "x"}) == skills.MATCH_THRESHOLD


# ---------- reinforce / penalize (파일 반영) ----------

@pytest.fixture
def skill_store(tmp_path, monkeypatch):
    """임시 skills.json — v1 스킬 하나가 든 저장소."""
    f = tmp_path / "skills.json"
    f.write_text(json.dumps([{
        "id": "sk_v1", "label": "바닷가", "examples": ["바닷가 사진"],
        "search_text": "beach", "media_type": None, "place": None,
        "embedding": None, "uses": 2, "created_at": 0, "last_used": None,
    }], ensure_ascii=False))
    monkeypatch.setattr(skills, "SKILLS_FILE", f)
    monkeypatch.setattr(skills.db, "DATA_DIR", tmp_path)
    skills._cache["skills"] = None  # 캐시 리셋
    yield f
    skills._cache["skills"] = None


def test_reinforce_accumulates_and_persists(skill_store):
    assert skills.reinforce("sk_v1", 1.0)
    assert skills.reinforce("sk_v1", 0.3)
    saved = json.loads(skill_store.read_text())[0]
    assert saved["success"] == pytest.approx(1.3)


def test_penalize_accumulates(skill_store):
    assert skills.penalize("sk_v1", 1.0)
    assert skills.penalize("sk_v1", 0.5)
    saved = json.loads(skill_store.read_text())[0]
    assert saved["fail"] == pytest.approx(1.5)


def test_reinforce_unknown_id(skill_store):
    assert not skills.reinforce("sk_없음", 1.0)
