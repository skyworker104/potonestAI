"""앨범 / 타임라인 / 검색 / 인물 / 지도 엔드포인트.

TV 앱(Immich-Android-TV 계열)은 동기화 스트림을 쓰지 않고 이 REST만 쓴다.
폰 앱은 타임라인을 기기 안 DB로 그리므로 /timeline/* 은 주로 TV 앱이 부른다.

PhotoNest에 대응 개념이 없는 것들(파트너 공유, 메모리, 스택, 태그, 공유링크)은
빈 목록을 돌려준다 — 앱이 그 화면을 "비어 있음"으로 정상 표시한다.
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel

from .. import db, search
from . import auth, dto, media, query, state

router = APIRouter()

MAX_PAGE = 1000


def _assets(rows):
    """행 목록 → AssetResponseDto 목록 (체크섬은 한 번에 조회)."""
    ids = [r["id"] for r in rows]
    state.register("asset", ids)
    checksums = state.checksums_for(ids)
    return [dto.asset_response(r, checksums.get(r["id"]) or "") for r in rows]


def _album_id(album_uuid):
    ref = state.resolve(album_uuid, "album")
    if ref is None:
        raise HTTPException(status_code=404, detail="Album not found")
    return int(ref)


def _person_id(person_uuid):
    ref = state.resolve(person_uuid, "person")
    if ref is None:
        raise HTTPException(status_code=404, detail="Person not found")
    return int(ref)


# ---------- 앨범 ----------

@router.get("/albums")
def albums_list(assetId: Optional[str] = None, shared: Optional[bool] = None,
                session: str = Depends(auth.require_session)):
    rows = query.albums()
    state.register("album", [a["id"] for a in rows])
    state.register("asset", [a["thumb_media_id"] for a in rows])
    if shared:
        return []  # PhotoNest에는 공유 앨범 개념이 없다
    if assetId:
        media_id = state.resolve(assetId, "asset")
        if media_id is None:
            return []
        rows = [a for a in rows
                if media_id in query.album_asset_ids(a["id"])]
    return [dto.album_response(a, a["count"], a["thumb_media_id"]) for a in rows]


@router.get("/albums/statistics")
def albums_statistics(session: str = Depends(auth.require_session)):
    n = len(query.albums())
    return {"owned": n, "shared": 0, "notShared": n}


@router.get("/albums/{album_uuid}")
def album_get(album_uuid: str, session: str = Depends(auth.require_session)):
    album_id = _album_id(album_uuid)
    rows = [a for a in query.albums() if a["id"] == album_id]
    if not rows:
        raise HTTPException(status_code=404, detail="Album not found")
    album = rows[0]
    media_ids = query.album_asset_ids(album_id)
    assets = query.assets_by_ids(media_ids)
    order = {mid: i for i, mid in enumerate(media_ids)}
    assets.sort(key=lambda r: order.get(r["id"], 0))
    body = dto.album_response(album, len(media_ids), album["thumb_media_id"])
    body["assets"] = _assets(assets)
    return body


class CreateAlbumBody(BaseModel):
    albumName: str
    description: Optional[str] = None
    assetIds: Optional[List[str]] = None


@router.post("/albums", status_code=201)
def album_create(body: CreateAlbumBody,
                 session: str = Depends(auth.require_session)):
    album_id = db.create_album(body.albumName)
    media_ids = list(state.resolve_many(body.assetIds or [], "asset").values())
    if media_ids:
        db.add_to_album(album_id, media_ids)
    state.register("album", [album_id])
    rows = [a for a in query.albums() if a["id"] == album_id]
    album = rows[0]
    return dto.album_response(album, album["count"], album["thumb_media_id"])


class UpdateAlbumBody(BaseModel):
    albumName: Optional[str] = None
    description: Optional[str] = None


@router.patch("/albums/{album_uuid}")
def album_update(album_uuid: str, body: UpdateAlbumBody,
                 session: str = Depends(auth.require_session)):
    album_id = _album_id(album_uuid)
    if body.albumName:
        db.rename_album(album_id, body.albumName)
    album = [a for a in query.albums() if a["id"] == album_id][0]
    return dto.album_response(album, album["count"], album["thumb_media_id"])


@router.delete("/albums/{album_uuid}")
def album_delete(album_uuid: str, session: str = Depends(auth.require_session)):
    db.delete_album(_album_id(album_uuid))
    return Response(status_code=204)


class AlbumAssetsBody(BaseModel):
    ids: List[str]


@router.put("/albums/{album_uuid}/assets")
def album_add_assets(album_uuid: str, body: AlbumAssetsBody,
                     session: str = Depends(auth.require_session)):
    album_id = _album_id(album_uuid)
    resolved = state.resolve_many(body.ids, "asset")
    existing = set(query.album_asset_ids(album_id))
    results = []
    to_add = []
    for asset_id in body.ids:
        media_id = resolved.get(asset_id)
        if media_id is None:
            results.append({"id": asset_id, "success": False,
                            "error": "not_found"})
        elif media_id in existing:
            results.append({"id": asset_id, "success": False,
                            "error": "duplicate"})
        else:
            to_add.append(media_id)
            results.append({"id": asset_id, "success": True})
    if to_add:
        db.add_to_album(album_id, to_add)
    return results


@router.delete("/albums/{album_uuid}/assets")
def album_remove_assets(album_uuid: str, body: AlbumAssetsBody,
                        session: str = Depends(auth.require_session)):
    album_id = _album_id(album_uuid)
    resolved = state.resolve_many(body.ids, "asset")
    media_ids = [resolved[a] for a in body.ids if a in resolved]
    if media_ids:
        db.remove_from_album(album_id, media_ids)
    return [
        {"id": asset_id, "success": asset_id in resolved,
         **({} if asset_id in resolved else {"error": "not_found"})}
        for asset_id in body.ids
    ]


# ---------- 타임라인 ----------

def _timeline_filters(albumId, personId, isFavorite, isTrashed):
    return {
        "album_id": _album_id(albumId) if albumId else None,
        "person_id": _person_id(personId) if personId else None,
        "favorites": bool(isFavorite),
        "trashed": bool(isTrashed),
    }


@router.get("/timeline/buckets")
def timeline_buckets(albumId: Optional[str] = None, personId: Optional[str] = None,
                     isFavorite: Optional[bool] = None,
                     isTrashed: Optional[bool] = None,
                     session: str = Depends(auth.require_session)):
    """월 단위 묶음 목록. timeBucket은 그 달 1일(YYYY-MM-DD)."""
    buckets = query.buckets(**_timeline_filters(albumId, personId,
                                                isFavorite, isTrashed))
    return [{"timeBucket": "%s-01" % ym, "count": n} for ym, n in buckets]


@router.get("/timeline/bucket")
def timeline_bucket(timeBucket: str, albumId: Optional[str] = None,
                    personId: Optional[str] = None,
                    isFavorite: Optional[bool] = None,
                    isTrashed: Optional[bool] = None,
                    session: str = Depends(auth.require_session)):
    """한 달치 에셋 (열 단위 배열). timeBucket은 날짜든 ISO든 앞 7자만 본다."""
    month = str(timeBucket)[:7]
    rows = query.bucket_assets(
        month, **_timeline_filters(albumId, personId, isFavorite, isTrashed)
    )
    ids = [r["id"] for r in rows]
    state.register("asset", ids)
    return dto.time_bucket(rows, None)


# ---------- 검색 ----------

class MetadataSearchBody(BaseModel):
    size: Optional[int] = 250
    page: Optional[int] = 1
    order: Optional[str] = "desc"
    takenAfter: Optional[str] = None
    takenBefore: Optional[str] = None
    isFavorite: Optional[bool] = None
    type: Optional[str] = None
    albumIds: Optional[List[str]] = None
    personIds: Optional[List[str]] = None
    city: Optional[str] = None
    originalFileName: Optional[str] = None
    checksum: Optional[str] = None
    withDeleted: Optional[bool] = False


def _search_response(rows, has_next, page):
    return {
        "assets": {
            "items": _assets(rows),
            "count": len(rows),
            "total": len(rows),
            "facets": [],
            "nextPage": str(page + 1) if has_next else None,
            "nextCursor": None,
        },
        "albums": {"items": [], "count": 0, "total": 0, "facets": []},
    }


def _first_id(uuids, kind):
    if not uuids:
        return None
    resolved = state.resolve_many(uuids, kind)
    for u in uuids:
        if u in resolved:
            return int(resolved[u]) if kind in ("album", "person") else resolved[u]
    raise HTTPException(status_code=404, detail="%s not found" % kind)


@router.post("/search/metadata")
def search_metadata(body: MetadataSearchBody,
                    session: str = Depends(auth.require_session)):
    if body.checksum:
        media_id = state.media_id_by_sha1(body.checksum)
        rows = query.assets_by_ids([media_id]) if media_id else []
        return _search_response(rows, False, 1)
    rows, has_next = query.search_assets(
        size=body.size, page=body.page, order=body.order,
        taken_after=body.takenAfter, taken_before=body.takenBefore,
        is_favorite=body.isFavorite, asset_type=body.type,
        album_id=_first_id(body.albumIds, "album"),
        person_id=_first_id(body.personIds, "person"),
        city=body.city, filename=body.originalFileName,
        with_deleted=bool(body.withDeleted),
    )
    return _search_response(rows, has_next, body.page or 1)


class RandomSearchBody(MetadataSearchBody):
    pass


@router.post("/search/random")
def search_random(body: RandomSearchBody,
                  session: str = Depends(auth.require_session)):
    """TV 앱 슬라이드쇼가 쓰는 무작위 추출. 배열을 그대로 돌려준다."""
    rows, _has_next = query.search_assets(
        size=body.size, order=body.order,
        taken_after=body.takenAfter, taken_before=body.takenBefore,
        is_favorite=body.isFavorite, asset_type=body.type,
        album_id=_first_id(body.albumIds, "album"),
        person_id=_first_id(body.personIds, "person"),
        city=body.city, with_deleted=bool(body.withDeleted),
        random_order=True,
    )
    return _assets(rows)


class SmartSearchBody(BaseModel):
    query: Optional[str] = ""
    size: Optional[int] = 250
    page: Optional[int] = 1
    type: Optional[str] = None
    isFavorite: Optional[bool] = None


@router.post("/search/smart")
def search_smart(body: SmartSearchBody,
                 session: str = Depends(auth.require_session)):
    """자연어 검색 — PhotoNest의 의미 검색(search.find)을 그대로 쓴다."""
    text = (body.query or "").strip()
    if not text:
        return _search_response([], False, 1)
    media_type = None
    if body.type:
        media_type = "video" if body.type.upper() == "VIDEO" else "image"
    hits = search.find(text, media_type=media_type,
                       top_k=max(1, min(int(body.size or 250), MAX_PAGE)))
    ids = [h["id"] for h in hits]
    rows = query.assets_by_ids(ids)
    order = {mid: i for i, mid in enumerate(ids)}
    rows.sort(key=lambda r: order.get(r["id"], len(ids)))
    if body.isFavorite is not None:
        rows = [r for r in rows if bool(r["favorite"]) == body.isFavorite]
    return _search_response(rows, False, 1)


def _city_representatives():
    """지명별 대표 사진 [(지명, 에셋 행)] — 사진이 많은 지명 순."""
    with db.conn() as c:
        rows = c.execute(
            "SELECT place_name, MIN(id) media_id, COUNT(*) n FROM media "
            "WHERE trashed_at IS NULL AND place_name IS NOT NULL "
            "AND place_name != '' GROUP BY place_name ORDER BY n DESC LIMIT 20"
        ).fetchall()
    media_ids = [r["media_id"] for r in rows]
    by_id = {r["id"]: r for r in query.assets_by_ids(media_ids)}
    return [(r["place_name"], by_id[r["media_id"]]) for r in rows
            if r["media_id"] in by_id]


@router.get("/search/explore")
def search_explore(session: str = Depends(auth.require_session)):
    """"둘러보기" — 지명별 대표 사진을 {value, data} 묶음으로."""
    pairs = _city_representatives()
    assets = _assets([row for _name, row in pairs])
    items = [{"value": name, "data": asset}
             for (name, _row), asset in zip(pairs, assets)]
    return [{"fieldName": "exifInfo.city", "items": items}]


@router.get("/search/cities")
def search_cities(session: str = Depends(auth.require_session)):
    """지명별 대표 사진 — explore와 달리 에셋 배열 그대로 (스키마가 다르다)."""
    return _assets([row for _name, row in _city_representatives()])


@router.get("/search/suggestions")
def search_suggestions(type: Optional[str] = None,
                       session: str = Depends(auth.require_session)):
    if type != "city":
        return []
    with db.conn() as c:
        rows = c.execute(
            "SELECT DISTINCT place_name FROM media WHERE trashed_at IS NULL "
            "AND place_name IS NOT NULL AND place_name != '' ORDER BY place_name"
        ).fetchall()
    return [r["place_name"] for r in rows]


@router.get("/search/places")
def search_places(name: Optional[str] = None,
                  session: str = Depends(auth.require_session)):
    return []


@router.get("/search/person")
def search_person(name: str = "", withHidden: Optional[bool] = None,
                  session: str = Depends(auth.require_session)):
    people = [p for p in query.persons()
              if name.lower() in (p["name"] or "").lower()]
    state.register("person", [p["id"] for p in people])
    return [dto.person_response(p) for p in people]


# ---------- 인물 ----------

@router.get("/people")
def people_list(page: int = 1, size: int = 500, withHidden: Optional[bool] = None,
                session: str = Depends(auth.require_session)):
    people = query.persons()
    state.register("person", [p["id"] for p in people])
    size = max(1, min(size, MAX_PAGE))
    start = max(0, (max(1, page) - 1) * size)
    window = people[start:start + size]
    return {
        "people": [dto.person_response(p) for p in window],
        "total": len(people),
        "hidden": 0,
        "hasNextPage": start + size < len(people),
    }


@router.get("/people/{person_uuid}")
def person_get(person_uuid: str, session: str = Depends(auth.require_session)):
    person_id = _person_id(person_uuid)
    person = db.get_person(person_id)
    if not person:
        raise HTTPException(status_code=404, detail="Person not found")
    return dto.person_response(person)


@router.get("/people/{person_uuid}/statistics")
def person_statistics(person_uuid: str,
                      session: str = Depends(auth.require_session)):
    person_id = _person_id(person_uuid)
    return {"assets": len(query.person_asset_ids(person_id))}


@router.get("/people/{person_uuid}/thumbnail")
def person_thumbnail(person_uuid: str,
                     session: str = Depends(auth.require_session)):
    person_id = _person_id(person_uuid)
    rows = [p for p in query.persons() if p["id"] == person_id]
    if not rows or not rows[0]["cover_face_id"]:
        raise HTTPException(status_code=404, detail="No face thumbnail")
    return media.person_thumb_response(rows[0]["cover_face_id"])


class PersonUpdateBody(BaseModel):
    name: Optional[str] = None


@router.put("/people/{person_uuid}")
def person_update(person_uuid: str, body: PersonUpdateBody,
                  session: str = Depends(auth.require_session)):
    person_id = _person_id(person_uuid)
    if body.name is not None:
        db.rename_person(person_id, body.name)
    person = db.get_person(person_id)
    return dto.person_response(person)


# ---------- 지도 ----------

@router.get("/map/markers")
def map_markers(session: str = Depends(auth.require_session)):
    items = db.geo_items()
    state.register("asset", [it["id"] for it in items])
    return [
        {
            "id": state.asset_uuid(it["id"]),
            "lat": it["lat"], "lon": it["lon"],
            "city": it["place_name"] or None, "country": None, "state": None,
        }
        for it in items
    ]


@router.get("/map/reverse-geocode")
def reverse_geocode(lat: float, lon: float,
                    session: str = Depends(auth.require_session)):
    from .. import geoname
    if not geoname.available():
        return []
    name = geoname.lookup(lat, lon)
    return [{"city": name, "country": None, "state": None}] if name else []


# ---------- PhotoNest에 대응 개념이 없는 것들 ----------

@router.get("/partners")
def partners(direction: Optional[str] = None,
             session: str = Depends(auth.require_session)):
    return []


@router.get("/memories")
def memories(session: str = Depends(auth.require_session)):
    return []


@router.get("/memories/statistics")
def memories_statistics(session: str = Depends(auth.require_session)):
    return {"total": 0}


@router.get("/stacks")
def stacks(session: str = Depends(auth.require_session)):
    return []


@router.get("/tags")
def tags(session: str = Depends(auth.require_session)):
    return []


@router.get("/shared-links")
def shared_links(session: str = Depends(auth.require_session)):
    return []


@router.get("/activities")
def activities(albumId: Optional[str] = None,
               session: str = Depends(auth.require_session)):
    return []


@router.get("/activities/statistics")
def activities_statistics(albumId: Optional[str] = None,
                          session: str = Depends(auth.require_session)):
    return {"comments": 0, "likes": 0}


@router.get("/sessions")
def sessions(session: str = Depends(auth.require_session)):
    return []


@router.get("/duplicates")
def duplicates(session: str = Depends(auth.require_session)):
    return []


@router.get("/notifications")
def notifications(session: str = Depends(auth.require_session)):
    return []


@router.get("/view/folder/unique-paths")
def folder_paths(session: str = Depends(auth.require_session)):
    dirs = set()
    with db.conn() as c:
        # 커서를 그대로 순회한다 — 경로 전체를 메모리에 올리지 않는다
        for (path,) in c.execute(
            "SELECT path FROM media WHERE trashed_at IS NULL AND path IS NOT NULL"
        ):
            head = path.rsplit("/", 1)[0] if "/" in path else ""
            dirs.add(head or "/")
    return sorted(dirs)


@router.get("/view/folder")
def folder_view(path: str = "", session: str = Depends(auth.require_session)):
    prefix = path.strip("/")
    like = ("%s/%%" % prefix) if prefix else "%"
    with db.conn() as c:
        rows = [dict(r) for r in c.execute(
            f"SELECT {dto.ASSET_COLS} FROM media "
            "WHERE trashed_at IS NULL AND path LIKE ? "
            "AND instr(substr(path, ?), '/') = 0 LIMIT ?",
            (like, len(prefix) + 2 if prefix else 1, MAX_PAGE),
        ).fetchall()]
    return _assets(rows)
