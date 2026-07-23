"""PhotoNest AI — 로컬 구글포토: 대화형 검색 + 타임라인/앨범/지도/휴지통 서버."""
import os
import threading
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import db, indexer, llm, search, upload

BASE_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIR = BASE_DIR / "frontend"

app = FastAPI(title="PhotoNest AI")
app.include_router(upload.router)


@app.get("/upload")
def upload_page():
    return FileResponse(FRONTEND_DIR / "upload.html")


class ChatTurn(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    message: str
    history: Optional[List[ChatTurn]] = None


class AlbumCreate(BaseModel):
    name: str


class AlbumRename(BaseModel):
    name: str


class MediaIds(BaseModel):
    media_ids: List[str]


class FavoriteSet(BaseModel):
    value: bool


class LocationSet(BaseModel):
    lat: Optional[float] = None
    lon: Optional[float] = None


class PersonRename(BaseModel):
    name: str


class CommentSet(BaseModel):
    comment: str


class SettingsUpdate(BaseModel):
    engine_mode: Optional[str] = None
    openrouter_api_key: Optional[str] = None
    openrouter_model: Optional[str] = None


class OpenRouterTest(BaseModel):
    openrouter_api_key: Optional[str] = None
    openrouter_model: Optional[str] = None


class TakeoutAlbumApply(BaseModel):
    names: Optional[List[str]] = None  # None이면 감지된 전체


@app.on_event("startup")
def startup():
    db.init()
    threading.Thread(target=indexer.build_index, daemon=True).start()
    # 로컬 LLM이 떠 있으면 백그라운드로 미리 깨워(콜드 로딩) 첫 응답 지연 방지
    threading.Thread(target=_warmup_llm, daemon=True).start()


def _warmup_llm():
    try:
        from . import local_llm
        if local_llm.available():
            local_llm.parse("안녕", today="2026-01-01")
    except Exception:
        pass


# ---------- 상태 / 색인 ----------

@app.get("/api/status")
def status():
    s = indexer.get_state()
    from . import local_llm, openrouter, settings, takeout
    base, model, name = local_llm.discover()
    mode = settings.load()["engine_mode"]

    # 설정된 모드 + 가용성으로 실제 사용될 엔진을 표기
    def _resolve_engine():
        if mode == "openrouter":
            return "openrouter" if openrouter.available() else "heuristic"
        if mode == "local":
            return "local-llm" if base else "heuristic"
        if mode == "claude":
            return "claude" if os.environ.get("ANTHROPIC_API_KEY") else "heuristic"
        # auto
        if openrouter.available():
            return "openrouter"
        if base:
            return "local-llm"
        if os.environ.get("ANTHROPIC_API_KEY"):
            return "claude"
        return "heuristic"

    engine = _resolve_engine()
    from . import embedder
    llm_info = {
        "engine": engine,
        "engine_mode": mode,
        "llm_name": openrouter.config()[1] if engine == "openrouter" else name,
        "llm_model": openrouter.config()[1] if engine == "openrouter" else model,
        "embed_backend": embedder.name(),
    }
    ts = takeout.get_state()
    return dict(
        s, count=db.timeline_months()["total"],
        takeout_running=ts["running"], takeout_phase=ts["phase"],
        takeout_added=ts["added"], **llm_info,
    )


# ---------- 설정 (LLM 엔진 / OpenRouter) ----------

@app.get("/api/settings")
def get_settings():
    from . import openrouter, settings
    data = settings.public()
    data["openrouter_presets"] = openrouter.PRESET_MODELS
    data["default_model"] = settings.DEFAULT_OPENROUTER_MODEL
    return data


@app.post("/api/settings")
def update_settings(patch: SettingsUpdate):
    from . import settings
    try:
        settings.save(patch.model_dump(exclude_none=True))
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return settings.public()


@app.post("/api/settings/test")
def test_settings(body: OpenRouterTest):
    from . import openrouter, settings
    key = body.openrouter_api_key
    # 마스킹 값이거나 비어있으면 저장된 키로 검증
    if not key or "…" in key or "*" in key:
        key = settings.load().get("openrouter_api_key", "")
    ok, message = openrouter.test_key(key, body.openrouter_model)
    return {"ok": ok, "message": message}


@app.get("/api/takeout/pending")
def takeout_pending():
    from . import takeout
    zips = takeout.find_zips()
    state = takeout._load_state()
    items = []
    for z in zips:
        rec = state.get(z.name, {})
        items.append({
            "name": z.name,
            "size_mb": round(z.stat().st_size / 1024 / 1024, 1),
            "processed": rec.get("sig") == takeout._sig(z),
            "added": rec.get("added"),
        })
    return {"zips": items}


@app.get("/api/takeout/albums")
def takeout_albums():
    """구글포토 앨범 폴더 감지 결과 — 앨범으로 가져올지 사용자에게 묻는 용도."""
    from . import takeout
    detected, _ = takeout.detect_albums()
    return {"albums": detected}


@app.post("/api/takeout/albums/apply")
def takeout_albums_apply(body: TakeoutAlbumApply):
    from . import takeout
    return takeout.apply_albums(body.names)


@app.post("/api/reindex")
def reindex():
    threading.Thread(
        target=indexer.build_index, kwargs={"force": True}, daemon=True
    ).start()
    return {"ok": True}


# ---------- 전용 모바일 앱 배포 ----------

APK_PATH = BASE_DIR / "data" / "app" / "photonest-uploader.apk"          # 일반 배포용(preview)
APK_DEV_PATH = BASE_DIR / "data" / "app" / "photonest-uploader-dev.apk"  # 개발용(dev client)


@app.get("/api/app-info")
def app_info():
    """전용 업로드 앱 다운로드 정보. APK가 준비돼 있으면 QR로 안내."""
    from . import upload
    si = upload.server_info()
    base = f"http://{si['ip']}:{si['port']}"
    return {
        **si,
        "apk_available": APK_PATH.is_file(),
        "apk_url": f"{base}/download/app" if APK_PATH.is_file() else None,
        "apk_dev_available": APK_DEV_PATH.is_file(),
        "apk_dev_url": f"{base}/download/app-dev" if APK_DEV_PATH.is_file() else None,
    }


@app.get("/download/app")
def download_app():
    if not APK_PATH.is_file():
        return JSONResponse(
            {"error": "전용 앱이 아직 빌드되지 않았습니다. mobile/README.md 참고."},
            status_code=404,
        )
    return FileResponse(
        APK_PATH, media_type="application/vnd.android.package-archive",
        filename="photonest-uploader.apk",
    )


@app.get("/download/app-dev")
def download_app_dev():
    """개발용(dev client) 앱 — 개발자 본인 설치용. Metro 연결이 있어야 실행됨."""
    if not APK_DEV_PATH.is_file():
        return JSONResponse({"error": "개발용 앱이 없습니다."}, status_code=404)
    return FileResponse(
        APK_DEV_PATH, media_type="application/vnd.android.package-archive",
        filename="photonest-uploader-dev.apk",
    )


# ---------- 조회 ----------

@app.get("/api/timeline")
def timeline():
    return db.timeline_months()


@app.get("/api/photos")
def photos(month: Optional[str] = None, album: Optional[int] = None,
           favorites: bool = False, limit: int = 2000, offset: int = 0):
    return {
        "items": db.list_photos(
            month=month, album_id=album, favorites=favorites,
            limit=limit, offset=offset,
        )
    }


@app.get("/api/geo")
def geo():
    return {"items": db.geo_items()}


@app.get("/api/media/{media_id}/details")
def media_details(media_id: str):
    """카메라 장비·촬영 설정 등 상세 EXIF (상세정보 패널용, 즉석 추출)."""
    m = db.get_media(media_id)
    if not m:
        return JSONResponse({"error": "not found"}, status_code=404)
    if m.get("trashed_at") and m.get("trash_path"):
        path = (db.DATA_DIR / m["trash_path"]).resolve()
    else:
        path = (indexer.PHOTOS_DIR / m["path"]).resolve()
    if not path.is_file():
        return {"details": {}}
    return {"details": indexer.extract_details(path)}


@app.get("/api/duplicates")
def duplicates():
    return {"groups": db.duplicate_groups()}


@app.post("/api/duplicates/clean")
def clean_duplicates():
    """모든 중복 그룹에서 첫 장만 남기고 나머지를 휴지통으로 이동."""
    trashed = 0
    groups = 0
    for g in db.duplicate_groups():
        groups += 1
        for it in g[1:]:
            if indexer.move_to_trash(it["id"]):
                trashed += 1
    return {"ok": True, "groups": groups, "trashed": trashed}


# ---------- 대화 / 검색 ----------

# 직전 검색 기억 (단일 로컬 사용자 — 피드백 교정에 사용)
_last_search = {}


# 완화 라벨 → 답변 문구 조각 (search_retry가 적용한 완화를 사용자에게 설명)
RELAX_PHRASES = {
    "date_widened": "기간을 조금 넓혀서",
    "date_removed": "날짜 조건 없이",
    "place_to_meta": "위치정보 대신 앨범·폴더 이름 기준으로",
    "place_removed": "장소 조건을 빼고",
    "english_retry": "표현을 바꿔서",
}


def _combine_ids(base_ids, person_ids):
    """정제 대상(직전 결과)과 인물 필터의 교집합 — 순서는 base_ids 기준 유지."""
    if base_ids is None:
        return person_ids
    if person_ids is None:
        return base_ids
    pset = set(person_ids)
    return [i for i in base_ids if i in pset]


def _run_search(message, *, search_text, bbox, place, date_from, date_to,
                media_type, person, engine, skill_used=None, exclude_ids=None,
                hour_from=None, hour_to=None, place_text=None, base_ids=None):
    from . import search_retry

    person_ids = db.person_media_ids(person["id"]) if person else None
    only_ids = _combine_ids(base_ids, person_ids)
    # 내용 검색(CLIP)은 관련도순 상위만, 위치/인물/날짜 필터만일 땐 그 그룹 전체
    top_k = 60 if search_text else 1000
    plan = dict(
        search_text=search_text, date_from=date_from, date_to=date_to,
        media_type=media_type, raw_query=message, only_ids=only_ids,
        bbox=bbox, exclude_ids=exclude_ids, hour_from=hour_from,
        hour_to=hour_to, place_text=place_text, top_k=top_k,
    )
    # 저품질 판정 기준·영어 재시도 함수 주입 (AI 스택이 있을 때만)
    quality_bar = english_fn = None
    if search_text and indexer.ai_available():
        try:
            from . import embedder
            P = embedder.params()
            quality_bar = P["score_threshold"] + 0.5 * P["score_margin"]
            if not embedder.needs_english():  # CLIP-ONNX는 find가 이미 영어 변환
                english_fn = llm._ko_to_en
        except Exception:
            pass
    results, relaxed = search_retry.run_with_retry(
        plan, search.find,
        place_name=place["name"] if place else None,
        quality_bar=quality_bar, english_fn=english_fn,
    )

    n = len(results)
    refined = base_ids is not None
    loc = f"{place['name']} 지역(위치 기준)에서 " if place \
        else (f"'{place_text}'에서 " if place_text else "")
    if n == 0:
        reply = "직전 결과 안에는 그 조건에 맞는 사진이 없어요." if refined \
            else "조건에 맞는 사진을 찾지 못했어요. 다른 말로 다시 말씀해 주시겠어요?"
    elif relaxed == ["nearest_time"]:
        reply = f"요청하신 시기의 사진이 없어서, 가장 가까운 시기의 사진 {n}장을 보여드릴게요."
    elif relaxed:
        how = ", ".join(RELAX_PHRASES.get(l, l) for l in relaxed)
        reply = f"조건 그대로는 없어서 {how} 다시 찾았어요. 모두 {n}장이에요."
    elif refined:
        reply = f"그 중에서 {n}장으로 좁혔어요."
    elif person and not search_text and not place:
        reply = f"'{person['name']}'님이 나온 사진 {n}장을 찾았어요."
    else:
        reply = f"{loc}모두 {n}장을 찾았어요."

    # _last_search에는 사용자가 요청한 원 조건을 저장 (피드백 교정은 원 의도 기준)
    _last_search.update(
        query=message, place=place, bbox=bbox, search_text=search_text,
        date_from=date_from, date_to=date_to, media_type=media_type,
        person=person, hour_from=hour_from, hour_to=hour_to,
        place_text=place_text, result_ids=[r["id"] for r in results],
    )
    return {"reply": reply, "intent": "search", "engine": engine,
            "skill": skill_used, "place": place["name"] if place else None,
            "relaxed": relaxed, "results": results}


@app.post("/api/chat")
def chat(req: ChatRequest):
    from . import skills, places

    history = [{"role": t.role, "content": t.content} for t in (req.history or [])]
    message = req.message

    # -1) "그 중에서 ~" — 직전 검색 결과 안에서 좁히기 (연쇄 정제 가능).
    #     피드백 감지보다 먼저: "~것만 보여줘"가 피드백 'only'로 새는 것 방지.
    base_ids = None
    remainder = llm.detect_refine(message)
    if remainder is not None and _last_search.get("result_ids"):
        if not remainder:
            return {"reply": "직전 결과에서 무엇으로 좁힐까요? 예: \"밤에 찍은 것만\", \"강아지 나온 것만\"",
                    "intent": "chat", "engine": "instant", "results": []}
        base_ids = _last_search["result_ids"]
        message = remainder  # 이후 해석은 정제 조건만으로

    # 0) 교정 피드백이면 직전 검색을 위치 기준으로 다시
    if base_ids is None:
        fb = llm.detect_feedback(message)
        if fb and _last_search.get("query"):
            return _handle_feedback(message, fb)

    meta = llm.quick_meta(message)
    if meta["greeting"]:
        return {"reply": "안녕하세요! 찾고 싶은 사진을 말씀해 주세요.",
                "intent": "chat", "engine": "instant", "results": []}

    date_from, date_to, media_type = meta["date_from"], meta["date_to"], meta["media_type"]
    hour_from, hour_to = meta["hour_from"], meta["hour_to"]
    person = db.match_person_name(message)

    # 지명 감지 → 위치(GPS) 검색. 지명 뺀 나머지(residual)로 내용 의도 분석
    place = places.detect(message)
    bbox = place["bbox"] if place else None
    core = place["residual"] if place else message
    core_has_content = bool(skills._strip_terms(core)) if place else True

    search_text = None
    place_text = None
    engine = "location" if place else None
    skill_used = None

    if not place or core_has_content:
        target = core if place else message
        skill, sim = skills.match(target)
        if skill:
            search_text = skill.get("search_text")
            place_text = skill.get("place_text")   # 지명은 재사용 시에도 메타 필터로
            media_type = media_type or skill.get("media_type")
            if skill.get("place") and not place:   # 스킬이 학습한 위치 선호
                place = skill["place"]; bbox = place["bbox"]
            engine = "skill"
            skill_used = skill["label"]
            skills.record_use(skill["id"])
        else:
            parsed = llm.parse(target, history=history)
            # 정제 모드에서는 잡담 판정이어도 중단하지 않는다 —
            # "동영상만" 같은 순수 필터 발화는 quick_meta 필터만으로 좁힌다
            if parsed.get("intent") == "chat" and not place and base_ids is None:
                return {"reply": parsed.get("reply") or "무엇을 도와드릴까요?",
                        "intent": "chat", "engine": parsed.get("engine"), "results": []}
            search_text = parsed.get("search_text")
            place_text = parsed.get("place_text")
            media_type = media_type or parsed.get("media_type")
            date_from = parsed.get("date_from") or date_from
            date_to = parsed.get("date_to") or date_to
            if not person and parsed.get("person"):
                person = db.match_person_name(parsed["person"])
            engine = parsed.get("engine")
            # LLM류 엔진의 해석은 스킬로 학습 — place_text도 함께 캐싱해
            # 재사용 시 지명이 의미검색으로 새지 않게 한다.
            # 정제 조각("~것만" 등)은 재사용 가치가 없어 저장하지 않는다.
            if base_ids is None and engine in ("local-llm", "claude", "openrouter") \
                    and (search_text or place_text):
                sk = skills.add(target, search_text, parsed.get("media_type"),
                                place_text=place_text)
                if sk:
                    skill_used = sk["label"]

    # LLM이 분리한 지명이 등록 지역(places 사전)이면 정밀 GPS 검색으로 승격
    if place_text and not place:
        known = places.detect(place_text)
        if known:
            place, bbox, place_text = known, known["bbox"], None

    return _run_search(
        message, search_text=search_text, bbox=bbox, place=place,
        date_from=date_from, date_to=date_to, media_type=media_type,
        person=person, engine=engine, skill_used=skill_used,
        hour_from=hour_from, hour_to=hour_to, place_text=place_text,
        base_ids=base_ids,
    )


def _handle_feedback(message, fb):
    """직전 검색을 피드백대로 교정 — 주로 지명을 위치(GPS)로 정확히."""
    from . import skills, places
    ls = _last_search
    place = places.detect(message) or ls.get("place")

    # 위치로 좁힐 지명이 없으면 안내
    if not place:
        return {"reply": "어느 지역 사진인지 알려주시면 위치 정보로 정확히 찾아드릴게요. 예: \"제주도 사진\"",
                "intent": "chat", "engine": "instant", "results": []}

    result = _run_search(
        ls["query"], search_text=ls.get("search_text"), bbox=place["bbox"],
        place=place, date_from=ls.get("date_from"), date_to=ls.get("date_to"),
        media_type=ls.get("media_type"), person=ls.get("person"),
        hour_from=ls.get("hour_from"), hour_to=ls.get("hour_to"), engine="location",
    )
    # 피드백을 스킬로 학습 → 다음에 같은 류 질의는 위치로 처리
    skills.add(ls["query"], ls.get("search_text"), ls.get("media_type"), place=place)
    n = len(result["results"])
    result["reply"] = (f"네, {place['name']} 위치 정보를 기준으로 다시 찾았어요. "
                       f"이제 {n}장이에요. 앞으로 비슷한 검색도 위치로 정확히 찾을게요.")
    result["engine"] = "feedback"
    return result


# ---------- 검색 스킬 / 도움말 ----------

@app.get("/api/skills")
def list_skills():
    from . import skills
    return {"skills": skills.list_skills()}


@app.delete("/api/skills/{skill_id}")
def delete_skill(skill_id: str):
    from . import skills
    return {"ok": skills.delete(skill_id)}


@app.get("/api/help")
def help_info():
    from . import skills
    return {
        "examples": [
            "제주도 사진 보여줘",        # 지역명 → 위치(GPS)로 정확히
            "부산에서 찍은 바다 사진",
            "작년 여름 사진",
            "3년 전 가을 단풍 사진",
            "밤에 찍은 도시 야경",
            "강아지 사진 찾아줘",
            "엄마가 나온 사진",          # 인물 이름 → 인물 검색
            "불꽃놀이 영상",
        ],
        "tips": [
            "지역명(제주도·부산·서울 등)을 말하면 사진의 위치 정보로 정확히 찾아요.",
            "결과에 다른 지역이 섞이면 ‘제주도 것만 보여줘’처럼 말하면 위치로 바로잡고 학습해요.",
            "‘작년/3년 전/지난달’ 같은 날짜를 함께 말하면 그 기간으로 좁혀줘요.",
            "‘영상/동영상’이라고 하면 동영상만, 인물 이름을 말하면 그 사람 사진을 찾아요.",
            "한 번 찾은 검색은 스킬로 저장돼, 비슷한 질문은 즉시 처리돼요.",
        ],
        "skills": skills.list_skills(),
    }


# ---------- 즐겨찾기 ----------

@app.post("/api/media/{media_id}/favorite")
def favorite(media_id: str, req: FavoriteSet):
    db.set_favorite(media_id, req.value)
    return {"ok": True, "favorite": req.value}


@app.post("/api/media/{media_id}/location")
def set_location(media_id: str, req: LocationSet):
    """지도에서 수동 지정한 촬영 위치 저장 (GPS 없는 사진 보완)."""
    if not db.get_media(media_id):
        return JSONResponse({"error": "not found"}, status_code=404)
    lat = req.lat if req.lat is not None and req.lon is not None else None
    lon = req.lon if lat is not None else None
    db.set_location(media_id, lat, lon)
    return {"ok": True, "lat": lat, "lon": lon}


# ---------- 코멘트 ----------

@app.post("/api/media/{media_id}/comment")
def set_comment(media_id: str, req: CommentSet):
    if not db.get_media(media_id):
        return JSONResponse({"error": "not found"}, status_code=404)
    text = req.comment.strip()
    emb = search.embed_comment(text) if text else None
    db.set_comment(media_id, text, emb)
    return {"ok": True, "comment": text}


# ---------- 휴지통 ----------

@app.get("/api/trash")
def trash_list():
    return {"items": db.list_trash()}


@app.post("/api/media/{media_id}/trash")
def trash_media(media_id: str):
    return {"ok": indexer.move_to_trash(media_id)}


@app.post("/api/media/{media_id}/restore")
def restore_media(media_id: str):
    return {"ok": indexer.restore_from_trash(media_id)}


@app.delete("/api/media/{media_id}")
def delete_media(media_id: str):
    return {"ok": indexer.delete_permanently(media_id)}


@app.post("/api/trash/empty")
def empty_trash():
    n = 0
    for it in db.list_trash():
        if indexer.delete_permanently(it["id"]):
            n += 1
    return {"ok": True, "deleted": n}


# ---------- 앨범 ----------

@app.get("/api/albums")
def albums():
    return {"albums": db.list_albums()}


@app.post("/api/albums")
def create_album(req: AlbumCreate):
    aid = db.create_album(req.name.strip() or "이름 없는 앨범")
    return {"ok": True, "id": aid}


@app.put("/api/albums/{album_id}")
def rename_album(album_id: int, req: AlbumRename):
    db.rename_album(album_id, req.name.strip())
    return {"ok": True}


@app.delete("/api/albums/{album_id}")
def delete_album(album_id: int):
    db.delete_album(album_id)
    return {"ok": True}


@app.get("/api/albums/{album_id}")
def album_detail(album_id: int):
    a = db.get_album(album_id)
    if not a:
        return JSONResponse({"error": "not found"}, status_code=404)
    return {"album": a, "items": db.list_photos(album_id=album_id)}


@app.post("/api/albums/{album_id}/items")
def add_album_items(album_id: int, req: MediaIds):
    db.add_to_album(album_id, req.media_ids)
    return {"ok": True}


@app.post("/api/albums/{album_id}/items/remove")
def remove_album_items(album_id: int, req: MediaIds):
    db.remove_from_album(album_id, req.media_ids)
    return {"ok": True}


# ---------- 인물 ----------

@app.get("/api/persons")
def persons():
    return {"persons": db.list_persons()}


@app.get("/api/persons/{person_id}")
def person_detail(person_id: int):
    p = db.get_person(person_id)
    if not p:
        return JSONResponse({"error": "not found"}, status_code=404)
    ids = db.person_media_ids(person_id)
    return {"person": p, "items": db.list_photos(ids=ids)}


@app.put("/api/persons/{person_id}")
def rename_person(person_id: int, req: PersonRename):
    final_id = db.rename_person(person_id, req.name.strip())
    merged = final_id != person_id
    return {"ok": True, "id": final_id, "merged": merged}


@app.get("/faces/{face_id}.jpg")
def face_thumb(face_id: int):
    from .faces import FACES_DIR
    p = FACES_DIR / f"{face_id}.jpg"
    if not p.is_file():
        return JSONResponse({"error": "not found"}, status_code=404)
    return FileResponse(p)


# ---------- 미디어 파일 ----------

_HEIC_CACHE = BASE_DIR / "data" / "preview"


@app.get("/media/{rel_path:path}")
def media(rel_path: str):
    p = (indexer.PHOTOS_DIR / rel_path).resolve()
    if not str(p).startswith(str(indexer.PHOTOS_DIR.resolve())) or not p.is_file():
        return JSONResponse({"error": "not found"}, status_code=404)
    # HEIC/HEIF는 브라우저가 표시 못 하므로 JPEG로 변환해 제공(디스크 캐시)
    if p.suffix.lower() in (".heic", ".heif"):
        try:
            import hashlib
            st = p.stat()
            digest = hashlib.md5(f"{p}-{int(st.st_mtime)}-{st.st_size}".encode()).hexdigest()
            cached = _HEIC_CACHE / f"{digest}.jpg"
            if not cached.is_file():
                from PIL import Image
                _HEIC_CACHE.mkdir(parents=True, exist_ok=True)
                img = Image.open(p)
                img = indexer.ImageOps.exif_transpose(img).convert("RGB")
                img.save(cached, "JPEG", quality=88)
            return FileResponse(cached, media_type="image/jpeg")
        except Exception:
            return FileResponse(p)  # 변환 실패 시 원본
    return FileResponse(p)


@app.get("/thumbs/{item_id}.jpg")
def thumb(item_id: str):
    p = indexer.THUMBS_DIR / f"{item_id}.jpg"
    if not p.is_file():
        return JSONResponse({"error": "not found"}, status_code=404)
    return FileResponse(p)


app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
