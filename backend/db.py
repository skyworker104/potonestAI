"""SQLite 저장 계층 — 미디어 메타데이터, 임베딩, 앨범, 즐겨찾기, 휴지통."""
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import numpy as np

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("DATA_DIR", BASE_DIR / "data"))
DB_FILE = DATA_DIR / "photonest.db"

_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS media (
  id TEXT PRIMARY KEY,
  path TEXT,
  type TEXT NOT NULL,
  taken_at TEXT,
  lat REAL, lon REAL,
  width INTEGER, height INTEGER,
  duration REAL DEFAULT 0,
  sig TEXT,
  hash TEXT,
  favorite INTEGER DEFAULT 0,
  trashed_at TEXT,
  trash_path TEXT,
  embedding BLOB
);
CREATE INDEX IF NOT EXISTS idx_media_taken ON media(taken_at DESC);
CREATE INDEX IF NOT EXISTS idx_media_hash ON media(hash);
CREATE TABLE IF NOT EXISTS albums (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  created_at TEXT NOT NULL,
  cover_id TEXT
);
CREATE TABLE IF NOT EXISTS album_items (
  album_id INTEGER NOT NULL,
  media_id TEXT NOT NULL,
  added_at TEXT NOT NULL,
  PRIMARY KEY (album_id, media_id)
);
CREATE TABLE IF NOT EXISTS faces (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  media_id TEXT NOT NULL,
  bbox TEXT,
  embedding BLOB,
  person_id INTEGER
);
CREATE INDEX IF NOT EXISTS idx_faces_media ON faces(media_id);
CREATE INDEX IF NOT EXISTS idx_faces_person ON faces(person_id);
CREATE TABLE IF NOT EXISTS persons (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT,
  cover_face_id INTEGER
);
-- 폰이 업로드와 함께 보낸 임베딩 (색인 전 임시 보관 — 콘텐츠 해시 키).
-- 인덱서가 해당 파일을 색인할 때 모델이 맞으면 media.embedding으로 옮긴다.
CREATE TABLE IF NOT EXISTS pending_embeddings (
  hash TEXT PRIMARY KEY,
  model TEXT NOT NULL,
  vec BLOB NOT NULL,
  created_at TEXT NOT NULL
);
"""

MIGRATIONS = [
    "ALTER TABLE media ADD COLUMN faces_scanned INTEGER DEFAULT 0",
    # (0,0)은 위치정보가 제거된 사진 — 좌표 없음으로 정정 (멱등)
    "UPDATE media SET lat=NULL, lon=NULL WHERE lat=0 AND lon=0",
    # 사용자 코멘트 + 코멘트 의미검색용 임베딩
    "ALTER TABLE media ADD COLUMN comment TEXT",
    "ALTER TABLE media ADD COLUMN comment_emb BLOB",
    # OCR(사진 속 글자) 추출 텍스트 + 스캔 여부
    "ALTER TABLE media ADD COLUMN ocr_text TEXT",
    "ALTER TABLE media ADD COLUMN ocr_scanned INTEGER DEFAULT 0",
    # 자동 캡션(비전 LLM) — 상황/관계 질의("웃고 있는", "생일 파티") 보강용.
    # 비용이 커 신규 사진에만 적용(indexer._run_pipeline 참고), 기존 사진은 소급 안 함.
    "ALTER TABLE media ADD COLUMN caption TEXT",
    "ALTER TABLE media ADD COLUMN caption_emb BLOB",
    # 임베딩을 만든 모델 태그 — 백엔드(SigLIP2/CLIP-ONNX)가 달라지면 벡터가
    # 비호환이므로, 검색은 같은 모델끼리만 비교하고 불일치분은 재색인한다.
    "ALTER TABLE media ADD COLUMN embed_model TEXT",
    # 태그 도입 전의 임베딩은 전부 SigLIP2였음 — 소급 태깅 (멱등)
    "UPDATE media SET embed_model='google/siglip2-base-patch16-256' "
    "WHERE embedding IS NOT NULL AND embed_model IS NULL",
]


@contextmanager
def conn():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with _lock:
        c = sqlite3.connect(DB_FILE)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        try:
            yield c
            c.commit()
        finally:
            c.close()


def init():
    with conn() as c:
        c.executescript(SCHEMA)
        for mig in MIGRATIONS:
            try:
                c.execute(mig)
            except sqlite3.OperationalError:
                pass  # 이미 적용됨


MEDIA_COLS = (
    "id, path, type, taken_at, lat, lon, width, height, duration, favorite, "
    "trashed_at, comment"
)


def row_to_item(r):
    return {k: r[k] for k in MEDIA_COLS.replace(" ", "").split(",")}


# ---------- 미디어 ----------

def upsert_media(meta, embedding=None):
    emb = embedding.astype(np.float32).tobytes() if embedding is not None else None
    with conn() as c:
        c.execute(
            """INSERT INTO media (id, path, type, taken_at, lat, lon, width, height,
                                  duration, sig, hash, embedding)
               VALUES (:id, :path, :type, :taken_at, :lat, :lon, :width, :height,
                       :duration, :sig, :hash, :embedding)
               ON CONFLICT(id) DO UPDATE SET
                 path=:path, taken_at=:taken_at, lat=:lat, lon=:lon,
                 width=:width, height=:height, duration=:duration, sig=:sig,
                 hash=:hash,
                 embedding=COALESCE(:embedding, embedding)""",
            dict(meta, embedding=emb),
        )


def set_embedding(media_id, embedding, model=None):
    with conn() as c:
        c.execute(
            "UPDATE media SET embedding=?, embed_model=? WHERE id=?",
            (embedding.astype(np.float32).tobytes(), model, media_id),
        )


def set_comment(media_id, comment, embedding=None):
    comment = (comment or "").strip() or None
    emb = embedding.astype(np.float32).tobytes() if embedding is not None else None
    with conn() as c:
        c.execute(
            "UPDATE media SET comment=?, comment_emb=? WHERE id=?",
            (comment, emb, media_id),
        )


def comment_embeddings():
    """코멘트가 있는 활성 미디어의 (ids, 임베딩 행렬). 코멘트 의미검색용."""
    with conn() as c:
        rows = c.execute(
            "SELECT id, comment_emb FROM media "
            "WHERE trashed_at IS NULL AND comment_emb IS NOT NULL"
        ).fetchall()
    if not rows:
        return [], None
    ids = [r["id"] for r in rows]
    mat = np.vstack([np.frombuffer(r["comment_emb"], dtype=np.float32) for r in rows])
    return ids, mat


def set_caption(media_id, caption, embedding=None):
    caption = (caption or "").strip() or None
    emb = embedding.astype(np.float32).tobytes() if embedding is not None else None
    with conn() as c:
        c.execute(
            "UPDATE media SET caption=?, caption_emb=? WHERE id=?",
            (caption, emb, media_id),
        )


def caption_embeddings():
    """자동 캡션이 있는 활성 미디어의 (ids, 임베딩 행렬). 캡션 의미검색용."""
    with conn() as c:
        rows = c.execute(
            "SELECT id, caption_emb FROM media "
            "WHERE trashed_at IS NULL AND caption_emb IS NOT NULL"
        ).fetchall()
    if not rows:
        return [], None
    ids = [r["id"] for r in rows]
    mat = np.vstack([np.frombuffer(r["caption_emb"], dtype=np.float32) for r in rows])
    return ids, mat


def get_by_path():
    """path → {id, sig} (활성 미디어만). 증분 색인용."""
    with conn() as c:
        rows = c.execute(
            "SELECT path, id, sig FROM media WHERE trashed_at IS NULL"
        ).fetchall()
    return {r["path"]: {"id": r["id"], "sig": r["sig"]} for r in rows}


def remove_missing(present_paths):
    """디스크에서 사라진 활성 미디어 행 제거 (휴지통 항목은 유지)."""
    with conn() as c:
        rows = c.execute(
            "SELECT id, path FROM media WHERE trashed_at IS NULL"
        ).fetchall()
        gone = [r["id"] for r in rows if r["path"] not in present_paths]
        for mid in gone:
            c.execute("DELETE FROM media WHERE id=?", (mid,))
            c.execute("DELETE FROM album_items WHERE media_id=?", (mid,))
    return len(gone)


def get_media(media_id):
    with conn() as c:
        r = c.execute(
            f"SELECT {MEDIA_COLS}, trash_path FROM media WHERE id=?", (media_id,)
        ).fetchone()
    return dict(r) if r else None


def list_photos(month=None, album_id=None, favorites=False, trashed=False,
                ids=None, limit=2000, offset=0):
    q = f"SELECT {MEDIA_COLS} FROM media"
    where, args = [], []
    if trashed:
        where.append("trashed_at IS NOT NULL")
    else:
        where.append("trashed_at IS NULL")
    if month:
        where.append("substr(taken_at, 1, 7) = ?")
        args.append(month)
    if favorites:
        where.append("favorite = 1")
    if album_id is not None:
        q += " JOIN album_items ai ON ai.media_id = media.id"
        where.append("ai.album_id = ?")
        args.append(album_id)
    if ids is not None:
        if not ids:
            return []
        where.append(f"media.id IN ({','.join('?' * len(ids))})")
        args.extend(ids)
    q += " WHERE " + " AND ".join(where)
    q += " ORDER BY taken_at DESC LIMIT ? OFFSET ?"
    args.extend([limit, offset])
    with conn() as c:
        return [row_to_item(r) for r in c.execute(q, args).fetchall()]


def timeline_months():
    with conn() as c:
        rows = c.execute(
            """SELECT substr(taken_at, 1, 7) ym, COUNT(*) n FROM media
               WHERE trashed_at IS NULL GROUP BY ym ORDER BY ym DESC"""
        ).fetchall()
        total = c.execute(
            "SELECT COUNT(*) n FROM media WHERE trashed_at IS NULL"
        ).fetchone()["n"]
    return {"months": [{"ym": r["ym"], "count": r["n"]} for r in rows], "total": total}


def set_favorite(media_id, value):
    with conn() as c:
        c.execute("UPDATE media SET favorite=? WHERE id=?", (1 if value else 0, media_id))


def set_location(media_id, lat, lon):
    """사용자가 지도에서 수동 지정한 위치 저장 (lat/lon=None이면 위치 삭제)."""
    with conn() as c:
        c.execute("UPDATE media SET lat=?, lon=? WHERE id=?", (lat, lon, media_id))


def set_trashed(media_id, trash_path):
    with conn() as c:
        c.execute(
            "UPDATE media SET trashed_at=?, trash_path=? WHERE id=?",
            (datetime.now().isoformat(), trash_path, media_id),
        )


def set_restored(media_id):
    with conn() as c:
        c.execute(
            "UPDATE media SET trashed_at=NULL, trash_path=NULL WHERE id=?", (media_id,)
        )


def delete_media(media_id):
    with conn() as c:
        c.execute("DELETE FROM media WHERE id=?", (media_id,))
        c.execute("DELETE FROM album_items WHERE media_id=?", (media_id,))


def list_trash():
    with conn() as c:
        return [
            row_to_item(r)
            for r in c.execute(
                f"SELECT {MEDIA_COLS} FROM media WHERE trashed_at IS NOT NULL "
                "ORDER BY trashed_at DESC"
            ).fetchall()
        ]


def geo_items():
    with conn() as c:
        return [
            row_to_item(r)
            for r in c.execute(
                f"SELECT {MEDIA_COLS} FROM media "
                "WHERE trashed_at IS NULL AND lat IS NOT NULL"
            ).fetchall()
        ]


def duplicate_groups():
    with conn() as c:
        rows = c.execute(
            f"""SELECT {MEDIA_COLS}, hash FROM media
                WHERE trashed_at IS NULL AND hash IN (
                  SELECT hash FROM media WHERE trashed_at IS NULL AND hash IS NOT NULL
                  GROUP BY hash HAVING COUNT(*) > 1)
                ORDER BY hash, taken_at"""
        ).fetchall()
    groups = {}
    for r in rows:
        groups.setdefault(r["hash"], []).append(row_to_item(r))
    return list(groups.values())


# ---------- 임베딩 (검색용 행렬) ----------

def load_embeddings(model=None, dim=768):
    """임베딩이 있는 활성 미디어의 (ids, 행렬) 반환.

    model을 주면 그 모델로 만든 벡터만 (백엔드 간 벡터 비호환 — embedder 참고).
    """
    q = "SELECT id, embedding FROM media WHERE trashed_at IS NULL AND embedding IS NOT NULL"
    args = ()
    if model:
        q += " AND embed_model=?"
        args = (model,)
    with conn() as c:
        rows = c.execute(q, args).fetchall()
    if not rows:
        return [], np.zeros((0, dim), dtype=np.float32)
    ids = [r["id"] for r in rows]
    mat = np.vstack([np.frombuffer(r["embedding"], dtype=np.float32) for r in rows])
    return ids, mat


def save_pending_embedding(hash_, model, vec):
    """폰이 보낸 임베딩을 색인 전 임시 보관 (같은 해시 재업로드는 덮어씀)."""
    with conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO pending_embeddings (hash, model, vec, created_at) "
            "VALUES (?, ?, ?, ?)",
            (hash_, model, vec.astype(np.float32).tobytes(),
             datetime.now().isoformat()),
        )


def pop_pending_embedding(hash_):
    """해시로 보관된 임베딩을 꺼내고 삭제. (model, vec) 또는 (None, None)."""
    if not hash_:
        return None, None
    with conn() as c:
        row = c.execute(
            "SELECT model, vec FROM pending_embeddings WHERE hash=?", (hash_,)
        ).fetchone()
        if not row:
            return None, None
        c.execute("DELETE FROM pending_embeddings WHERE hash=?", (hash_,))
    return row["model"], np.frombuffer(row["vec"], dtype=np.float32)


def missing_embedding_ids(model=None):
    """임베딩이 없거나(모델 지정 시) 다른 모델로 만들어진 미디어 id — 재색인 대상."""
    if model:
        q = ("SELECT id FROM media WHERE trashed_at IS NULL "
             "AND (embedding IS NULL OR COALESCE(embed_model,'') != ?)")
        args = (model,)
    else:
        q = "SELECT id FROM media WHERE trashed_at IS NULL AND embedding IS NULL"
        args = ()
    with conn() as c:
        return [r["id"] for r in c.execute(q, args).fetchall()]


# ---------- 앨범 ----------

def create_album(name):
    with conn() as c:
        cur = c.execute(
            "INSERT INTO albums (name, created_at) VALUES (?, ?)",
            (name, datetime.now().isoformat()),
        )
        return cur.lastrowid


def list_albums():
    with conn() as c:
        rows = c.execute(
            """SELECT a.id, a.name, a.created_at, a.cover_id, COUNT(ai.media_id) n,
                      MIN(ai.media_id) first_item
               FROM albums a LEFT JOIN album_items ai ON ai.album_id = a.id
               GROUP BY a.id ORDER BY a.created_at DESC"""
        ).fetchall()
    return [
        {
            "id": r["id"], "name": r["name"], "created_at": r["created_at"],
            "count": r["n"], "cover_id": r["cover_id"] or r["first_item"],
        }
        for r in rows
    ]


def get_album(album_id):
    with conn() as c:
        r = c.execute("SELECT * FROM albums WHERE id=?", (album_id,)).fetchone()
    return dict(r) if r else None


def rename_album(album_id, name):
    with conn() as c:
        c.execute("UPDATE albums SET name=? WHERE id=?", (name, album_id))


def delete_album(album_id):
    with conn() as c:
        c.execute("DELETE FROM albums WHERE id=?", (album_id,))
        c.execute("DELETE FROM album_items WHERE album_id=?", (album_id,))


def add_to_album(album_id, media_ids):
    now = datetime.now().isoformat()
    with conn() as c:
        for mid in media_ids:
            c.execute(
                "INSERT OR IGNORE INTO album_items (album_id, media_id, added_at) "
                "VALUES (?, ?, ?)",
                (album_id, mid, now),
            )


def remove_from_album(album_id, media_ids):
    with conn() as c:
        for mid in media_ids:
            c.execute(
                "DELETE FROM album_items WHERE album_id=? AND media_id=?",
                (album_id, mid),
            )


def find_album_by_name(name):
    with conn() as c:
        r = c.execute(
            "SELECT id FROM albums WHERE name LIKE ? ORDER BY created_at DESC",
            (f"%{name}%",),
        ).fetchone()
    return r["id"] if r else None


# ---------- 얼굴 / 인물 ----------

def unscanned_media_ids():
    with conn() as c:
        return [
            r["id"]
            for r in c.execute(
                "SELECT id FROM media WHERE trashed_at IS NULL "
                "AND COALESCE(faces_scanned, 0) = 0 AND type = 'image'"
            ).fetchall()
        ]


def mark_faces_scanned(media_id):
    with conn() as c:
        c.execute("UPDATE media SET faces_scanned=1 WHERE id=?", (media_id,))


# ---------- OCR(사진 속 글자) ----------

def unscanned_ocr_ids():
    with conn() as c:
        return [
            r["id"]
            for r in c.execute(
                "SELECT id FROM media WHERE trashed_at IS NULL "
                "AND COALESCE(ocr_scanned, 0) = 0 AND type = 'image'"
            ).fetchall()
        ]


def set_ocr_text(media_id, text):
    with conn() as c:
        c.execute(
            "UPDATE media SET ocr_text=?, ocr_scanned=1 WHERE id=?",
            (text or None, media_id),
        )


def ocr_texts():
    """OCR 텍스트가 있는 활성 미디어의 (id, text) 목록."""
    with conn() as c:
        rows = c.execute(
            "SELECT id, ocr_text FROM media "
            "WHERE trashed_at IS NULL AND ocr_text IS NOT NULL AND ocr_text != ''"
        ).fetchall()
    return [(r["id"], r["ocr_text"]) for r in rows]


def caption_texts():
    """자동 캡션이 있는 활성 미디어의 (id, caption) 목록 — 단어 연관검색용."""
    with conn() as c:
        rows = c.execute(
            "SELECT id, caption FROM media "
            "WHERE trashed_at IS NULL AND caption IS NOT NULL AND caption != ''"
        ).fetchall()
    return [(r["id"], r["caption"]) for r in rows]


def album_name_media():
    """(media_id, album_name) 전체 목록 — 앨범명 연관검색용 (앨범 수는 소규모)."""
    with conn() as c:
        rows = c.execute(
            "SELECT ai.media_id, a.name FROM album_items ai "
            "JOIN albums a ON a.id = ai.album_id"
        ).fetchall()
    return [(r["media_id"], r["name"]) for r in rows]


def add_face(media_id, bbox, embedding):
    with conn() as c:
        cur = c.execute(
            "INSERT INTO faces (media_id, bbox, embedding) VALUES (?, ?, ?)",
            (media_id, bbox, embedding.astype(np.float32).tobytes()),
        )
        return cur.lastrowid


def unassigned_faces():
    with conn() as c:
        rows = c.execute(
            "SELECT id, embedding FROM faces WHERE person_id IS NULL ORDER BY id"
        ).fetchall()
    return [(r["id"], np.frombuffer(r["embedding"], dtype=np.float32)) for r in rows]


def faces_by_person():
    with conn() as c:
        rows = c.execute(
            "SELECT person_id, embedding FROM faces WHERE person_id IS NOT NULL"
        ).fetchall()
    out = {}
    for r in rows:
        out.setdefault(r["person_id"], []).append(
            np.frombuffer(r["embedding"], dtype=np.float32)
        )
    return out


def set_face_person(face_id, person_id):
    with conn() as c:
        c.execute("UPDATE faces SET person_id=? WHERE id=?", (person_id, face_id))


def create_person(cover_face_id=None):
    with conn() as c:
        cur = c.execute(
            "INSERT INTO persons (cover_face_id) VALUES (?)", (cover_face_id,)
        )
        return cur.lastrowid


def list_persons(min_faces=1):
    with conn() as c:
        rows = c.execute(
            """SELECT p.id, p.name, p.cover_face_id,
                      COUNT(DISTINCT f.media_id) n, MIN(f.id) first_face
               FROM persons p
               JOIN faces f ON f.person_id = p.id
               JOIN media m ON m.id = f.media_id AND m.trashed_at IS NULL
               GROUP BY p.id HAVING n >= ?
               ORDER BY (p.name IS NULL), n DESC""",
            (min_faces,),
        ).fetchall()
    return [
        {
            "id": r["id"], "name": r["name"], "count": r["n"],
            "cover_face_id": r["cover_face_id"] or r["first_face"],
        }
        for r in rows
    ]


def get_person(person_id):
    with conn() as c:
        r = c.execute("SELECT * FROM persons WHERE id=?", (person_id,)).fetchone()
    return dict(r) if r else None


def merge_persons(src_id, dst_id):
    """src 인물의 모든 얼굴을 dst로 옮기고 src 인물을 삭제. dst 유지."""
    if src_id == dst_id:
        return dst_id
    with conn() as c:
        c.execute("UPDATE faces SET person_id=? WHERE person_id=?", (dst_id, src_id))
        c.execute("DELETE FROM persons WHERE id=?", (src_id,))
    return dst_id


def rename_person(person_id, name):
    """인물 이름 변경. 같은 이름의 다른 인물이 이미 있으면 하나로 병합.

    병합 시 이름이 이미 붙어 있던 기존 인물(들)을 유지 대상으로 삼아
    이 인물의 얼굴을 그쪽으로 합친다. 반환: 최종 인물 id.
    """
    name = (name or "").strip() or None
    if name is None:
        with conn() as c:
            c.execute("UPDATE persons SET name=NULL WHERE id=?", (person_id,))
        return person_id

    with conn() as c:
        others = [
            r["id"]
            for r in c.execute(
                "SELECT id FROM persons WHERE name=? AND id<>? ORDER BY id",
                (name, person_id),
            ).fetchall()
        ]
    if not others:
        with conn() as c:
            c.execute("UPDATE persons SET name=? WHERE id=?", (name, person_id))
        return person_id

    # 같은 이름 인물이 이미 있음 → 가장 먼저 만들어진 인물로 모두 합친다
    dst = others[0]
    for src in others[1:]:
        merge_persons(src, dst)
    merge_persons(person_id, dst)
    with conn() as c:
        c.execute("UPDATE persons SET name=? WHERE id=?", (name, dst))
    return dst


def person_media_ids(person_id):
    with conn() as c:
        return [
            r["media_id"]
            for r in c.execute(
                "SELECT DISTINCT media_id FROM faces WHERE person_id=?", (person_id,)
            ).fetchall()
        ]


def match_person_name(text):
    """문장 속에 등장하는 인물 이름 매칭 (이름이 긴 순서로 우선)."""
    with conn() as c:
        rows = c.execute(
            "SELECT id, name FROM persons WHERE name IS NOT NULL"
        ).fetchall()
    best = None
    for r in rows:
        if r["name"] and r["name"] in text:
            if best is None or len(r["name"]) > len(best["name"]):
                best = {"id": r["id"], "name": r["name"]}
    return best
