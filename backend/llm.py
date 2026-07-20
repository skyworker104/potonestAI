"""클라우드 LLM(Claude)으로 자연어 발화를 검색 의도로 해석.

ANTHROPIC_API_KEY가 없으면 휴리스틱 폴백으로 동작한다
(다국어 CLIP이 한국어 질의를 직접 처리하므로 검색 자체는 가능).
"""
import json
import os
import re
from datetime import datetime, timedelta

SYSTEM_PROMPT = """\
너는 음성 대화형 사진 관리 비서다. 사용자의 한국어 발화를 분석해 JSON으로만 답하라.

JSON 스키마:
{
  "intent": "search" | "chat",
  "search_text": "사진 내용의 핵심 주제어, 원본 언어(한국어) 그대로. search가 아니면 null",
  "date_from": "ISO8601 또는 null",
  "date_to": "ISO8601 또는 null",
  "media_type": "image" | "video" | null,
  "reply": "사용자에게 음성으로 읽어줄 자연스러운 한국어 한두 문장"
}

규칙:
- 사진/영상을 찾아달라는 요청이면 intent=search.
- '작년 여름', '지난달', '3년 전' 같은 상대 날짜는 반드시 오늘 날짜 기준으로 환산해 date_from/date_to를 채울 것.
  예) 오늘이 2026년이면 '3년 전' → date_from=2023-01-01, date_to=2023-12-31T23:59:59.
  '재작년'=2년 전, '작년'=1년 전. 날짜 조건이 있으면 절대 비우지 말 것.
- search_text에는 날짜 표현을 넣지 말고 사물/장소/상황만 원본 언어(한국어) 그대로.
  이미지 검색 엔진(SigLIP2)이 다국어 네이티브라 번역이 필요 없다.
- 인사말이나 잡담이면 intent=chat, 짧고 친근하게 reply.
- reply에서 검색 결과 개수는 모르므로 언급하지 말 것 (예: "바닷가 사진을 찾아볼게요").
"""


def _claude_parse(message: str):
    import anthropic

    client = anthropic.Anthropic()
    today = datetime.now().strftime("%Y-%m-%d")
    resp = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=400,
        system=SYSTEM_PROMPT + f"\n오늘 날짜: {today}",
        messages=[{"role": "user", "content": message}],
    )
    text = resp.content[0].text
    m = re.search(r"\{.*\}", text, re.DOTALL)
    return json.loads(m.group(0)) if m else None


GREETINGS = ("안녕", "하이", "헬로", "고마워", "감사")

_KO_NUM = {"한": 1, "두": 2, "세": 3, "네": 4, "다섯": 5, "여섯": 6,
           "일곱": 7, "여덟": 8, "아홉": 9, "열": 10}


def _year_range(year):
    return f"{year}-01-01", f"{year}-12-31T23:59:59"


def _month_range(year, month):
    import calendar
    last = calendar.monthrange(year, month)[1]
    return f"{year}-{month:02d}-01", f"{year}-{month:02d}-{last}T23:59:59"


# 시간대 표현 → (시작시, 끝시). 밤은 자정을 넘겨 wrap(20→05).
_TIME_WORDS = {
    "새벽": (4, 8), "아침": (6, 11), "오전": (6, 12), "점심": (11, 14),
    "오후": (12, 18), "저녁": (17, 21), "밤": (20, 5), "심야": (23, 4),
}


def _parse_time_phrase(msg):
    """"아침에 찍은", "밤에" 같은 시간대 표현 → (hour_from, hour_to, 매칭문자열).

    조사('에/무렵/쯤/녘')가 붙은 경우만 시간 필터로 해석한다 —
    "저녁 사진"(음식), "밤 야경"(야경) 같은 내용어 용법과 구분하기 위함.
    """
    m = re.search(r"(새벽|아침|오전|점심|오후|저녁|밤|심야)\s*(에|무렵|쯤|녘)", msg)
    if not m:
        return None, None, None
    hf, ht = _TIME_WORDS[m.group(1)]
    return hf, ht, m.group(0)


