"""Immich 호환 계층의 영구 상태 — UUID 매핑, SHA-1 체크섬, 동기화 델타.

PhotoNest의 media.id는 경로에서 만든 슬러그이고, 중복 탐지 해시는
md5(파일크기 + 앞 4MB)다. Immich 앱은 이 둘 대신
  - UUID v4 형식의 asset id
  - base64(SHA-1(전체 파일)) 체크섬
을 전제로 동작한다. 특히 체크섬은 앱이 폰 안의 사진을 직접 SHA-1 해서 서버
값과 맞춰보는 "이미 백업됨" 판정에 쓰이므로, 다른 해시로는 대체가 안 된다.

media 테이블은 건드리지 않는다 — 호환 계층을 떼어내도 원래 스키마가 그대로
남도록, 필요한 값은 전부 immich_* 테이블에 따로 보관한다.
"""
import base64
import hashlib
import sqlite3
import threading
import time
import uuid
from pathlib import Path

from .. import db, indexer

# 파생 UUID의 네임스페이스. 바꾸면 모든 asset id가 달라지고 앱은 전량 재동기화한다.
_NS = "photonest.immich.v1:"

SCHEMA = """
-- 파생 UUID → PhotoNest 내부 식별자. UUID는 ref에서 결정적으로 계산되지만
-- 역방향(UUID → ref)은 계산할 수 없어 매핑을 남긴다.
CREATE TABLE IF NOT EXISTS immich_ids (
  uuid TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  ref  TEXT NOT NULL,
  UNIQUE (kind, ref)
);
-- 전체 파일 SHA-1. sig(크기-수정시각)가 달라지면 무효로 보고 다시 계산한다.
-- path는 아직 색인되지 않은(=media 행이 없는) 업로드 직후 파일의 체크섬이
-- 아직 유효한지 디스크에서 직접 확인하기 위해 둔다.
CREATE TABLE IF NOT EXISTS immich_checksum (
  media_id TEXT PRIMARY KEY,
  path     TEXT NOT NULL DEFAULT '',
  sig      TEXT NOT NULL,
  sha1     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_immich_checksum_sha1 ON immich_checksum(sha1);
-- 동기화 델타용 그림자 테이블. media에 updated_at이 없어 증분 동기화를 할 수
-- 없으므로, 보낸 내용의 지문(fp)을 기억해 두고 매번 비교해 변경분만 낸다.
CREATE TABLE IF NOT EXISTS immich_sync_row (
  kind    TEXT NOT NULL,
  ref     TEXT NOT NULL,
  seq     INTEGER NOT NULL,
  fp      TEXT NOT NULL,
  deleted INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (kind, ref)
);
CREATE INDEX IF NOT EXISTS idx_immich_sync_seq ON immich_sync_row(kind, deleted, seq);
CREATE TABLE IF NOT EXISTS immich_sync_meta (k TEXT PRIMARY KEY, v INTEGER NOT NULL);
-- 클라이언트가 어디까지 받았는지 (세션 × 엔티티 타입별 워터마크)
CREATE TABLE IF NOT EXISTS immich_sync_ack (
  session TEXT NOT NULL,
  type    TEXT NOT NULL,
  seq     INTEGER NOT NULL,
  PRIMARY KEY (session, type)
);
CREATE TABLE IF NOT EXISTS immich_sessions (
  token      TEXT PRIMARY KEY,
  device     TEXT,
  created_at TEXT NOT NULL
);
"""


def init():
    db.init()
    with db.conn() as c:
        c.executescript(SCHEMA)
        try:  # 이전 버전 테이블에 path가 없을 수 있다 (멱등)
            c.execute("ALTER TABLE immich_checksum ADD COLUMN path TEXT NOT NULL "
                      "DEFAULT ''")
        except sqlite3.OperationalError:
            pass


# ---------- UUID 파생 / 매핑 ----------

def uuid_of(kind: str, ref) -> str:
    """kind+ref로부터 결정적으로 UUID를 만든다 (v4 형식 — Immich가 강제한다).

    uuid5는 버전 비트가 5여서 Immich의 UUID v4 정규식을 통과하지 못한다.
    그래서 SHA-1 앞 16바이트에 v4 버전/변형 비트만 덮어쓴다.
    """
    digest = hashlib.sha1((_NS + kind + ":" + str(ref)).encode("utf-8")).digest()
    b = bytearray(digest[:16])
    b[6] = (b[6] & 0x0F) | 0x40  # version 4
    b[8] = (b[8] & 0x3F) | 0x80  # RFC 4122 variant
    return str(uuid.UUID(bytes=bytes(b)))


USER_ID = uuid_of("user", "local")


