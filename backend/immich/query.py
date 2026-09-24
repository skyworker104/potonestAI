"""이 계층이 쓰는 media/앨범/인물 조회 — Immich 응답에 필요한 모양으로.

db.py의 helper는 MEDIA_COLS에 sig가 없고 taken_at이 비어 있는 행을 그대로
두지만, Immich 앱은 모든 에셋에 촬영시각이 있다고 보고 타임라인을 묶는다.
그래서 taken_at이 없으면 파일 수정시각(sig의 뒷부분)으로 대체하는 식을
SQL에도 같은 규칙으로 넣어 두었다 (dto.taken_local의 폴백과 일치).
"""
import hashlib

from .. import db
from .dto import ASSET_COLS

def taken_expr(alias=""):
    """촬영시각 SQL 식. sig는 '크기-수정시각'이라 taken_at이 비면 그 뒤를 쓴다."""
    p = (alias + ".") if alias else ""
    mtime = f"CAST(substr({p}sig, instr({p}sig, '-') + 1) AS INTEGER)"
    return (f"COALESCE(NULLIF({p}taken_at, ''), "
            f"strftime('%Y-%m-%dT%H:%M:%S', {mtime}, 'unixepoch', 'localtime'))")


TAKEN = taken_expr()
MONTH = f"substr({TAKEN}, 1, 7)"


def _rows(c, q, args=()):
    return [dict(r) for r in c.execute(q, args).fetchall()]


def fingerprint(*values):
    """동기화 델타 비교용 지문 — 값이 하나라도 바뀌면 달라진다."""
    joined = "\x1f".join("" if v is None else str(v) for v in values)
    return hashlib.md5(joined.encode("utf-8")).hexdigest()[:16]


# ---------- 에셋 ----------

# 동기화 지문 — 이 값이 바뀌면 앱에 다시 보낸다.
# path는 넣지 않는다: media.id 자체가 경로에서 만들어지므로 경로가 바뀌면 id가
# 바뀌고, 그건 delete + create로 이미 드러난다.
_FP_EXPR = " || char(31) || ".join([
    "k.sha1",
    "COALESCE(m.type, '')", "COALESCE(m.taken_at, '')",
    "COALESCE(m.lat, '')", "COALESCE(m.lon, '')",
    "COALESCE(m.width, '')", "COALESCE(m.height, '')",
    "COALESCE(m.duration, '')", "COALESCE(m.favorite, 0)",
    "COALESCE(m.trashed_at, '')", "COALESCE(m.comment, '')",
    "COALESCE(m.place_name, '')", "COALESCE(m.sig, '')",
])

# 체크섬이 준비된 에셋만 동기화 대상이다(휴지통 항목 포함 — deletedAt로 나간다).
# SyncAssetV1.checksum은 null이 안 되고, 앱은 그 값으로 "이미 백업된 사진"을
# 판정하므로 체크섬 없는 에셋을 보내면 중복 업로드가 난다.
_SYNCABLE = ("FROM media m JOIN immich_checksum k "
             "ON k.media_id = m.id AND k.sig = m.sig")


def asset_changes():
    """동기화 그림자 테이블과 비교한 (upserts, deletes). 비교는 SQL이 한다.

    수만 장 라이브러리에서도 변경분만 메모리에 올라온다.
    """
    with db.conn() as c:
        upserts = [
            (r["id"], r["fp"]) for r in c.execute(
                f"SELECT m.id, {_FP_EXPR} fp {_SYNCABLE} "
                "LEFT JOIN immich_sync_row r ON r.kind='asset' AND r.ref = m.id "
                f"WHERE r.ref IS NULL OR r.deleted = 1 OR r.fp != ({_FP_EXPR})"
            ).fetchall()
        ]
        deletes = [
            r["ref"] for r in c.execute(
                "SELECT r.ref FROM immich_sync_row r "
                "WHERE r.kind='asset' AND r.deleted = 0 AND NOT EXISTS ("
                f"  SELECT 1 {_SYNCABLE} WHERE m.id = r.ref)"
            ).fetchall()
        ]
    return upserts, deletes


def assets_by_ids(media_ids):
    if not media_ids:
        return []
    out = []
    with db.conn() as c:
        for i in range(0, len(media_ids), 500):
            chunk = media_ids[i:i + 500]
            marks = ",".join("?" * len(chunk))
            out.extend(_rows(
                c, f"SELECT {ASSET_COLS} FROM media WHERE id IN ({marks})", chunk
            ))
    return out


def media_id_by_hash(content_hash):
    """PhotoNest 자체 중복 해시(md5: 크기+앞 4MB)로 활성 미디어 찾기.

    SHA-1이 아직 계산 안 된 사진과의 중복도 잡아내기 위한 2차 경로다.
    """
    with db.conn() as c:
        r = c.execute(
            "SELECT id FROM media WHERE hash=? AND trashed_at IS NULL LIMIT 1",
            (content_hash,),
        ).fetchone()
    return r["id"] if r else None


def asset_by_id(media_id):
    with db.conn() as c:
        rows = _rows(c, f"SELECT {ASSET_COLS} FROM media WHERE id=?", (media_id,))
    return rows[0] if rows else None


# ---------- 타임라인 ----------

def buckets(album_id=None, person_id=None, favorites=False, trashed=False):
    q = f"SELECT {MONTH} ym, COUNT(*) n FROM media"
    where, args = [], []
    if album_id is not None:
        q += " JOIN album_items ai ON ai.media_id = media.id"
        where.append("ai.album_id = ?")
        args.append(album_id)
    if person_id is not None:
        q += " JOIN faces f ON f.media_id = media.id"
        where.append("f.person_id = ?")
        args.append(person_id)
    where.append("trashed_at IS NOT NULL" if trashed else "trashed_at IS NULL")
    if favorites:
        where.append("favorite = 1")
    q += " WHERE " + " AND ".join(where) + " GROUP BY ym ORDER BY ym DESC"
    with db.conn() as c:
        rows = _rows(c, q, args)
    return [(r["ym"], r["n"]) for r in rows if r["ym"]]