def _parse_date_phrase(msg):
    """발화에서 상대/절대 날짜 표현을 찾아 (date_from, date_to, 매칭문자열) 반환.

    "3년 전", "재작년", "작년 여름", "2019년", "지난달" 등 처리.
    매칭문자열은 검색 키워드에서 제거하는 데 쓴다.
    """
    now = datetime.now()

    # "2018년 4월" — 연+월 조합 (연도 단독 패턴보다 먼저 검사해야 함)
    m = re.search(r"((?:19|20)\d{2})\s*년\s*(\d{1,2})\s*월", msg)
    if m and 1 <= int(m.group(2)) <= 12:
        df, dt = _month_range(int(m.group(1)), int(m.group(2)))
        return df, dt, m.group(0)

    # "작년 4월" / "재작년 4월" / "올해 4월"
    m = re.search(r"(재작년|작년|올해|금년)\s*(\d{1,2})\s*월", msg)
    if m and 1 <= int(m.group(2)) <= 12:
        yr = now.year - (2 if m.group(1) == "재작년" else 1 if m.group(1) == "작년" else 0)
        df, dt = _month_range(yr, int(m.group(2)))
        return df, dt, m.group(0)

    # "4월", "지난 4월" — 가장 최근에 지나간 그 달 (이번 달 포함)
    #   숫자 앞이 숫자면(2018 등) 제외, "N개월 전"의 월과도 구분됨(개 글자가 사이에)
    m = re.search(r"(?<![\d])(\d{1,2})\s*월(?!\s*전)", msg)
    if m and 1 <= int(m.group(1)) <= 12:
        mo = int(m.group(1))
        yr = now.year if mo <= now.month else now.year - 1
        df, dt = _month_range(yr, mo)
        return df, dt, m.group(0)

    # 절대 연도: "2019년", "2019년도"
    m = re.search(r"(19|20)\d{2}\s*년도?", msg)
    if m:
        yr = int(re.search(r"\d{4}", m.group(0)).group(0))
        df, dt = _year_range(yr)
        return df, dt, m.group(0)

    # "N년 전" / "N달(개월) 전" / "N주 전" / "N일 전" (숫자 또는 한글수)
    m = re.search(r"(\d+|한|두|세|네|다섯|여섯|일곱|여덟|아홉|열)\s*(년|개월|달|주|일)\s*전", msg)
    if m:
        n = int(m.group(1)) if m.group(1).isdigit() else _KO_NUM.get(m.group(1), 1)
        unit = m.group(2)
        if unit == "년":
            # "3년 전" = 그 해(now.year - n) 전체로 해석
            df, dt = _year_range(now.year - n)
        elif unit in ("개월", "달"):
            target = now - timedelta(days=30 * n)
            df = (target - timedelta(days=20)).strftime("%Y-%m-%d")
            dt = (target + timedelta(days=20)).strftime("%Y-%m-%dT23:59:59")
        elif unit == "주":
            target = now - timedelta(weeks=n)
            df = (target - timedelta(days=4)).strftime("%Y-%m-%d")
            dt = (target + timedelta(days=4)).strftime("%Y-%m-%dT23:59:59")
        else:  # 일
            target = now - timedelta(days=n)
            df = target.strftime("%Y-%m-%d")
            dt = target.strftime("%Y-%m-%dT23:59:59")
        return df, dt, m.group(0)

    # 명시적 상대어
    if "재작년" in msg:
        df, dt = _year_range(now.year - 2)
        return df, dt, "재작년"
    if "작년" in msg:
        df, dt = _year_range(now.year - 1)
        return df, dt, "작년"
    if "올해" in msg or "금년" in msg:
        key = "올해" if "올해" in msg else "금년"
        return f"{now.year}-01-01", None, key
    if "지난달" in msg or "지난 달" in msg:
        first = now.replace(day=1)
        prev_end = first - timedelta(seconds=1)
        return (prev_end.replace(day=1).strftime("%Y-%m-%d"),
                prev_end.isoformat(), "지난달" if "지난달" in msg else "지난 달")
    return None, None, None