def asset_uuid(media_id):
    return uuid_of("asset", media_id)


def album_uuid(album_id):
    return uuid_of("album", album_id)


def person_uuid(person_id):
    return uuid_of("person", person_id)


def register(kind: str, refs):
    """UUID → ref 역방향 조회용 매핑을 채운다 (멱등, 한 트랜잭션)."""
    refs = [r for r in refs if r is not None]
    if not refs:
        return
    rows = [(uuid_of(kind, r), kind, str(r)) for r in refs]
    with db.conn() as c:
        c.executemany(
            "INSERT OR IGNORE INTO immich_ids (uuid, kind, ref) VALUES (?, ?, ?)", rows
        )


def resolve(uuid_str: str, kind=None):
    """UUID → ref. 모르는 UUID면 None."""
    q = "SELECT kind, ref FROM immich_ids WHERE uuid=?"
    args = [str(uuid_str)]
    if kind:
        q += " AND kind=?"
        args.append(kind)
    with db.conn() as c:
        r = c.execute(q, args).fetchone()
    if not r:
        return None
    return r["ref"]


def resolve_many(uuids, kind=None):
    """UUID 목록 → {uuid: ref}. 모르는 것은 결과에서 빠진다."""
    uuids = [str(u) for u in uuids if u]
    if not uuids:
        return {}
    out = {}
    with db.conn() as c:
        for i in range(0, len(uuids), 500):
            chunk = uuids[i:i + 500]
            marks = ",".join("?" * len(chunk))
            q = f"SELECT uuid, ref FROM immich_ids WHERE uuid IN ({marks})"
            args = list(chunk)
            if kind:
                q += " AND kind=?"
                args.append(kind)
            for r in c.execute(q, args).fetchall():
                out[r["uuid"]] = r["ref"]
    return out


# ---------- SHA-1 체크섬 ----------

def media_id_for_path(rel: str) -> str:
    """상대경로 → media.id. indexer._index_file과 같은 규칙이어야 한다.

    업로드 응답은 색인이 끝나기 전에 asset id를 돌려줘야 하는데, 경로에서
    id를 같은 규칙으로 미리 계산하면 나중에 색인된 행과 UUID가 일치한다.
    """
    return rel.replace("/", "__").replace("\\", "__").replace(".", "_")


def sha1_of_file(path: Path) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return base64.b64encode(h.digest()).decode("ascii")


def record_checksum(media_id: str, path: str, sig: str, sha1: str):
    with db.conn() as c:
        c.execute(
            "INSERT INTO immich_checksum (media_id, path, sig, sha1) "
            "VALUES (?, ?, ?, ?) ON CONFLICT(media_id) DO UPDATE SET "
            "path=excluded.path, sig=excluded.sig, sha1=excluded.sha1",
            (media_id, path, sig, sha1),
        )


def _file_sig(rel_path: str):
    """디스크의 현재 sig('크기-수정시각'). 파일이 없으면 None."""
    try:
        st = (indexer.PHOTOS_DIR / rel_path).stat()
    except OSError:
        return None
    return "%d-%d" % (st.st_size, int(st.st_mtime))


def _valid_rows(rows):
    """체크섬 행 중 아직 유효한 것만 — 휴지통 여부는 여기서 따지지 않는다.

    media 행이 있으면 sig가 맞아야 하고(파일이 교체되면 무효), 아직 색인되지
    않았으면 디스크의 파일이 그대로 있어야 한다. 업로드 직후 색인이 끝나기 전의
    짧은 구간에도 앱이 중복을 올리지 않게 하려면 후자가 필요하다.
    """
    for r in rows:
        if r["mid"] is not None:
            if r["msig"] == r["sig"]:
                yield r
        elif r["path"] and _file_sig(r["path"]) == r["sig"]:
            yield r


_CHECKSUM_JOIN = (
    "SELECT k.media_id, k.path, k.sig, k.sha1, m.id mid, m.sig msig, m.trashed_at "
    "FROM immich_checksum k LEFT JOIN media m ON m.id = k.media_id "
)


def checksums_for(media_ids):
    """{media_id: sha1} — sig가 현재 media 행과 맞는 것만.

    라이브러리 전체를 한 번에 올리지 않도록 언제나 id 목록을 받는다.
    """
    media_ids = [m for m in media_ids if m]
    if not media_ids:
        return {}
    out = {}
    with db.conn() as c:
        for i in range(0, len(media_ids), 500):
            chunk = media_ids[i:i + 500]
            marks = ",".join("?" * len(chunk))
            rows = c.execute(
                "SELECT k.media_id, k.sha1 FROM immich_checksum k "
                "JOIN media m ON m.id = k.media_id AND m.sig = k.sig "
                f"WHERE k.media_id IN ({marks})",
                chunk,
            ).fetchall()
            for r in rows:
                out[r["media_id"]] = r["sha1"]
    return out


