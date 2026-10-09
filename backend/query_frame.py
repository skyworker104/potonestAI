"""대화 검색 질의에서 '조건'을 규칙으로 뽑는다 — 내용어 해석(LLM·이미지 검색) 전에.

질의 유형과 기대 동작은 claudedocs/search-query-cases.md(사양)와
tests/test_query_cases.py(기준표)에 정의한다. 새 표현은 이 둘에 먼저 추가한다.

원칙
  - 조건(언제 찍었나·언제 올렸나·정렬·개수·즐겨찾기·종류·계절·시간대)은 규칙으로
    확정한다. LLM은 문장마다 해석이 흔들리고, 조건어가 이미지 검색어로 새면
    "최근 업로드된"을 사진 내용으로 찾는 식의 엉뚱한 결과가 난다.
  - 뽑은 말은 문장에서 지워 residual로 돌려준다 — 내용 해석(LLM)은 남은 말만 본다.
  - 같은 종류 조건이 둘이면 앞의 것 하나만(기존 동작과 같음).
"""
import re
from datetime import datetime, timedelta

from . import llm

_NUM = dict(llm._KO_NUM, 스무=20, 서른=30)
_NUM_RE = r"(\d+|한|두|세|네|다섯|여섯|일곱|여덟|아홉|열|스무|서른)"


def _num(s):
    return int(s) if s.isdigit() else _NUM.get(s, 1)


def _day_range(d0, d1=None):
    d1 = d1 or d0
    return d0.strftime("%Y-%m-%d"), d1.strftime("%Y-%m-%dT23:59:59")


# ---------- 올린 시점 (라이브러리에 들어온 시각) ----------
# "받은·저장된"은 넣지 않는다 — "선물 받은 꽃"처럼 내용 표현과 겹친다.
_UPLOAD = re.compile(
    r"(?:새로\s*)?(?:업로드\s*(?:된|한|했던|됐던|되어\s*있는)?"
    r"|올(?:린|라온|라왔던|려진|렸던|려\s*둔|려둔)"
    r"|추가(?:된|한|했던|됐던)"
    r"|백업\s*(?:된|한|했던|됐던|되어\s*있는)"
    r"|전송(?:된|한)|동기화(?:된|한))"
)
# 최근·요즘 — 뒤의 기간 수식("최근 3일")은 _RECENT_N이 먼저 잡는다
_RECENT = re.compile(r"(?:가장\s*)?(?:최근(?:에)?|요즘|근래(?:에)?|최신)")
_RECENT_N = re.compile(rf"(?:최근|지난)\s*{_NUM_RE}\s*(일|주일|주|달|개월|년)(?:\s*(?:간|동안))?")
# 숫자가 낱말에 붙은 기간 — "최근 일주일", "지난 일 년"
_RECENT_WORD = re.compile(r"(?:최근|지난)\s*(일주일|일\s*년)(?:\s*(?:간|동안))?")

# ---------- 날짜 (llm._parse_date_phrase가 못 하는 표현) ----------
_FULL_DAY = re.compile(r"((?:19|20)\d{2})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일")
_MONTH_DAY = re.compile(r"(?<!\d)(\d{1,2})\s*월\s*(\d{1,2})\s*일")
_REL_DAY = re.compile(
    r"(오늘|어제|그저께|그제|엊그제|이번\s*주말|지난\s*주말|저번\s*주말"
    r"|이번\s*주|지난\s*주|저번\s*주|이번\s*달|금주)"
)

# ---------- 정렬·개수 ----------
_OLDEST = re.compile(r"(?:가장\s*|제일\s*)?(?:오래된|옛날|예전|맨\s*처음\s*찍은|처음\s*찍은|첫\s*사진)")
_NEWEST = re.compile(r"(?:가장\s*|제일\s*)?(?:마지막(?:으로)?\s*찍은|최근에?\s*찍은)")
_LIMIT = re.compile(rf"{_NUM_RE}\s*(?:장|개|컷)\s*(?:만|만큼|정도|쯤)?")

# ---------- 속성 ----------
_FAVORITE = re.compile(
    r"(?:즐겨\s*찾기|좋아요|하트|별표|최애)"
    r"(?:\s*(?:에\s*(?:있는|넣은|넣어\s*둔|추가한|추가된)|한|된|해\s*둔|해둔|누른|표시한|목록))?"
)
_SCREENSHOT = re.compile(r"(?:스크린\s*샷|스샷|화면\s*캡[처쳐]|캡[처쳐])(?:\s*(?:한|된|해\s*둔|해둔))?")
_VIDEO = re.compile(r"(?:동영상|영상|비디오)")
_PHOTO_ONLY = re.compile(r"사진만")

# ---------- 계절 ----------
_SEASON = re.compile(r"(?<![가-힣])(봄|여름|가을|겨울)(?:철|날|에는|에|의)?(?=휴가|방학|여행|$|[^가-힣])")
_SEASON_MONTHS = {"봄": [3, 4, 5], "여름": [6, 7, 8], "가을": [9, 10, 11], "겨울": [12, 1, 2]}


# 조건 말을 지우고 홀로 남은 조사 ("지난주에 찍은" → "에 찍은")
_ORPHAN = re.compile(r"(?<![가-힣])(?:에서|에는|에|의|은|는|이|가|을|를|도|만|부터|까지|쯤|동안|간)(?![가-힣])")


def _take(text, m):
    """매칭 부분을 지운 문장."""
    return text[:m.start()] + " " + text[m.end():]