def bucket_assets(month, album_id=None, person_id=None, favorites=False,
                  trashed=False):
    q = f"SELECT DISTINCT {_prefixed(ASSET_COLS)} FROM media"
    where, args = [], []
    if album_id is not None:
        q += " JOIN album_items ai ON ai.media_id = media.id"
        where.append("ai.album_id = ?")
        args.append(album_id)
    if person_id is not None:
        q += " JOIN faces f ON f.media_id = media.id"
        where.append("f.person_id = ?")
        args.append(person_id)
    where.append(f"{MONTH} = ?")
    args.append(month)
    where.append("trashed_at IS NOT NULL" if trashed else "trashed_at IS NULL")
    if favorites:
        where.append("favorite = 1")
    q += " WHERE " + " AND ".join(where) + f" ORDER BY {TAKEN} DESC"
    with db.conn() as c:
        return _rows(c, q, args)


def _prefixed(cols):
    """JOIN이 붙는 질의에서 컬럼 모호성을 없앤다."""
    return ", ".join("media." + c.strip() for c in cols.split(","))


# ---------- 검색 ----------

def search_assets(size=250, page=1, order="desc", taken_after=None,
                  taken_before=None, is_favorite=None, asset_type=None,
                  album_id=None, person_id=None, city=None, filename=None,
                  with_deleted=False, random_order=False):
    """MetadataSearchDto / RandomSearchDto 중 실제로 쓰이는 조건만 지원."""
    q = f"SELECT DISTINCT {_prefixed(ASSET_COLS)} FROM media"
    where, args = [], []
    if album_id is not None:
        q += " JOIN album_items ai ON ai.media_id = media.id"
        where.append("ai.album_id = ?")
        args.append(album_id)
    if person_id is not None:
        q += " JOIN faces f ON f.media_id = media.id"
        where.append("f.person_id = ?")
        args.append(person_id)
    if not with_deleted:
        where.append("trashed_at IS NULL")
    if is_favorite is not None:
        where.append("favorite = ?")
        args.append(1 if is_favorite else 0)
    if asset_type:
        where.append("type = ?")
        args.append("video" if asset_type.upper() == "VIDEO" else "image")
    if taken_after:
        where.append(f"{TAKEN} >= ?")
        args.append(taken_after)
    if taken_before:
        where.append(f"{TAKEN} <= ?")
        args.append(taken_before)
    if city:
        where.append("place_name LIKE ?")
        args.append("%%%s%%" % city)
    if filename:
        where.append("path LIKE ?")
        args.append("%%%s%%" % filename)
    if where:
        q += " WHERE " + " AND ".join(where)

    size = max(1, min(int(size or 250), 1000))
    page = max(1, int(page or 1))
    if random_order:
        q += " ORDER BY RANDOM()"
        offset = 0
    else:
        q += f" ORDER BY {TAKEN} " + ("ASC" if order == "asc" else "DESC")
        offset = (page - 1) * size
    q += " LIMIT ? OFFSET ?"
    args.extend([size + 1, offset])  # 다음 페이지 유무 판정용으로 1개 더

    with db.conn() as c:
        rows = _rows(c, q, args)
    has_next = len(rows) > size
    return rows[:size], has_next


# ---------- 앨범 ----------

def albums():
    """[{id, name, created_at, count, thumb_media_id}] — 최근 만든 것부터."""
    with db.conn() as c:
        rows = _rows(c, f"""
            SELECT a.id, a.name, a.created_at, a.cover_id, COUNT(ai.media_id) n,
                   (SELECT ai2.media_id FROM album_items ai2
                      JOIN media m2 ON m2.id = ai2.media_id
                     WHERE ai2.album_id = a.id AND m2.trashed_at IS NULL
                     ORDER BY {taken_expr('m2')} DESC
                     LIMIT 1) newest
            FROM albums a LEFT JOIN album_items ai ON ai.album_id = a.id
            GROUP BY a.id ORDER BY a.created_at DESC""")
    return [
        {
            "id": r["id"], "name": r["name"], "created_at": r["created_at"],
            "count": r["n"], "thumb_media_id": r["cover_id"] or r["newest"],
        }
        for r in rows
    ]


def album_fingerprints(album_rows):
    return {
        a["id"]: fingerprint(a["name"], a["created_at"], a["thumb_media_id"])
        for a in album_rows
    }


def album_asset_pairs():
    """앨범-에셋 연결 [(album_id, media_id)] — 활성 미디어만."""
    with db.conn() as c:
        rows = _rows(c, "SELECT ai.album_id, ai.media_id FROM album_items ai "
                        "JOIN media m ON m.id = ai.media_id "
                        "WHERE m.trashed_at IS NULL")
    return [(r["album_id"], r["media_id"]) for r in rows]


def album_asset_ids(album_id):
    with db.conn() as c:
        rows = _rows(c, f"""SELECT ai.media_id FROM album_items ai
                              JOIN media m ON m.id = ai.media_id
                             WHERE ai.album_id = ? AND m.trashed_at IS NULL
                             ORDER BY {taken_expr('m')} DESC""",
                    (album_id,))
    return [r["media_id"] for r in rows]


# ---------- 인물 ----------

def persons():
    return db.list_persons(min_faces=1)


def person_asset_ids(person_id):
    return db.person_media_ids(person_id)
