"""검색 스킬 저장·재사용.

로컬 LLM이 자연어를 해석해 만든 검색조건을 '스킬'로 저장해 두고,
유사한 질문이 오면 LLM 호출 없이 그 스킬을 즉시 재사용한다(쓸수록 빨라짐).

스킬은 '내용 검색어(search_text)'만 캐싱한다. 날짜·인물 같은 변동 요소는
호출부에서 매번 다시 계산하므로 '3년 전' 같은 상대 표현이 시간이 지나도 안전.
"""
import json
import re
import time

import numpy as np

from . import db, indexer

SKILLS_FILE = db.DATA_DIR / "skills.json"

# 보수적으로: 확실히 같은 의미일 때만 스킬 재사용(오매칭보다 LLM 폴백이 안전).
# 0.80은 실측 결과 오탐 발생(무관한 문장이 0.82~0.84로 걸림 — 예: "단풍 산" vs
# "오타루 여행"). 실제 재사용 사례는 0.88+, 무관 오탐은 0.82~0.84로 뚜렷이 갈려
# 0.87로 상향(안전마진 확보, 완전 무관은 0.4~0.65라 리콜 손실 거의 없음).
MATCH_THRESHOLD = 0.87   # 이 이상이면 기존 스킬 재사용
MERGE_THRESHOLD = 0.92   # 이 이상이면 같은 스킬로 보고 예시만 추가
_HANGUL = re.compile(r"[가-힣]")

_cache = {"skills": None}

# 검색 지시어·수식어 제거(문장 구조 유사성 노이즈 차단). 조사는 명사를 깎을 위험이
# 있어 제거하지 않는다("바닷가"의 '가' 등). 임베딩이 조사 정도는 흡수한다.
_STOP = re.compile(
    r"사진|영상|동영상|비디오|이미지"
    r"|보여줘|보여|찾아줘|찾아|검색해|검색|골라줘|골라|줄래|주세요|볼래|해줘|줘"
    r"|찍은|찍힌|찍었던|찍었|나온|있는|관련된|관련"
)


def _strip_terms(question):
    """검색 지시어·수식어·날짜를 제거한 결과 (폴백 없음 — 비면 빈 문자열).

    '제주도 사진 보여줘' → '' (모두 지시어). 내용 유무 판정에 쓴다.
    """
    from . import llm
    text = question.strip()
    _, _, span = llm._parse_date_phrase(text)
    if span:
        text = text.replace(span, " ")
    text = _STOP.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def _core(question):
    """임베딩 비교용 핵심 주제어 (비면 원문 — 임베딩 실패 방지)."""
    return _strip_terms(question) or question.strip()


# ---------- 저장소 ----------

def _load():
    if _cache["skills"] is not None:
        return _cache["skills"]
    if SKILLS_FILE.exists():
        try:
            _cache["skills"] = json.loads(SKILLS_FILE.read_text())
        except Exception:
            _cache["skills"] = []
    else:
        _cache["skills"] = []
    return _cache["skills"]


def _save():
    db.DATA_DIR.mkdir(parents=True, exist_ok=True)
    SKILLS_FILE.write_text(json.dumps(_cache["skills"], ensure_ascii=False, indent=1))


def _embed(text):
    """핵심 주제어 임베딩 (코멘트 검색과 같은 다국어 문장 모델 재활용)."""
    if not indexer.ai_available():
        return None
    try:
        from . import search
        return search.get_comment_model().encode(
            [_core(text)], convert_to_numpy=True, normalize_embeddings=True
        )[0]
    except Exception:
        return None


# ---------- 매칭 / 등록 ----------

def match(question):
    """질문과 의미가 유사한 스킬을 반환. 없으면 None."""
    skills = _load()
    if not skills:
        return None, 0.0
    qv = _embed(question)
    if qv is None:
        return None, 0.0
    best, best_sim = None, 0.0
    for sk in skills:
        emb = sk.get("embedding")
        if not emb:
            continue
        sim = float(np.array(emb, dtype=np.float32) @ qv)
        if sim > best_sim:
            best, best_sim = sk, sim
    if best and best_sim >= MATCH_THRESHOLD:
        return best, best_sim
    return None, best_sim


def add(question, search_text, media_type=None, place=None):
    """검색을 스킬로 저장(자동). 매우 유사한 스킬엔 예시만 추가.

    place: 피드백으로 학습된 위치 선호 {name, bbox} (지명 검색 보정).
    search_text 없이 place만 있어도 저장(순수 위치 스킬).
    """
    if not search_text and not place:
        return None
    qv = _embed(question)
    skills = _load()

    # 이미 거의 같은 스킬이 있으면 예시만 보태고(+위치 갱신) 끝
    if qv is not None:
        for sk in skills:
            emb = sk.get("embedding")
            if emb and float(np.array(emb, dtype=np.float32) @ qv) >= MERGE_THRESHOLD:
                if question not in sk["examples"]:
                    sk["examples"].append(question)
                    sk["examples"] = sk["examples"][-8:]
                if place:
                    sk["place"] = place
                if search_text:
                    sk["search_text"] = search_text
                _save()
                return sk

    sk = {
        "id": f"sk_{int(time.time() * 1000)}",
        "label": question.strip()[:40],
        "examples": [question.strip()],
        "search_text": search_text,
        "media_type": media_type,
        "place": place,
        "embedding": qv.tolist() if qv is not None else None,
        "uses": 0,
        "created_at": int(time.time()),
        "last_used": None,
    }
    skills.append(sk)
    _save()
    return sk


def record_use(skill_id):
    for sk in _load():
        if sk["id"] == skill_id:
            sk["uses"] = sk.get("uses", 0) + 1
            sk["last_used"] = int(time.time())
            _save()
            return


def list_skills():
    """사용 빈도순 스킬 목록 (임베딩 제외)."""
    skills = sorted(_load(), key=lambda s: (-s.get("uses", 0), -s["created_at"]))
    return [
        {k: v for k, v in sk.items() if k != "embedding"}
        for sk in skills
    ]


def delete(skill_id):
    skills = _load()
    n = len(skills)
    _cache["skills"] = [s for s in skills if s["id"] != skill_id]
    if len(_cache["skills"]) != n:
        _save()
        return True
    return False
