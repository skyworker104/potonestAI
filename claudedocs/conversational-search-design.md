# 대화형 사진 검색 고도화 설계서

> **목표**: "작년 사진 찾아줘" 같은 자연어 대화를 시간·장소·내용 추론으로 해석하고,
> 결과를 스스로 평가해 재시도하며, 사용자 피드백(명시적+암묵적)을 학습해
> **쓸수록 정확해지는** 검색 시스템.
>
> 작성일: 2026-07-22 · 대상 브랜치: main · 상태: 검토 대기

---

## 1. 현재 아키텍처 요약 (변경 기준점)

```
/api/chat (main.py:351)
 ├─ ① llm.detect_feedback()        부정 피드백 감지 → _handle_feedback (위치 교정만)
 ├─ ② llm.quick_meta()             정규식: 날짜(작년/N년 전…)·시간대·미디어타입
 ├─ ③ db.match_person_name()       인물 이름 매칭
 ├─ ④ places.detect()              지명 사전 → GPS bbox
 ├─ ⑤ skills.match()               임베딩 유사도 ≥0.87 → LLM 생략 재사용
 ├─ ⑥ llm.parse()                  OpenRouter→로컬→Claude→휴리스틱 폴백 체인
 ├─ ⑦ _run_search → search.find()  이미지임베딩+코멘트+캡션+OCR+이름매치 병합
 └─ ⑧ skills.add()                 LLM 해석 결과를 스킬로 자동 캐싱
```

**이미 잘 되어 있는 것 (유지)**
- 시간 추론: 정규식(`llm.py:93 _parse_date_phrase`) + LLM 프롬프트 오늘날짜 주입의 이중화
- 지명↔내용 분리 (`place_text`가 의미검색에 새지 않게 메타 필터로만)
- 저사양 폴백 체인 (LLM 없어도 CLIP+정규식으로 동작)
- 스킬 캐시 = 반복 질의 고속화

**핵심 문제 (이번 설계의 대상)**
| # | 문제 | 위치 |
|---|---|---|
| G1 | 검색이 원샷 — 0장이면 사과만 하고 끝 | `main.py:334` |
| G2 | 긍정 피드백·열람 신호를 전혀 학습하지 않음 | `llm.py:254 detect_feedback` |
| G3 | 스킬에 성공률 개념이 없어 나쁜 스킬이 계속 재사용 | `skills.py` 스키마 |
| G4 | "그 중에서 ~만" 직전 결과 정제 불가 | `main.py:315 _last_search` |
| G5 | LLM이 결과를 보지 못함 — 전략 재선택 불가 | 구조적 |

---

## 2. Phase 1 — 결과 인지 재시도 루프 (자동 완화)

### 2.1 설계 원칙
- **LLM 불필요** — 순수 코드 사다리(ladder). 태블릿/Termux에서도 동작.
- 재시도는 **조건을 하나씩만 완화**하고, 완화한 사실을 답변에 명시
  ("작년 사진은 없지만, 재작년 겨울 사진 12장을 찾았어요").

### 2.2 트리거 조건
`_run_search` 결과가 아래 중 하나일 때:
- `len(results) == 0`
- `search_text` 있음 && 최고점수 < `score_threshold + 0.5*score_margin`
  (임계 턱걸이 = 사실상 무관한 결과일 확률 높음. embedder.params() 기준)

### 2.3 완화 사다리 (순서 고정, 첫 성공에서 정지)

```
R1. 날짜 완화     date_from/to 있으면 → 범위를 앞뒤로 1단계 확장
                  (월 단위였으면 ±1개월, 연 단위였으면 ±1년, 그래도 0장이면 날짜 제거)
R2. 장소 완화     bbox(GPS) 검색이 0장 → place_text 메타 필터로 강등
                  place_text 필터가 0장 → 필터 제거하고 search_text에 지명 병합
R3. 내용어 재시도  search_text를 _to_english() 변환/동의어로 1회 재검색
                  (CLIP-ONNX 백엔드에서 특히 유효)
R4. 근사 제시     전부 실패 → 날짜 조건만으로 "가장 가까운 시기" 20장 제시
```