def _relative_day(word, now):
    w = re.sub(r"\s+", "", word)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    monday = today - timedelta(days=today.weekday())
    if w == "오늘":
        return _day_range(today)
    if w == "어제":
        return _day_range(today - timedelta(days=1))
    if w in ("그저께", "그제", "엊그제"):
        return _day_range(today - timedelta(days=2))
    if w in ("이번주", "금주"):
        return _day_range(monday, today)
    if w in ("지난주", "저번주"):
        return _day_range(monday - timedelta(days=7), monday - timedelta(days=1))
    if w == "이번주말":
        sat = monday + timedelta(days=5)
        return _day_range(sat, sat + timedelta(days=1))
    if w in ("지난주말", "저번주말"):
        sat = monday - timedelta(days=2)
        return _day_range(sat, sat + timedelta(days=1))
    if w == "이번달":
        return _day_range(today.replace(day=1), today)
    return None, None


def _recent_n(n, unit, now):
    days = {"일": 1, "주": 7, "주일": 7, "달": 30, "개월": 30, "년": 365}[unit] * n
    return (now - timedelta(days=days)).strftime("%Y-%m-%d"), now.strftime("%Y-%m-%dT%H:%M:%S")


def _date(text, now):
    """날짜 표현 하나 → (from, to, 지운 문장, 표시 말). 없으면 (None, None, text, None)."""
    m = _RECENT_WORD.search(text)
    if m:
        unit = "주" if m.group(1) == "일주일" else "년"
        df, dt = _recent_n(1, unit, now)
        return df, dt, _take(text, m), m.group(0).strip()
    m = _RECENT_N.search(text)
    if m:
        df, dt = _recent_n(_num(m.group(1)), m.group(2), now)
        return df, dt, _take(text, m), m.group(0).strip()
    m = _FULL_DAY.search(text)
    if m:
        try:
            d = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            return (*_day_range(d), _take(text, m), m.group(0))
        except ValueError:
            pass
    m = _MONTH_DAY.search(text)
    if m:
        try:
            d = datetime(now.year, int(m.group(1)), int(m.group(2)))
            if d > now:  # 아직 안 온 날짜면 작년의 그날
                d = d.replace(year=now.year - 1)
            return (*_day_range(d), _take(text, m), m.group(0))
        except ValueError:
            pass
    m = _REL_DAY.search(text)
    if m:
        df, dt = _relative_day(m.group(1), now)
        if df:
            return df, dt, _take(text, m), m.group(0)
    df, dt, span = llm._parse_date_phrase(text)
    if span:
        return df, dt, text.replace(span, " ", 1), span
    return None, None, text, None


def parse(message, now=None):
    """질의 → 조건 프레임.

    반환 키
      date_from/date_to/date_span  촬영 기간 (올린 시점 말이 있으면 added_*로 간다)
      added_from/added_to          라이브러리에 들어온 기간
      upload                       '올린·업로드·백업' 질의인가 (정렬도 올린 시각 기준)
      order                        "taken_desc" | "taken_asc" | "added_desc" | None
      limit                        "10장만" → 10
      favorites, kind("screenshot"), media_type("image"|"video")
      months/season                계절 → 달 목록
      hour_from/hour_to/time_span  시간대 ("아침에")
      greeting                     인사
      residual                     조건 말을 모두 지운 나머지 (내용 해석용)
    """
    now = now or datetime.now()
    text = message.strip()
    f = dict(greeting=llm.quick_meta(text)["greeting"], date_from=None, date_to=None,
             date_span=None, added_from=None, added_to=None, added_span=None,
             upload=False, order=None,
             limit=None, favorites=False, kind=None, media_type=None, months=None,
             season=None, hour_from=None, hour_to=None, time_span=None)

    m = _UPLOAD.search(text)
    if m:
        f["upload"] = True
        text = _take(text, m)

    df, dt, text, span = _date(text, now)
    if span:
        if f["upload"]:
            f["added_from"], f["added_to"], f["added_span"] = df, dt, span
        else:
            f["date_from"], f["date_to"], f["date_span"] = df, dt, span

    m = _OLDEST.search(text)
    if m:
        f["order"] = "taken_asc"
        text = _take(text, m)
    m = _NEWEST.search(text)
    if m:
        f["order"] = f["order"] or "taken_desc"
        text = _take(text, m)
    m = _RECENT.search(text)
    if m:
        text = _take(text, m)
        if not f["upload"]:
            f["order"] = f["order"] or "taken_desc"
    if f["upload"]:
        f["order"] = "added_desc"

    m = _LIMIT.search(text)
    if m:
        f["limit"] = max(1, min(_num(m.group(1)), 1000))
        text = _take(text, m)

    m = _FAVORITE.search(text)
    if m:
        f["favorites"] = True
        text = _take(text, m)
    m = _SCREENSHOT.search(text)
    if m:
        f["kind"] = "screenshot"
        text = _take(text, m)
    m = _VIDEO.search(text)
    if m:
        f["media_type"] = "video"
        text = _take(text, m)
    elif _PHOTO_ONLY.search(text):
        f["media_type"] = "image"
        text = _PHOTO_ONLY.sub(" ", text, count=1)

    m = _SEASON.search(text)
    if m:
        f["season"] = m.group(1)
        f["months"] = _SEASON_MONTHS[m.group(1)]
        text = _take(text, m)

    hf, ht, tspan = llm._parse_time_phrase(text)
    if tspan:
        f["hour_from"], f["hour_to"], f["time_span"] = hf, ht, tspan
        text = text.replace(tspan, " ", 1)

    f["residual"] = re.sub(r"\s+", " ", _ORPHAN.sub(" ", text)).strip()
    return f


def has_filter(f):
    """내용어가 없어도 그것만으로 검색이 되는 조건이 있는가 (기간·시간대 제외 —
    그 둘은 호출부가 따로 본다)."""
    return bool(f["upload"] or f["added_from"] or f["favorites"] or f["kind"]
                or f["months"] or f["limit"] or f["order"] == "taken_asc")