def checksum(media_id: str):
    """이 미디어의 SHA-1 (휴지통 항목도 포함 — 동기화는 삭제 상태도 내보낸다)."""
    with db.conn() as c:
        rows = c.execute(_CHECKSUM_JOIN + "WHERE k.media_id=?",
                         (media_id,)).fetchall()
    for r in _valid_rows(rows):
        return r["sha1"]
    return None


def media_id_by_sha1(sha1: str):
    """같은 내용의 미디어 찾기 — 업로드 중복 판정용. 색인 전 파일도 잡는다.

    휴지통에 있는 사진은 중복으로 보지 않는다(재업로드 허용) — PhotoNest의
    업로드 경로(upload._hash_exists)와 같은 정책이다.
    """
    with db.conn() as c:
        rows = c.execute(_CHECKSUM_JOIN + "WHERE k.sha1=?", (sha1,)).fetchall()
    for r in _valid_rows(rows):
        if r["trashed_at"] is None:
            return r["media_id"]
    return None


# ---------- 체크섬 backfill ----------

_backfill_state = {"running": False, "pending": 0, "done": 0, "error": None}
_backfill_lock = threading.Lock()


def backfill_state():
    return dict(_backfill_state)


def _pending_checksums(limit=200):
    """체크섬이 없거나 sig가 어긋난 활성 미디어 (id, path, sig)."""
    with db.conn() as c:
        rows = c.execute(
            "SELECT m.id, m.path, m.sig FROM media m "
            "LEFT JOIN immich_checksum k ON k.media_id = m.id "
            "WHERE m.trashed_at IS NULL AND m.path IS NOT NULL "
            "AND (k.sha1 IS NULL OR k.sig != m.sig) LIMIT ?",
            (limit,),
        ).fetchall()
        total = c.execute(
            "SELECT COUNT(*) n FROM media m "
            "LEFT JOIN immich_checksum k ON k.media_id = m.id "
            "WHERE m.trashed_at IS NULL AND m.path IS NOT NULL "
            "AND (k.sha1 IS NULL OR k.sig != m.sig)"
        ).fetchone()["n"]
    return [tuple(r) for r in rows], total


def prune_checksums():
    """media 행도 없고 파일도 없는 체크섬 행 제거.

    경로가 바뀌면 media.id도 바뀌어 옛 행이 남는다. 그대로 두면 없는 사진을
    "이미 있음"으로 판정해 앱의 업로드를 잘못 막는다.
    """
    with db.conn() as c:
        rows = c.execute(
            "SELECT k.media_id, k.path, k.sig FROM immich_checksum k "
            "LEFT JOIN media m ON m.id = k.media_id WHERE m.id IS NULL"
        ).fetchall()
        gone = [(r["media_id"],) for r in rows
                if not r["path"] or _file_sig(r["path"]) != r["sig"]]
        if gone:
            c.executemany("DELETE FROM immich_checksum WHERE media_id=?", gone)
    return len(gone)


def _backfill_loop():
    """데몬 스레드 본체 — DB/디스크 오류로 죽더라도 흔적을 남기고 조용히 멈춘다."""
    try:
        prune_checksums()
        while True:
            batch, total = _pending_checksums()
            _backfill_state["pending"] = total
            if not batch:
                return
            for media_id, rel, sig in batch:
                p = indexer.PHOTOS_DIR / rel
                try:
                    record_checksum(media_id, rel, sig, sha1_of_file(p))
                    _backfill_state["done"] += 1
                except OSError:
                    # 읽을 수 없는 파일은 계속 재시도하지 않도록 빈 체크섬 대신
                    # 건너뛴다 — 다음 색인에서 사라지거나 고쳐진다.
                    _backfill_state["pending"] = max(0, total - 1)
                # 저사양 기기(라즈베리파이)에서 디스크를 독점하지 않도록 양보
                time.sleep(0.01)
    except sqlite3.Error as e:
        _backfill_state["error"] = str(e)
    finally:
        _backfill_state["running"] = False


def start_backfill():
    """SHA-1 backfill을 백그라운드로 시작 (이미 돌고 있으면 그대로 둔다).

    여러 번 불러도 안전하다 — 앱이 붙을 때마다 호출해 진행을 이어간다.
    """
    with _backfill_lock:
        if _backfill_state["running"]:
            return
        _backfill_state["running"] = True
        _backfill_state["done"] = 0
        _backfill_state["error"] = None
    threading.Thread(target=_backfill_loop, daemon=True).start()