### 2.4 구현 위치
- `backend/search_retry.py` (신규, ~120줄): `run_with_retry(plan) -> (results, applied_relaxations)`
- `main.py`의 `_run_search`가 이를 감싸도록 변경. `reply` 생성부에
  완화 내역 문구 추가 (`applied_relaxations`를 한국어 문장으로 매핑).
- 응답 JSON에 `"relaxed": ["date_widened", ...]` 필드 추가 (프론트 배지용, 선택).

### 2.5 응답 예시
| 상황 | 기존 | 개선 |
|---|---|---|
| 작년 바닷가 0장 | "찾지 못했어요" | "작년엔 없지만 재작년 바닷가 9장을 찾았어요" |
| 제주 GPS 0장 (GPS 없는 사진들) | "찾지 못했어요" | "위치정보가 있는 사진 중엔 없어서, 폴더·앨범명에서 '제주' 사진 31장을 찾았어요" |

---

## 3. Phase 2 — 대화형 정제 ("그 중에서")

### 3.1 감지
`main.py chat()`의 ①과 ② 사이에 정제 의도 감지 추가:

```python
_REFINE = re.compile(r"(그\s*중|그중|거기서|여기서|이\s*중|얘네|이것들|결과)\s*(에서|중)?")
```
- 매칭 && `_last_search.result_ids` 존재 → **정제 모드**
- 정제 모드에서는 지시어를 제거한 나머지로 quick_meta/인물/지명/스킬/LLM 해석을
  동일하게 수행하되, `search.find(only_ids=_last_search.result_ids)` 로 실행.
- 결과는 다시 `_last_search`에 저장 → **연쇄 정제 가능**
  ("작년 사진" → "그 중 제주도" → "거기서 밤에 찍은 것만").

### 3.2 `_last_search` 확장
```python
_last_search = {
    ...기존 필드,
    "result_ids": [...],      # 이미 있음 (main.py:344)
    "turn": int,              # 정제 깊이 (답변 문구용: "그 중에서 12장이에요")
    "skill_id": str | None,   # ★ Phase 3에서 피드백 귀속에 사용
}
```

### 3.3 주의점
- 정제 감지는 **스킬 매칭보다 먼저** 해야 함 — "그 중에 강아지"가
  기존 '강아지' 스킬에 가로채여 전체 검색이 되는 것 방지.
- 음성 인식이 "그중에"를 "그 중에"/"그중애" 등으로 적는 변형 → `\s*` 및
  기존 `_lev1` 스타일 허용은 과설계. 정규식 변형 2~3개면 충분.

---

## 4. Phase 3 — 양방향 피드백 학습 (핵심)

### 4.1 신호 소스 3종

| 신호 | 감지 | 강도 |
|---|---|---|
| 명시적 긍정 | "맞아/그거야/좋아/잘 찾았/고마워/딱이야" (검색 직후 턴) | +1.0 |
| 명시적 부정 | 기존 `detect_feedback` (틀렸어/아니야/섞였어…) | -1.0 |
| 암묵적 긍정 | 검색 결과에서 라이트박스 열람 발생 | +0.3 (상한 1회/검색) |

### 4.2 `detect_feedback` 확장 (`llm.py`)
```python
def detect_feedback(message):
    # 기존 4종(location/wrong/only/exclude) 유지 + 추가:
    # type: 'positive'  — "맞아", "그거야", "좋아", "잘 찾았네", "고마워", "딱이네"
    #   단, 뒤에 새 검색어가 이어지면(길이 >12자 등) positive 아님 → 일반 검색으로
```
- 긍정은 **감사·확인 단독 발화**일 때만. "좋아 그럼 이제 제주도 사진"은 새 검색.

### 4.3 스킬 스키마 v2 (`skills.py`)
```jsonc
{
  "id": "sk_...",
  "label": "...", "examples": [...], "search_text": "...",
  "media_type": null, "place": null, "place_text": null,
  "embedding": [...], "uses": 3,
  // ---- 신규 ----
  "success": 2.3,        // 피드백 가중 누적 (positive +1, 열람 +0.3)
  "fail": 1.0,           // 부정 누적
  "version": 2
}
```
- **마이그레이션**: `_load()`에서 키 없으면 `success=0, fail=0` 기본값 주입.
  기존 skills.json 그대로 호환 (파일 재작성 불필요, 다음 _save 때 자연 승급).