# 사진 검색 도메인 한→영 사전 (긴 키 우선 매칭).
# CLIP 네이티브 텍스트 인코더가 영어에서 훨씬 정확하므로,
# LLM이 없을 때 핵심 키워드를 영어로 변환해 검색 품질을 확보한다.
KO_EN = [
    ("불꽃놀이", "fireworks"), ("폭죽", "fireworks"),
    ("바닷가", "beach"), ("해수욕장", "beach"), ("해변", "beach"), ("바다", "sea ocean"),
    ("노을", "sunset"), ("일몰", "sunset"), ("석양", "sunset"), ("일출", "sunrise"),
    ("야경", "city skyline at night"), ("밤", "night"),
    ("강아지", "dog"), ("멍멍이", "dog"), ("개", "dog"), ("고양이", "cat"), ("냥이", "cat"),
    ("벚꽃", "cherry blossom"), ("장미", "rose"), ("꽃", "flowers"),
    ("설경", "snowy landscape"), ("눈사람", "snowman"), ("눈", "snow"), ("겨울", "winter"),
    ("등산", "mountain hiking"), ("산", "mountain"), ("단풍", "autumn foliage"),
    ("폭포", "waterfall"), ("계곡", "valley stream"),
    ("바베큐", "barbecue grilled meat"), ("고기", "grilled meat"),
    ("파스타", "pasta"), ("스파게티", "spaghetti"), ("피자", "pizza"), ("케이크", "cake"),
    ("맛있는", "delicious"), ("음식", "food dish"), ("저녁", "dinner food"),
    ("점심", "lunch food"), ("아침", "morning"),
    ("커피", "coffee latte"), ("라떼", "latte"), ("카페", "cafe"),
    ("궁궐", "korean palace"), ("경복궁", "korean palace"), ("한옥", "korean traditional house"),
    ("절", "temple"), ("교회", "church"),
    ("아기", "baby"), ("아이", "child"), ("가족", "family"), ("사람", "person"),
    ("셀카", "selfie"), ("결혼", "wedding"), ("생일", "birthday party"),
    ("자동차", "car"), ("자전거", "bicycle"), ("오토바이", "motorcycle"),
    ("비행기", "airplane"), ("기차", "train"), ("배", "boat ship"),
    ("하늘", "sky"), ("구름", "clouds"), ("비", "rain"), ("무지개", "rainbow"),
    ("호수", "lake"), ("강", "river"), ("섬", "island"),
    ("공원", "park"), ("놀이터", "playground"), ("숲", "forest"), ("나무", "trees"),
    ("도시", "city"), ("거리", "street"), ("건물", "buildings"), ("다리", "bridge"),
    ("풍경", "landscape scenery"), ("여행", "travel"),
]


def _ko_to_en(text: str):
    """사전 기반 키워드 변환. 매칭이 하나도 없으면 None."""
    terms = []
    remaining = text
    for ko, en in KO_EN:
        if ko in remaining:
            if en not in terms:
                terms.append(en)
            remaining = remaining.replace(ko, " ")
    return " ".join(terms) if terms else None


def _fallback_parse(message: str):
    msg = message.strip()
    if any(g in msg for g in GREETINGS) and len(msg) <= 12:
        return {
            "intent": "chat",
            "search_text": None,
            "date_from": None,
            "date_to": None,
            "media_type": None,
            "reply": "안녕하세요! 찾고 싶은 사진이나 영상을 말씀해 주세요.",
        }

    media_type = None
    if any(w in msg for w in ("영상", "동영상", "비디오")):
        media_type = "video"

    date_from, date_to, date_span = _parse_date_phrase(msg)

    # 검색 지시어 + 날짜 표현을 제거해 핵심 키워드만 남긴다
    clean = re.sub(
        r"(사진|영상|동영상|비디오)?\s*(을|를|좀|들)?\s*(찾아|보여|검색해|골라)\s*(줘|줄래|주세요|봐|볼래)?",
        "",
        msg,
    )
    if date_span:
        clean = clean.replace(date_span, " ")
    clean = re.sub(r"\s+", " ", clean).strip() or msg

    # CLIP 검색 품질을 위해 가능하면 영어 키워드로 변환
    translated = _ko_to_en(clean)

    return {
        "intent": "search",
        "search_text": translated or clean,
        "date_from": date_from,
        "date_to": date_to,
        "media_type": media_type,
        "reply": f"'{clean}' 관련 {'영상' if media_type == 'video' else '사진'}을 찾아볼게요.",
    }


def detect_feedback(message):
    """검색 결과에 대한 교정 피드백 의도 감지. 없으면 None.

    type: 'location'(위치로 정확히), 'wrong'(틀림/섞임), 'only'(특정 지역만),
          'exclude'(특정 지역 빼기)
    """
    t = message.replace(" ", "")
    # "X 아닌 거/것 빼줘" → X만 남기기
    m = re.search(r"(아닌|아니).{0,4}(빼|제외|지워|없애|말고)", t)
    if m:
        return {"type": "only"}
    # "위치로/장소로/지도로/GPS로 (다시) 찾아"
    if re.search(r"(위치|장소|지도|gps|좌표)(로|기준|으로)", t) or "위치로" in t:
        return {"type": "location"}
    # 틀림/섞임 일반 불만
    if re.search(r"(틀렸|틀려|아니야|아니잖|섞였|섞여|잘못|이상해|안맞|다른지역|딴데|엉뚱)", t):
        return {"type": "wrong"}
    # "X만 보여줘 / X 것만"
    if re.search(r"(만|것만|거만)(보여|찾|남)", t):
        return {"type": "only"}
    return None