# ---------- 동기화 델타 ----------

def _next_seq(c, count):
    """seq 카운터를 count만큼 예약하고 첫 값을 돌려준다."""
    row = c.execute("SELECT v FROM immich_sync_meta WHERE k='seq'").fetchone()
    cur = row["v"] if row else 0
    c.execute(
        "INSERT INTO immich_sync_meta (k, v) VALUES ('seq', ?) "
        "ON CONFLICT(k) DO UPDATE SET v=excluded.v",
        (cur + count,),
    )
    return cur + 1


def apply_changes(kind: str, upserts, deletes):
    """그림자 테이블에 변경분을 적용한다. upserts=[(ref, fp)], deletes=[ref].

    바뀐 행에만 새 seq를 주므로, 클라이언트는 자기 워터마크보다 큰 seq만
    받아서 증분 동기화가 된다. 반환: 변경된 행 수.
    """
    upserts = [(str(ref), fp) for ref, fp in upserts]
    deletes = [str(ref) for ref in deletes]
    changed = len(upserts) + len(deletes)
    if not changed:
        return 0
    with db.conn() as c:
        seq = _next_seq(c, changed)
        c.executemany(
            "INSERT INTO immich_sync_row (kind, ref, seq, fp, deleted) "
            "VALUES (?, ?, ?, ?, 0) ON CONFLICT(kind, ref) DO UPDATE SET "
            "seq=excluded.seq, fp=excluded.fp, deleted=0",
            [(kind, ref, seq + i, fp) for i, (ref, fp) in enumerate(upserts)],
        )
        c.executemany(
            "UPDATE immich_sync_row SET seq=?, deleted=1 WHERE kind=? AND ref=?",
            [(seq + len(upserts) + i, kind, ref) for i, ref in enumerate(deletes)],
        )
    return changed


def refresh(kind: str, current: dict):
    """현재 상태 전체({ref: fp})와 비교해 변경분을 적용한다.

    행 수가 적은 종류(사용자·앨범·앨범연결)에만 쓴다. 에셋은 수만 건이 될 수
    있어 파이썬으로 전량 비교하지 않고 sync._refresh_assets가 SQL로 비교한다.
    """
    current = {str(k): v for k, v in current.items()}
    with db.conn() as c:
        have = {
            r["ref"]: (r["fp"], r["deleted"])
            for r in c.execute(
                "SELECT ref, fp, deleted FROM immich_sync_row WHERE kind=?", (kind,)
            ).fetchall()
        }
    upserts = [
        (ref, fp) for ref, fp in current.items()
        if have.get(ref) is None or have[ref][0] != fp or have[ref][1]
    ]
    deletes = [
        ref for ref, (_fp, deleted) in have.items()
        if ref not in current and not deleted
    ]
    return apply_changes(kind, upserts, deletes)


def changed_rows(kind: str, deleted: bool, after: int, limit: int):
    """after보다 큰 seq를 가진 행 (seq 순). 페이징용."""
    with db.conn() as c:
        rows = c.execute(
            "SELECT ref, seq FROM immich_sync_row "
            "WHERE kind=? AND deleted=? AND seq>? ORDER BY seq LIMIT ?",
            (kind, 1 if deleted else 0, after, limit),
        ).fetchall()
    return [(r["ref"], r["seq"]) for r in rows]


def ack_get(session: str, type_: str) -> int:
    with db.conn() as c:
        r = c.execute(
            "SELECT seq FROM immich_sync_ack WHERE session=? AND type=?",
            (session, type_),
        ).fetchone()
    return r["seq"] if r else 0


def ack_all(session: str):
    with db.conn() as c:
        rows = c.execute(
            "SELECT type, seq FROM immich_sync_ack WHERE session=?", (session,)
        ).fetchall()
    return {r["type"]: r["seq"] for r in rows}


def ack_set(session: str, type_: str, seq: int):
    with db.conn() as c:
        # 뒤로 가는 ack는 무시 — 재전송 중 낮은 값이 올 수 있다
        c.execute(
            "INSERT INTO immich_sync_ack (session, type, seq) VALUES (?, ?, ?) "
            "ON CONFLICT(session, type) DO UPDATE SET seq=MAX(seq, excluded.seq)",
            (session, type_, int(seq)),
        )


def ack_clear(session: str, types=None):
    with db.conn() as c:
        if types:
            c.executemany(
                "DELETE FROM immich_sync_ack WHERE session=? AND type=?",
                [(session, t) for t in types],
            )
        else:
            c.execute("DELETE FROM immich_sync_ack WHERE session=?", (session,))