### 4.4 신뢰도 기반 동적 매칭 임계값
```python
def _threshold(sk):
    """성공률에 따라 재사용 문턱을 조정. 기본 0.87.
    성공 우세(성공-실패 ≥ 2) → 0.85 (더 관대하게 재사용)
    실패 우세(실패 > 성공)   → 0.92 (거의 정확히 같은 질문만)
    실패 ≥ 3 && 성공 0       → 매칭 제외 (사실상 도태)"""
```
- `skills.match()`가 스킬별 `_threshold(sk)`와 비교하도록 변경.
- 완전 삭제는 하지 않음(사용자가 도움말 UI에서 직접 삭제 가능) —
  자동 삭제는 오판 시 복구 불가라 **도태(비활성)** 로만.

### 4.5 피드백 귀속 흐름
```
검색 실행 → _last_search.skill_id = 사용된 스킬 id (LLM 해석이 스킬로 저장된 경우 그 id)
긍정 피드백 → skills.reinforce(skill_id, +1.0)
             + 스킬이 없던 검색(휴리스틱 등)이면 이번 해석을 즉시 스킬로 저장
부정 피드백 → skills.penalize(skill_id, 1.0)
             + 기존 _handle_feedback 위치 교정 로직 수행
             + ★스킬 경유 검색이었다면 스킬을 우회하고 LLM 재해석 1회 시도
```

### 4.6 암묵적 신호 API
- `POST /api/feedback/view  {media_id}` (신규, main.py ~10줄)
- 프론트 `lightbox.js openLightbox()`에서 `state.view === "search"`일 때만 호출 (1줄).
- 백엔드: `_last_search.result_ids`에 포함된 열람이면 해당 `skill_id`에 +0.3
  (검색당 1회 상한 — 슬라이드쇼 연속 열람으로 과대 학습 방지).

### 4.7 위치 선호 학습 (기존 유지 + 보강)
- 기존: 부정 피드백 시 `skills.add(place=...)` — 유지.
- 보강: `_handle_feedback`에서 학습한 지명이 `places` 사전에 없으면
  결과 사진들의 GPS 분포로 bbox를 추정해 `places.add_user_place()` 호출
  (이미 함수 존재, 호출부만 추가).

---

## 5. Phase 4 — LLM 검색 플래너 (조건부 에이전틱 루프)

> Phase 1~3으로 해결 안 되는 잔여 케이스 전용. **기본 경로 아님.**

### 5.1 발동 조건 (전부 충족 시에만)
1. Phase 1 재시도 사다리까지 전부 실패 (R4 근사 제시 직전)
2. LLM 엔진 사용 가능 (`engine_mode`가 휴리스틱 아님)
3. 직전 턴이 플래너 아니었음 (무한루프 방지, 최대 1회/발화)

### 5.2 프로토콜 (도구 호출 아닌 단일 JSON 왕복 ×2)
로컬 소형 LLM 호환을 위해 tool-use API 대신 구조화 JSON 유지:

```
1차 요청: 발화 + 사용 가능한 전략 목록 + 1차 시도 요약
  {"tried": [{"strategy":"semantic+date", "results":0}, ...],
   "available": ["semantic","place_meta","album_name","person","date_only","ocr"],
   "db_hints": {"total":5231, "date_range":"2015-01~2026-07",
                "albums":["제주2024","가족여행",...], "people":["아빠","딸",...]}}
응답: {"plan": {"strategy":"album_name", "search_text":"제주",
       "date_from":null,...}, "reason":"...", "give_up": false}
→ 실행 → 결과 요약을 붙여 2차(최종) 요청 → 만족/재계획/포기
```
- `db_hints`의 앨범·인물 목록이 핵심 — LLM이 "DB에 뭐가 있는지" 알아야
  "어떻게 찾을지 생각"이 가능. (앨범 20개·인물 20명까지만 — 토큰 절약)
