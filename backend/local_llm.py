"""범용 로컬 LLM 어댑터 (OpenAI 호환 API).

LM Studio(기본 :1234), Ollama(:11434), 기타 OpenAI 호환 서버를 같은 구조로 연결.
서버를 자동 탐지하고, 사용 가능하면 자연어 발화를 검색 의도(JSON)로 변환한다.
어떤 것도 없으면 호출부가 휴리스틱 폴백을 쓴다.

환경변수:
  LOCAL_LLM_BASE   예) http://localhost:1234/v1  (미지정 시 자동 탐지)
  LOCAL_LLM_MODEL  사용할 모델 id (미지정 시 서버의 첫 모델)
  LOCAL_LLM_KEY    필요 시 API 키 (기본 'lm-studio')
"""
import json
import os
import re
import urllib.error
import urllib.request

# 자동 탐지 후보 (base_url, 표시이름)
_CANDIDATES = [
    ("http://localhost:1234/v1", "LM Studio"),
    ("http://localhost:11434/v1", "Ollama"),
    ("http://localhost:8080/v1", "llama.cpp"),
]

_TIMEOUT = 150  # 첫 호출은 모델 콜드 로딩(수십 초)이 있을 수 있어 넉넉히
_cache = {"checked": False, "base": None, "model": None, "name": None}
_supports_json_format = {"v": None}  # 서버의 response_format 지원 여부 캐시