def quick_meta(message):
    """LLM 없이 즉시 추출하는 메타 — 인사, 날짜 범위, 시간대, 미디어 종류.

    스킬 재사용 경로에서 날짜·미디어를 매번 다시 계산하는 데 쓴다
    (상대 날짜 '3년 전' 등이 시간 지나도 안전하도록).
    """
    msg = message.strip()
    is_greeting = any(g in msg for g in GREETINGS) and len(msg) <= 12
    media_type = "video" if any(w in msg for w in ("영상", "동영상", "비디오")) else None
    df, dt, span = _parse_date_phrase(msg)
    hf, ht, tspan = _parse_time_phrase(msg)
    return {
        "greeting": is_greeting, "media_type": media_type,
        "date_from": df, "date_to": dt, "date_span": span,
        "hour_from": hf, "hour_to": ht, "time_span": tspan,
    }


_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}")
_CJK_LEAK = re.compile(r"[一-鿿぀-ヿ]")  # 한자/히라가나/가타카나 — 소형 다국어 모델의 언어 혼입 감지


def _clean_date(v):
    """ISO 날짜 형식이 아니면 버린다 (소형 로컬 LLM이 '작년' 같은 원문을 그대로 넣는 경우 방지)."""
    return v if isinstance(v, str) and _ISO_DATE.match(v) else None


def _normalize(result):
    """LLM 출력의 누락 키를 채우고 타입을 보정."""
    reply = result.get("reply") or "사진을 찾아볼게요."
    if _CJK_LEAK.search(reply):  # 소형 다국어 모델이 간혹 중/일본어를 섞어 냄 — 안전한 문구로 대체
        reply = "찾아볼게요."
    return {
        "intent": result.get("intent") or "search",
        "search_text": result.get("search_text") or None,
        "date_from": _clean_date(result.get("date_from")),
        "date_to": _clean_date(result.get("date_to")),
        "media_type": result.get("media_type") or None,
        "person": result.get("person") or None,
        "reply": reply,
    }


def _try_openrouter(message, history, today):
    from . import openrouter
    if not openrouter.available():
        return None
    try:
        result = openrouter.parse(message, history=history, today=today)
        if result and "intent" in result:
            out = _normalize(result)
            out["engine"] = "openrouter"
            return out
    except Exception:
        pass
    return None


def _try_local(message, history, today):
    from . import local_llm
    if not local_llm.available():
        return None
    try:
        result = local_llm.parse(message, history=history, today=today)
        if result and "intent" in result:
            out = _normalize(result)
            out["engine"] = "local-llm"
            return out
    except Exception:
        pass
    return None


def _try_claude(message):
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    try:
        result = _claude_parse(message)
        if result and "intent" in result:
            out = _normalize(result)
            out["engine"] = "claude"
            return out
    except Exception:
        pass
    return None


def parse(message: str, history=None):
    """발화 → 의도 구조체.

    설정(engine_mode)에 따라 엔진을 선택한다:
      auto       → OpenRouter(키 有) → 로컬 → Claude → 휴리스틱
      openrouter → OpenRouter → 휴리스틱
      local      → 로컬 → 휴리스틱
      claude     → Claude → 휴리스틱
    어떤 엔진도 실패하면 항상 휴리스틱으로 폴백한다(검색은 CLIP이 직접 처리).
    history: 대화 맥락 [{role, content}, ...] (후속 질문 처리용).
    """
    from . import settings

    today = datetime.now().strftime("%Y-%m-%d")
    mode = settings.load()["engine_mode"]

    if mode == "auto":
        chain = (
            lambda: _try_openrouter(message, history, today),
            lambda: _try_local(message, history, today),
            lambda: _try_claude(message),
        )
    elif mode == "openrouter":
        chain = (lambda: _try_openrouter(message, history, today),)
    elif mode == "local":
        chain = (lambda: _try_local(message, history, today),)
    elif mode == "claude":
        chain = (lambda: _try_claude(message),)
    else:
        chain = ()

    for attempt in chain:
        out = attempt()
        if out:
            return out

    # 휴리스틱 폴백
    result = _fallback_parse(message)
    result.setdefault("person", None)
    result["engine"] = "heuristic"
    return result