- 최대 2왕복 후 무조건 종료. 각 왕복 타임아웃 10s.

### 5.3 프롬프트 위치
`llm.py`에 `PLANNER_PROMPT` 추가, `openrouter.py`/`local_llm.py`에
`plan()` 함수 추가 (기존 `parse()`와 동일한 폴백 체인 재사용).

---

## 6. API / 파일 변경 요약

| 파일 | 변경 | Phase |
|---|---|---|
| `backend/search_retry.py` | **신규** — 완화 사다리 | 1 |
| `backend/main.py` | `_run_search` 래핑, 정제 감지, `skill_id` 추적, `/api/feedback/view` | 1·2·3 |
| `backend/llm.py` | `detect_feedback`에 positive 추가, (P4) PLANNER_PROMPT | 3·4 |
| `backend/skills.py` | 스키마 v2, `reinforce/penalize`, 동적 임계값 | 3 |
| `backend/places.py` | 변경 없음 (호출부만 추가) | 3 |
| `frontend/lightbox.js` | 검색 뷰 열람 시 view 피드백 1줄 | 3 |
| `frontend/voice.js` | 응답의 `relaxed` 배지 표시 (선택) | 1 |
| `data/skills.json` | 무변경 호환 (lazy 마이그레이션) | 3 |

**API 하위호환**: `/api/chat` 응답에 `relaxed` 필드 추가만 — 기존 필드 불변.

---

## 7. 테스트 계획 (`tests/` 신규)

| 대상 | 케이스 |
|---|---|
| 재시도 사다리 | 0장→날짜 완화 성공 / GPS 0장→메타 강등 / 전부 실패→근사 제시 / 완화 문구 |
| 정제 | "그 중에 밤" → only_ids 적용 / 연쇄 2회 / 결과 없는 상태에서 "그 중에" → 일반 검색 |
| 피드백 | positive 감지 (단독 vs 새 검색 혼합) / reinforce 후 임계 하강 / fail 3회 도태 |
| 스킬 마이그레이션 | v1 skills.json 로드 → 기본값 주입 → 정상 동작 |
| 시간 추론 (회귀) | 기존 `_parse_date_phrase` 전 패턴 스냅샷 (변경 없음 확인) |

백엔드는 pytest 순수 유닛으로 LLM 없이 검증 가능하게 설계
(재시도/정제/스킬은 전부 결정적 로직).

---

## 8. 리스크 & 트레이드오프

| 리스크 | 대응 |
|---|---|
| 완화가 과해 "아무거나" 보여줌 | 사다리 1단계씩 + 답변에 완화 사실 명시 → 사용자가 즉시 인지 |
| 긍정 오탐 ("좋아 그럼 다음…") | 단독 발화 조건 + 길이 제한. 오탐해도 +1 가중일 뿐 파괴적이지 않음 |
| 열람 신호 노이즈 (호기심 클릭) | +0.3 저가중 + 검색당 1회 상한 |
| 스킬 도태 오판 | 삭제 아닌 비활성 + UI 수동 관리 유지 |
| Phase 4 지연·비용 | 조건부 발동 + 2왕복 상한 + 실패해도 R4 근사 제시로 폴백 |
| `_last_search` 전역(단일 사용자 전제) | 현 제품 전제 유지. 다중 사용자화 시 세션 키만 추가하면 됨 (구조 변경 불필요) |

---

## 9. 구현 순서 제안

```
PR1  Phase 1  search_retry.py + _run_search 래핑 + 테스트        (~반나절)
PR2  Phase 2  정제 감지 + only_ids 연쇄 + 테스트                  (~2시간)
PR3  Phase 3  스킬 v2 + 피드백 확장 + view API + 프론트 1줄       (~반나절)
PR4  Phase 4  플래너 (PR1~3 실사용 후 잔여 실패 사례 보고 결정)
```

Phase 4는 PR1~3 배포 후 **실제로 못 찾는 발화 로그를 모아** 필요성을
재평가하는 것을 권장 — Phase 1~3만으로 해결되는 비율이 높을 것으로 예상.