def _http_json(url, payload=None, timeout=_TIMEOUT, method=None, key=None, extra_headers=None):
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {key or os.environ.get('LOCAL_LLM_KEY', 'lm-studio')}",
    }
    if extra_headers:
        headers.update(extra_headers)
    req = urllib.request.Request(
        url, data=data, method=method or ("POST" if data else "GET"), headers=headers,
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _list_models(base):
    try:
        d = _http_json(f"{base}/models", timeout=4)
        return [m["id"] for m in d.get("data", [])]
    except Exception:
        return None


def discover(force=False):
    """사용 가능한 로컬 LLM 서버/모델 탐지. (base, model, name) 또는 (None,None,None)."""
    if _cache["checked"] and not force:
        return _cache["base"], _cache["model"], _cache["name"]

    base = os.environ.get("LOCAL_LLM_BASE")
    chosen = None
    if base:
        models = _list_models(base)
        if models is not None:
            chosen = (base, "사용자 지정", models)
    else:
        for cand_base, name in _CANDIDATES:
            models = _list_models(cand_base)
            if models:
                chosen = (cand_base, name, models)
                break

    if not chosen:
        _cache.update(checked=True, base=None, model=None, name=None)
        return None, None, None

    base, name, models = chosen
    model = os.environ.get("LOCAL_LLM_MODEL")
    if not model or model not in models:
        # 채팅용 모델 우선 (임베딩 모델 제외)
        non_embed = [m for m in models if "embed" not in m.lower()]
        model = (non_embed or models)[0]
    _cache.update(checked=True, base=base, model=model, name=name)
    return base, model, name


def available():
    base, model, _ = discover()
    return bool(base and model)


# 간결한 프롬프트 + few-shot 예시 (작은 로컬 모델은 예시 없이는 스키마 설명 문구를
# 값 자리에 그대로 베끼는 경향이 있어, 실제 출력 형태를 보여줘야 안정적으로 따라온다).
# 이미지 의미검색은 SigLIP2(다국어)라 search_text는 번역 없이 원본 언어(한국어)로 둔다.
# date_from/date_to는 항상 null — 날짜는 llm.quick_meta가 정규식으로 먼저 확정하므로
# LLM이 잘못 채우면 오히려 그 값이 우선 적용돼(main.py) 틀릴 수 있다.
SYSTEM_PROMPT = """\
너는 사진 검색 비서다. 사용자 발화를 분석해 JSON 객체 딱 하나만 출력한다. \
다른 설명, 코드블록, 줄바꿈 문구는 절대 출력하지 않는다.

intent는 "search"(사진/영상 찾기) 또는 "chat"(인사·잡담) 둘 중 하나만 적는다.
search_text는 사진 내용의 핵심 주제어만 원본 언어 그대로 적는다(지시어·날짜·인물 제외).
인물 이름만으로 찾는 요청이면(다른 내용어가 없으면) search_text는 null로 둔다.
date_from과 date_to는 상대날짜 표현("작년","지난달" 등)이 있어도 계산하지 말고
**항상 null**로 둔다(날짜 계산은 별도 로직이 담당한다).
media_type은 "영상/동영상/비디오"라는 말이 있으면 "video", 없으면 null.
person은 발화에 사람 이름이 명시돼 있으면 그 이름, 없으면 null.
reply는 한국어 한 문장(개수 언급 금지).

예시1)
입력: 강아지 사진 찾아줘
출력: {"intent":"search","search_text":"강아지","date_from":null,"date_to":null,"media_type":null,"person":null,"reply":"강아지 사진을 찾아드릴게요."}

예시2)
입력: 안녕
출력: {"intent":"chat","search_text":null,"date_from":null,"date_to":null,"media_type":null,"person":null,"reply":"안녕하세요! 찾고 싶은 사진을 말씀해 주세요."}

예시3)
입력: 작년 여름에 민준이랑 바닷가에서 찍은 동영상
출력: {"intent":"search","search_text":"바닷가","date_from":null,"date_to":null,"media_type":"video","person":"민준","reply":"민준님과 바닷가에서 찍은 영상을 찾아볼게요."}

예시4)
입력: 민준이 나온 사진 있어?
출력: {"intent":"search","search_text":null,"date_from":null,"date_to":null,"media_type":null,"person":"민준","reply":"민준님이 나온 사진을 찾아드릴게요."}

직전 대화 맥락이 있으면 후속 요청의 조건을 이어서 누적 반영한다."""


def _try_load(s):
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        try:  # 흔한 오류(후행 콤마) 보정
            return json.loads(re.sub(r",\s*([}\]])", r"\1", s))
        except json.JSONDecodeError:
            return None


def _extract_json(text):
    """텍스트에서 'intent' 키를 가진 JSON 객체를 찾는다.

    reasoning 모델은 사고 과정에 예시 JSON을 여러 개 쓰므로, 균형 잡힌
    중괄호 블록들을 뒤에서부터(=최종 답) 검사해 첫 유효 객체를 반환한다.
    """
    if not text:
        return None
    # 균형 중괄호 블록 수집
    blocks, stack = [], []
    for i, ch in enumerate(text):
        if ch == "{":
            stack.append(i)
        elif ch == "}" and stack:
            start = stack.pop()
            if not stack:
                blocks.append(text[start:i + 1])
    for block in reversed(blocks):  # 마지막(최종 답) 우선
        obj = _try_load(block)
        if isinstance(obj, dict) and "intent" in obj:
            return obj
    # 폴백: 전체에서 가장 바깥 매칭
    m = re.search(r"\{.*\}", text, re.DOTALL)
    return _try_load(m.group(0)) if m else None


def parse(message, history=None, today=None, base=None, model=None, key=None,
          extra_headers=None):
    """발화→의도 dict. 실패 시 None(호출부가 폴백). history: [{role,content}...].

    base/model/key를 넘기면 그 서버(OpenRouter 등 OpenAI 호환)로 직접 호출하고,
    없으면 로컬 서버를 자동 탐지한다.
    """
    if base is None or model is None:
        base, model, _ = discover()
    if not base or not model:
        return None

    sys_prompt = SYSTEM_PROMPT
    if today:
        sys_prompt += f"\n오늘 날짜: {today}"

    messages = [{"role": "system", "content": sys_prompt}]
    for turn in (history or [])[-6:]:  # 최근 3턴(user+assistant)만
        messages.append(turn)
    messages.append({"role": "user", "content": message})

    # reasoning(사고형) 모델은 생각에 토큰을 쓰므로 넉넉히 (일반 모델은 일찍 멈춤)
    payload = {
        "model": model, "messages": messages,
        "temperature": 0, "max_tokens": 1024,
    }
    # response_format(json_object)을 지원하는 서버에서만 사용 (Gemma+LM Studio는 미지원→400)
    if _supports_json_format["v"]:
        payload["response_format"] = {"type": "json_object"}

    try:
        d = _http_json(f"{base}/chat/completions", payload, key=key, extra_headers=extra_headers)
    except urllib.error.HTTPError as e:
        if e.code == 400 and "response_format" in payload:
            # 이 서버는 json_object 미지원 → 비활성화하고 1회 재시도
            _supports_json_format["v"] = False
            payload.pop("response_format", None)
            try:
                d = _http_json(f"{base}/chat/completions", payload, key=key,
                               extra_headers=extra_headers)
            except Exception:
                return None
        else:
            return None
    except Exception:
        return None

    msg = d["choices"][0]["message"]
    # 일반 모델은 content에, reasoning 모델은 사고 후 content에 답을 둔다.
    # content가 비면(사고 도중 잘림 등) reasoning_content에서도 JSON을 찾는다.
    for field in ("content", "reasoning_content", "reasoning"):
        result = _extract_json(msg.get(field) or "")
        if result and "intent" in result:
            return result
    return None
